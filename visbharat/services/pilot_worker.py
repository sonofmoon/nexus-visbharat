"""Leased, replay-aware processing; no provider work on dashboard reads."""
import base64
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from flask import current_app
from ..db import get_db
from ..audit import write_audit_log
from . import pilot


class ReviewRequired(Exception): pass


def provider_step(rid,step,inputs,call):
    db=get_db();sha=pilot.digest(inputs)
    db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    if rid.startswith('channel:'):
        prior=pilot.rows('SELECT step,input_sha256 FROM pilot_provider_steps WHERE request_id=?',(rid,))
        if any(row['step']!=step or row['input_sha256']!=sha for row in prior):
            db.rollback()
            raise ReviewRequired('Provider event ID was reused with changed input; use a new event ID')
    existing=pilot.rows('SELECT * FROM pilot_provider_steps WHERE request_id=? AND step=? AND input_sha256=?',(rid,step,sha))
    if existing:
        db.commit()
        if existing[0]['status']=='completed': return json.loads(existing[0]['result_json'])
        raise ReviewRequired('Previous provider outcome is uncertain; a reviewer must reconcile it before another call')
    db.execute('''INSERT INTO pilot_provider_steps(request_id,step,input_sha256,status,started_at)
        VALUES(?,?,?,'started',?)''',(rid,step,sha,pilot.now()));db.commit()
    began=time.perf_counter()
    try:
        result=call()
        if not isinstance(result,dict): raise ReviewRequired('Provider returned an invalid result')
        if result.get('fallback_used') or result.get('provider_mode') in ('simulation','local_fallback'):
            raise ReviewRequired('Provider returned a fallback; human review is required')
        db.execute('''UPDATE pilot_provider_steps SET status='completed',provider=?,model=?,result_json=?,
            latency_ms=?,usage_json=?,completed_at=? WHERE request_id=? AND step=? AND input_sha256=?''',
            (result.get('provider_mode','google_speech_live'),result.get('model'),json.dumps(result),
             round((time.perf_counter()-began)*1000,2),json.dumps(result.get('usage') or {'tokens':'not_reported'}),pilot.now(),rid,step,sha))
        db.commit();return result
    except Exception:
        db.rollback()
        db.execute("UPDATE pilot_provider_steps SET status='uncertain',completed_at=? WHERE request_id=? AND step=? AND input_sha256=?",
            (pilot.now(),rid,step,sha));db.commit()
        raise ReviewRequired('Provider result requires review; automatic rebilling has been stopped')


def claim(job_id=None):
    db=get_db();db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    stamp=pilot.now();args=[stamp,stamp]
    clause="((status='queued' AND next_attempt_at<=?) OR (status='running' AND lease_until<?))"
    if job_id: clause+=' AND job_id=?';args.append(job_id)
    jobs=pilot.rows('SELECT * FROM pilot_outbox WHERE '+clause+' ORDER BY created_at LIMIT 1',args)
    if not jobs: db.commit();return None
    job=jobs[0];lease=secrets.token_hex(16)
    until=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
    db.execute("UPDATE pilot_outbox SET status='running',attempts=attempts+1,lease_id=?,lease_until=?,updated_at=? WHERE job_id=?",
        (lease,until,stamp,job['job_id']));db.commit();job['lease_id']=lease;return job


def active_lease(job):
    return bool(pilot.rows("SELECT job_id FROM pilot_outbox WHERE job_id=? AND lease_id=? AND status='running'",(job['job_id'],job['lease_id'])))


def run_one(job_id=None):
    job=claim(job_id)
    if not job: return {'status':'idle'}
    db=get_db();rid=job['request_id']
    pr=pilot.rows('SELECT * FROM pilot_requests WHERE request_id=?',(rid,))[0]
    raw=pilot.rows('SELECT * FROM citizen_requests WHERE request_id=?',(rid,))[0]
    data=json.loads(pr['payload_json']);p=pilot.programme(pr['pilot_id'])
    metadata=json.loads(raw['ai_metadata_json']);result_status='manual_review';error=None
    if job['kind']=='analytics_sync':
        try:
            sync_analytics(raw,pr);state='completed';failure=None
        except Exception:
            state='review_required';failure='Analytics synchronization is unavailable; operational record retained'
        db.execute('UPDATE pilot_outbox SET status=?,last_error=?,updated_at=? WHERE job_id=? AND lease_id=? AND status=\'running\'',
            (state,failure,pilot.now(),job['job_id'],job['lease_id']));db.commit()
        return {'status':state,'kind':job['kind'],'request_id':rid}
    try:
        if pilot.rows("SELECT request_id FROM auditor_processing_restrictions WHERE request_id=? AND purpose='request_processing' AND state='restricted'",(rid,)):
            raise ReviewRequired('Processing is restricted by the current receipt')
        if data.get('audio_base64') and current_app.config.get('PILOT_AUDIO_BUCKET'):
            from .pilot_media import archive_audio
            data=archive_audio(pr)
        if not current_app.config.get('PILOT_MODEL_CALLS',False):
            raise ReviewRequired('Live model processing is not enabled; a reviewer can record the translation and classification')
        ai=current_app.extensions.get('google_ai_client')
        if not ai: raise ReviewRequired('Configured Google AI provider is unavailable')
        allowed=current_app.config.get('PILOT_MODEL_REGIONS',['asia-south1'])
        if not getattr(ai,'use_vertex',False) or getattr(ai,'api_key','') or getattr(ai,'vertex_openai_location','global') not in allowed:
            raise ReviewRequired('Model transport is outside the approved regional configuration')
        original=data['text']
        if data.get('audio_base64') or data.get('audio_object'):
            # The legacy Speech v1 client is global; require a separately configured regional adapter.
            stt=current_app.extensions.get('pilot_regional_stt')
            if not stt: raise ReviewRequired('A reviewed regional speech endpoint must be configured')
            from .pilot_media import read_audio
            audio=read_audio(data)
            import hashlib
            speech=provider_step(rid,'speech',{'sha256':hashlib.sha256(audio).hexdigest(),'language':data['language']},
                lambda:stt.transcribe_bytes(audio,{'ta':'ta-IN','te':'te-IN','en':'en-IN'}[data['language']],data['mime_type']))
            original=pilot.text(speech.get('transcript'),'transcript',1,4000);metadata['speech']=speech
        if not original: raise ReviewRequired('A transcript is needed before classification')
        translation={'translated_text':original,'provider_mode':'no_translation_required','model':None}
        from .pilot_portal import translate
        programme=pilot.programme(pr['pilot_id'])
        enabled_categories=programme['config'].get('categories',current_app.config['CATEGORIES'])
        if data['language']!='en':
            translation=provider_step(rid,'translation',{'text':original,'language':data['language']},lambda:translate(programme,ai,original,data['language']))
        category=provider_step(rid,'classification',{'text':original,'language':data['language']},
            lambda:ai.classify_request(original,data['language'],enabled_categories))
        if category.get('category') not in enabled_categories or category.get('urgency') not in ('Routine','Urgent','Emergency'):
            raise ReviewRequired('Classification labels need correction')
        translated=pilot.text(translation.get('translated_text'),'translated text',1,6000)
        confidence=category.get('confidence')
        result_status='ready' if isinstance(confidence,(int,float)) and not isinstance(confidence,bool) and .85<=confidence<=1 else 'manual_review'
        metadata.update(translation=translation,classification=category,provider_mode='google_ai_live',processing_status=result_status)
        db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
        if not active_lease(job): db.rollback();return {'status':'lease_lost'}
        if pilot.rows("SELECT request_id FROM auditor_processing_restrictions WHERE request_id=? AND purpose='request_processing' AND state='restricted'",(rid,)):
            db.rollback();raise ReviewRequired('Processing was restricted during inference; output is held for review')
        db.execute('UPDATE citizen_requests SET original_text=?,translated_text=?,category=?,urgency=?,sentiment=?,ai_metadata_json=? WHERE request_id=?',
            (original,translated,category['category'],category['urgency'],category.get('sentiment','Unknown'),json.dumps(metadata),rid))
    except ReviewRequired as exc:
        db.rollback();error=str(exc);result_status='manual_review'
    except Exception:
        db.rollback();error='Processing failed; inspect authorised diagnostics and review the report';result_status='manual_review'
        current_app.logger.warning('Pilot processing failed for a queued job')
    db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    if not active_lease(job): db.rollback();return {'status':'lease_lost'}
    metadata.update(processing_status=result_status,processing_note=error or 'Processing complete; human decisions remain required')
    db.execute('UPDATE citizen_requests SET ai_metadata_json=? WHERE request_id=?',(json.dumps(metadata),rid))
    db.execute('UPDATE pilot_requests SET processing_status=?,updated_at=?,version=version+1 WHERE request_id=?',(result_status,pilot.now(),rid))
    db.execute('UPDATE pilot_outbox SET status=?,last_error=?,updated_at=? WHERE job_id=? AND lease_id=?',
        ('review_required' if error else 'completed',error,pilot.now(),job['job_id'],job['lease_id']))
    write_audit_log('pilot_worker','pilot_processing_'+result_status,'citizen_request',rid,{'job_id':job['job_id'],'provider_mode':metadata.get('provider_mode')},commit=False)
    if not error:queue_analytics(rid)
    db.commit();return {'status':result_status,'request_id':rid,'notice':error}


def queue_analytics(rid):
    stamp=pilot.now()
    get_db().execute('''INSERT INTO pilot_outbox(job_id,request_id,kind,status,next_attempt_at,created_at,updated_at)
        VALUES(?,?,'analytics_sync','queued',?,?,?) ON CONFLICT(request_id,kind) DO UPDATE SET status='queued',
        next_attempt_at=excluded.next_attempt_at,updated_at=excluded.updated_at''',('JOB-'+secrets.token_hex(12),rid,stamp,stamp,stamp))


def sync_analytics(raw,pr):
    if not current_app.config.get('PILOT_ANALYTICS_SYNC',False): raise ReviewRequired('Analytics replication is not enabled')
    from google.cloud import bigquery
    table=current_app.config.get('PILOT_BIGQUERY_TABLE','')
    import re
    if not re.fullmatch(r'[a-z][a-z0-9-]+\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*',table): raise ReviewRequired('A dedicated pilot analytics table is required')
    client=current_app.extensions.get('pilot_bigquery_client') or bigquery.Client()
    # Latest version replaces previous output. Redacted analytical payload; no citizen text/audio.
    restricted=bool(pilot.rows("SELECT request_id FROM auditor_processing_restrictions WHERE request_id=? AND state='restricted'",(raw['request_id'],)))
    payload={k:raw[k] for k in ('request_id','state','district','category','urgency','status','created_at')}
    payload.update(pilot_id=pr['pilot_id'],record_version=pr['version'],restricted=restricted)
    params=[bigquery.ScalarQueryParameter('rid','STRING',raw['request_id']),bigquery.ScalarQueryParameter('version','INT64',pr['version']),bigquery.ScalarQueryParameter('payload','STRING',json.dumps(payload))]
    config=bigquery.QueryJobConfig(query_parameters=params,maximum_bytes_billed=current_app.config.get('PILOT_BQ_MAX_BYTES',1000000000))
    client.query(f'''MERGE `{table}` t USING (SELECT @rid request_id,@version record_version,@payload payload_json) s
        ON t.request_id=s.request_id WHEN MATCHED AND s.record_version>=t.record_version THEN UPDATE SET
        record_version=s.record_version,payload_json=s.payload_json,replicated_at=CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT(request_id,record_version,payload_json,replicated_at)
        VALUES(s.request_id,s.record_version,s.payload_json,CURRENT_TIMESTAMP())''',job_config=config,
        location=current_app.config.get('BIGQUERY_LOCATION','asia-south1'),timeout=20).result(timeout=30)
