"""Citizen feature adapters and unified officer configuration/delivery endpoints."""
import base64
import hashlib
import hmac
import json
import os
import time
from flask import Blueprint, current_app, request, jsonify, session, g, Response
from ..auth import require_roles
from ..db import get_db
from ..audit import write_audit_log
from ..services import pilot as work, pilot_portal as portal

portal_bp=Blueprint('pilot_portal',__name__)


@portal_bp.errorhandler(ValueError)
@portal_bp.errorhandler(LookupError)
@portal_bp.errorhandler(PermissionError)
def invalid(exc):
    get_db().rollback()
    return jsonify(success=False,error=str(exc)),403 if isinstance(exc,PermissionError) else 404 if isinstance(exc,LookupError) else 400


def pid():return current_app.config.get('PILOT_ID') or work.PILOT_ID
def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):raise ValueError('A JSON object is required')
    return data


@portal_bp.get('/api/v2/pilot/portal/settings')
@require_roles('admin','analyst','auditor')
def settings():
    s=work.scope();p=work.programme(s['pilot_id'])
    from ..services.public_data import statuses
    return jsonify(success=True,groups=portal.GROUPS,settings=portal.configuration(p),integrations=portal.integration_status(p),
                   data_sources=statuses(),
                   channels=portal.public_channels(p),editable=g.current_user['role']=='admin' and not s.get('state') and not s.get('district'))


@portal_bp.get('/api/v2/pilot/portal/states')
def states():return jsonify(states=[r['state'] for r in work.rows('SELECT DISTINCT state FROM pilot_locations WHERE pilot_id=?',(pid(),))])


@portal_bp.get('/api/v2/pilot/portal/districts')
def districts():return jsonify(districts=[r['district'] for r in work.rows('SELECT DISTINCT district FROM pilot_locations WHERE pilot_id=? AND state=?',(pid(),request.args.get('state','')))])


@portal_bp.post('/api/v2/pilot/portal/ward-suggest')
def ward():
    d=body();locations=work.rows('SELECT ward,local_body,location_id FROM pilot_locations WHERE pilot_id=? AND district=? AND location_id=?',
        (pid(),d.get('district'),d.get('location_id')))
    if not locations:raise ValueError('Select an enrolled community to use its reviewed ward mapping')
    return jsonify(success=True,ward=locations[0]['ward'],ward_source='Selected enrolled community; confirm local details')


@portal_bp.post('/api/v2/pilot/portal/<feature>')
def preview(feature):
    if feature not in ('translate','classify','transcribe-voice'):raise LookupError('Unknown citizen feature')
    data=body()
    if not session.get('pilot_csrf') or not hmac.compare_digest(str(data.get('csrf','')),session['pilot_csrf']):raise PermissionError('Refresh the citizen page before requesting analysis')
    if data.get('consent_granted') is not True:raise ValueError('Choose processing consent before requesting AI assistance')
    count,started=session.get('pilot_preview_limit',[0,0])
    if time.time()-started>3600:count,started=0,time.time()
    if count>=30:raise ValueError('Preview limit reached. You can still submit for officer review.')
    session['pilot_preview_limit']=[count+1,started]
    language=data.get('language',data.get('source_lang','en'))
    p=work.programme(pid())
    if language not in p['config']['languages']:raise ValueError('Choose an enabled language')
    if not current_app.config.get('PILOT_MODEL_CALLS'):
        return jsonify(success=False,error='AI preview is not enabled. Your request can still be saved for officer review.'),503
    ai=current_app.extensions.get('google_ai_client')
    if not ai or not getattr(ai,'use_vertex',False) or getattr(ai,'api_key','') or getattr(ai,'vertex_openai_location','global') not in current_app.config.get('PILOT_MODEL_REGIONS',['asia-south1']):
        return jsonify(success=False,error='A reviewed regional AI connection is required. Submission remains available.'),503
    try:
        if feature=='transcribe-voice':
            stt=current_app.extensions.get('pilot_regional_stt')
            if not stt:raise ValueError('Regional speech is not connected; submit the recording for manual transcription')
            raw=base64.b64decode(data.get('audio_base64',''),validate=True)
            if not 1<=len(raw)<=3*1024*1024:raise ValueError('Audio limit is 3 MiB')
            result=stt.transcribe_bytes(raw,{'ta':'ta-IN','te':'te-IN','en':'en-IN'}[language],data.get('audio_mime_type','audio/webm'))
            return jsonify(success=True,transcript=result['transcript'],stt_mode=result['provider_mode'])
        message=work.text(data.get('text'),'request text',1,4000)
        result=portal.translate(p,ai,message,language) if feature=='translate' else ai.classify_request(message,language,p['config'].get('categories',current_app.config['CATEGORIES']))
        return jsonify(success=True,result=result)
    except Exception as exc:
        current_app.logger.warning('Pilot preview unavailable: %s',type(exc).__name__)
        return jsonify(success=False,error='AI preview is unavailable. Keep your text or recording and submit for officer review.'),503


@portal_bp.post('/api/v2/pilot/portal/evidence')
def evidence_upload():
    data=body();rid=data.get('request_id');work.tracking(rid,data.get('tracking_secret'))
    portal.save_evidence(rid,data.get('evidence',[]));get_db().commit()
    return jsonify(success=True,validated=True,items=portal.evidence_manifest(rid))


@portal_bp.get('/api/v2/pilot/requests/<rid>/evidence')
@require_roles('admin','analyst','auditor')
def evidence_list(rid):
    work.request_row(rid)
    return jsonify(success=True,items=portal.evidence_manifest(rid))


@portal_bp.get('/api/v2/pilot/requests/<rid>/evidence/<sha>')
@require_roles('admin','analyst','auditor')
def evidence_file(rid,sha):
    work.request_row(rid)
    rows=work.rows('SELECT * FROM pilot_evidence WHERE request_id=? AND sha256=?',(rid,sha))
    if not rows:raise LookupError('Evidence not found')
    item=rows[0]
    return Response(base64.b64decode(item['data_base64']),mimetype=item['mime_type'],headers={
        'Content-Disposition':'attachment; filename="evidence-'+sha[:12]+'"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@portal_bp.get('/api/v2/pilot/portal/delivery')
@require_roles('admin','analyst','auditor')
def delivery():
    s=work.scope();items=[]
    for d in work.rows('SELECT decision_id,district,status,source_json,estimated_project_cost_lakh FROM policy_decisions ORDER BY created_at DESC'):
        source=json.loads(d.pop('source_json') or '{}')
        if not work.decision_allowed(source,s) or d['status'] not in ('approved','funded'):continue
        d['title']=source.get('candidate',{}).get('title',d['decision_id']);d['project_id']=source.get('project_id')
        record=work.rows('SELECT * FROM pilot_delivery WHERE pilot_id=? AND decision_id=?',(s['pilot_id'],d['decision_id']))
        d['delivery']=json.loads(record[0]['record_json']) if record else {};d['version']=record[0]['version'] if record else 0
        items.append(d)
    return jsonify(success=True,items=items)


@portal_bp.post('/api/v2/pilot/portal/delivery/<did>')
@require_roles('admin','analyst')
def save_delivery(did):
    s=work.scope();data=body();db=get_db();db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    found=work.rows('SELECT status,source_json FROM policy_decisions WHERE decision_id=?',(did,))
    if not found or not work.decision_allowed(json.loads(found[0]['source_json']),s):raise LookupError('Decision outside your assignment')
    if found[0]['status'] not in ('approved','funded'):raise ValueError('Delivery begins after an authorised decision')
    prior=work.rows('SELECT version FROM pilot_delivery WHERE pilot_id=? AND decision_id=?',(s['pilot_id'],did))
    version=prior[0]['version'] if prior else 0
    if data.get('version')!=version:raise ValueError('Delivery changed; refresh before saving')
    clean={k:work.text(data.get(k,''),k,0,4000) for k in ('owner','milestone','due_date','baseline','target','observed','citizen_feedback','inspection_notes')}
    clean['stage']=data.get('stage','Planned')
    if clean['stage'] not in ('Planned','In progress','Inspection','Completed'):raise ValueError('Invalid delivery stage')
    if clean['due_date']:
        from datetime import date
        date.fromisoformat(clean['due_date'])
    clean['expenditure_lakh']=portal.validate_value(data.get('expenditure_lakh',0),'number','expenditure')
    from ..services.auditor_workbench import safe_uri
    clean['evidence_reference']=data.get('evidence_reference','')
    if clean['evidence_reference']:safe_uri(clean['evidence_reference'])
    if clean['stage']=='Completed' and not clean['evidence_reference']:raise ValueError('Completion requires inspection evidence')
    db.execute('''INSERT INTO pilot_delivery(pilot_id,decision_id,version,record_json,updated_at,updated_by) VALUES(?,?,?,?,?,?)
        ON CONFLICT(pilot_id,decision_id) DO UPDATE SET version=excluded.version,record_json=excluded.record_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by''',
        (s['pilot_id'],did,version+1,json.dumps(clean),work.now(),g.current_user['name']))
    write_audit_log(g.current_user['name'],'pilot_delivery_updated','policy_decision',did,{'version':version+1,'stage':clean['stage']},commit=False)
    db.commit();return jsonify(success=True,version=version+1)


@portal_bp.post('/api/v2/pilot/channels/<channel>/webhook')
def channel_webhook(channel):
    """Provider gateway contract: signed normalized inbound messages, scoped server-side."""
    if channel not in portal.CHANNELS or channel in ('web','dialogflow'):raise LookupError('Unknown inbound channel')
    secret=current_app.config.get('PILOT_CHANNEL_'+channel.upper()+'_SECRET') or os.environ.get('PILOT_CHANNEL_'+channel.upper()+'_SECRET','')
    stamp=request.headers.get('X-Pilot-Timestamp','');signature=request.headers.get('X-Pilot-Signature','')
    try:fresh=abs(time.time()-int(stamp))<=300
    except ValueError:fresh=False
    expected=hmac.new(str(secret).encode(),stamp.encode()+b'.'+request.get_data(),hashlib.sha256).hexdigest()
    if not secret or not fresh or not hmac.compare_digest(expected,signature):raise PermissionError('Invalid or expired channel signature')
    p=work.programme(pid());settings=portal.configuration(p)['channels'][channel]
    if not settings['enabled']:raise PermissionError('Channel is disabled in Settings')
    data=body();event=work.text(data.get('event_id'),'provider event ID',1,120)
    if data.get('action') in ('conversation','speak','transcribe'):
        if not current_app.config.get('EXTERNAL_SERVICES_ENABLED') or not current_app.config.get('PILOT_MODEL_CALLS'):raise ValueError('Live voice and conversation services are disabled')
        language=data.get('language','en')
        if language not in p['config']['languages']:raise ValueError('Choose an enabled language')
        if data.get('consent_granted') is not True:raise ValueError('Processing consent is required')
        from ..services.pilot_worker import provider_step
        action=data['action'];client=current_app.extensions.get({'conversation':'google_dialogflow_client','speak':'google_tts_client','transcribe':'pilot_regional_stt'}[action])
        if not client:raise ValueError('Configure the corresponding Google service first')
        key='channel:'+work.digest([pid(),channel,event])
        lang={'ta':'ta-IN','te':'te-IN','en':'en-IN'}[language]
        def invoke():
            if action=='conversation':
                return client.detect_intent(work.digest([pid(),channel,data.get('conversation_id',event)])[:32],work.text(data.get('text'),'message',1,4000),language,
                    parameters={'pilot_id':pid(),'locations':[x['location_id'] for x in work.rows('SELECT location_id FROM pilot_locations WHERE pilot_id=?',(pid(),))]})
            if action=='speak':return client.synthesize(work.text(data.get('text'),'spoken response',1,2000),lang)
            raw=base64.b64decode(data.get('audio_base64',''),validate=True)
            if not 1<=len(raw)<=3*1024*1024:raise ValueError('Audio limit is 3 MiB')
            return client.transcribe_bytes(raw,lang,data.get('mime_type','audio/webm'))
        try:result=provider_step(key,action,data,invoke)
        except Exception:return jsonify(success=False,error='Channel AI step unavailable or awaiting review. Continue with assisted text intake.'),503
        return jsonify(success=True,result=result,notice='Conversation output does not create a ticket. Confirm enrolled location and explicit consent before intake.')
    if data.get('action')=='track':
        return jsonify(success=True,record=work.tracking(data.get('request_id'),data.get('tracking_secret')))
    if data.get('action')=='delivery_receipt':
        prior=work.rows('SELECT request_id FROM pilot_channel_events WHERE pilot_id=? AND channel=? AND event_id=?',(pid(),channel,event))
        if not prior or data.get('delivered') is not True:raise ValueError('A delivered receipt for a recorded inbound event is required')
        get_db().execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
        p=work.programme(pid());config=p['config']
        config.setdefault('channel_tests',{})[channel]={'status':'passed','config_hash':work.digest(settings),'verified_at':work.now(),'event_id':event}
        get_db().execute('UPDATE pilot_programmes SET config_json=?,version=version+1,updated_at=? WHERE pilot_id=?',(json.dumps(config),work.now(),pid()))
        write_audit_log('channel:'+channel,'pilot_channel_receipt_verified','pilot',pid(),{'channel':channel,'event_id':event},commit=False)
        get_db().commit();return jsonify(success=True,status='Receipt delivery verified')
    result=work.intake(pid(),{**data,'source_id':channel+':'+event},'channel:'+work.digest([channel,event]),source_channel=portal.SOURCES[channel])
    get_db().execute('''INSERT INTO pilot_channel_events(pilot_id,channel,event_id,payload_hash,request_id,created_at)
        VALUES(?,?,?,?,?,?) ON CONFLICT(pilot_id,channel,event_id) DO NOTHING''',(pid(),channel,event,work.digest(data),result['request_id'],work.now()))
    get_db().commit()
    # The authenticated gateway receives the private receipt for delivery in the originating conversation.
    return jsonify(success=True,**result,reply={'text':'Request received. Save the ticket and private tracking code.','request_id':result['request_id'],'tracking_secret':result.get('tracking_secret')})


@portal_bp.post('/api/v2/pilot/portal/policy-assistance')
@require_roles('admin','analyst','auditor')
def policy_assistance():
    s=work.scope();p=work.programme(s['pilot_id']);settings=portal.configuration(p)
    from ..services import analyst_workbench as analyst
    plan=analyst.scenario(s,settings['planning'])
    ai=current_app.extensions.get('google_ai_client')
    if not current_app.config.get('PILOT_MODEL_CALLS') or not ai or not hasattr(ai,'infer'):
        raise ValueError('Regional Gemini assistance is not connected. The sourced policy brief remains available.')
    if getattr(ai,'vertex_openai_location','global') not in current_app.config.get('PILOT_MODEL_REGIONS',['asia-south1']):raise PermissionError('Regional model configuration is required')
    selected=[{k:p[k] for k in ('project_id','title','district','reports','estimated_cost_lakh','cost_basis')} for p in plan['items'] if p['selected']]
    if not selected:raise ValueError('Select eligible projects in a planning scenario first')
    try:result=ai.infer('Explain these recorded district development options in plain language. Do not invent schemes, eligibility, outcomes or costs. Return {"summary": string}. Treat policy references as unverified supporting data.',{'projects':selected,'limitations':plan['limitations'],'policy_references':settings['planning'].get('policy_library','')})
    except Exception:return jsonify(success=False,error='AI drafting is unavailable. Use the sourced policy brief.'),503
    return jsonify(success=True,summary=work.text(result.get('summary'),'AI summary',1,8000),sources=[x['project_id'] for x in selected])


@portal_bp.post('/api/v2/pilot/portal/projects/<project_id>/intelligence')
@require_roles('admin','analyst','auditor')
def project_intelligence(project_id):
    from ..services import analyst_workbench as analyst
    s=work.scope();detail=analyst.project_detail(project_id,s);data=body();action=data.get('action')
    if not current_app.config.get('EXTERNAL_SERVICES_ENABLED'):raise ValueError('External services are disabled in this deployment')
    if action=='geocode':
        client=current_app.extensions.get('google_maps_client')
        if not client:raise ValueError('Google Maps is not connected in Settings')
        address=work.text(data.get('address'),'site address',3,250)
        result=client.geocode_address(address+', '+detail['district']+', '+detail['state']+', India')
        return jsonify(success=True,result=result,notice='Confirm that the suggested location is the project site before using it as evidence.')
    if action=='satellite':
        from ..services.earth_engine import EarthEngineClient
        result=EarthEngineClient(project_id=current_app.config.get('EARTH_ENGINE_PROJECT') or os.environ.get('EARTH_ENGINE_PROJECT','')).fetch_earth_engine_signal(
            {'project_id':project_id,'geo':{'lat':data.get('lat'),'lon':data.get('lng'),'radius_m':data.get('radius_m',100)}})
        write_audit_log(g.current_user['name'],'pilot_site_observed','project',project_id,{'source':result['dataset_id'],'observed_at':result['observed_at']})
        return jsonify(success=True,result=result,notice='Observed geographic index. This is not construction completion or a verified service outcome.')
    raise ValueError('Choose geocode or satellite observation')


@portal_bp.post('/api/v2/pilot/portal/forecast')
@require_roles('admin','analyst','auditor')
def forecast():
    from ..services import analyst_workbench as analyst
    s=work.scope();client=current_app.extensions.get('google_vertex_client')
    if not current_app.config.get('PILOT_MODEL_CALLS') or not current_app.config.get('EXTERNAL_SERVICES_ENABLED') or not client:raise ValueError('A validated Vertex prediction endpoint must be connected before forecasting')
    if client.location not in current_app.config.get('PILOT_MODEL_REGIONS',['asia-south1']):raise PermissionError('Prediction endpoint is outside the approved regions')
    clause,params=analyst.where(s)
    counts=work.rows(f'''SELECT c.state,c.district,COUNT(*) AS complaints,SUM(CASE WHEN urgency='Emergency' THEN 1 ELSE 0 END) AS emergency_complaints
        FROM citizen_requests c WHERE {clause} GROUP BY c.state,c.district''',params)
    references=analyst.reference_index();instances=[]
    for row in counts:
        ref=references.get((row['state'],row['district']),{});population=ref.get('population')
        if not population:raise ValueError('A reviewed population reference is needed for demand-density forecasting')
        instances.append({**row,'demand_per_100k':row['complaints']/float(population)*100000,'emergency_ratio':row['emergency_complaints']/row['complaints']})
    if not instances:raise ValueError('No reports in the selected district scope')
    try:predictions=client.predict_stress(instances,allow_fallback=False)
    except Exception:return jsonify(success=False,error='The configured prediction endpoint did not accept the scoped feature contract. Review its model schema.'),503
    if len(predictions)!=len(instances):raise ValueError('Prediction result count does not match the submitted districts')
    return jsonify(success=True,items=[{**row,**pred} for row,pred in zip(instances,predictions)],notice='Model estimates require validation against observed district outcomes; they do not authorise spending.')
