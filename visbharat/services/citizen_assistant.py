"""Server-owned intake. CX supplies NLU; only saved database records issue receipts."""
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from uuid import uuid4
from flask import current_app, jsonify, request
from ..db import get_db
from .pii_scrubber import scrub_text

STRINGS = json.loads((Path(__file__).resolve().parents[2] / 'static/data/assistant_i18n.json').read_text(encoding='utf-8-sig'))
YES = {'yes', 'y', 'confirm', 'submit', 'yes submit my request', 'confirm and submit', 'ஆம்', 'ஆம் சமர்ப்பிக்கவும்', 'உறுதிசெய்து சமர்ப்பி', 'ஆமாம்', 'சரி', 'சமர்ப்பி', 'అవును', 'అవును సమర్పించండి', 'నిర్ధారించి సమర్పించండి', 'సరే', 'సమర్పించు'}
CANCEL = {'cancel', 'no', 'இல்லை', 'ரத்து', 'వద్దు', 'కాదు', 'రద్దు'}
TRACK = {'track', 'check status', 'check status of my request', 'கோரிக்கை நிலை', 'நிலை', 'స్థితి', 'అభ్యర్థన స్థితి'}
ALIASES = {'வேலூர்':'Vellore', 'వేలూరు':'Vellore', 'திருப்பதி':'Tirupati', 'తిరుపతి':'Tirupati', 'சென்னை':'Chennai', 'చెన్నై':'Chennai', 'கரூர்':'Karur', 'కరూర్':'Karur', 'హైదరాబాద్':'Hyderabad'}


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS assistant_sessions (
        session_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
        draft_json TEXT NOT NULL, state TEXT NOT NULL, language TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 0, expires_at BIGINT NOT NULL,
        busy_until BIGINT NOT NULL DEFAULT 0, lock_token TEXT,
        last_turn_id TEXT, last_turn_hash TEXT, response_json TEXT, request_id TEXT UNIQUE
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_assistant_expiry ON assistant_sessions(expires_at)')
    db.commit()


def configuration():
    repo = current_app.extensions['reference_repo']
    return jsonify(success=True, languages=['en','ta','te'],
                   districts={s:repo.list_districts(s) for s in repo.list_states()}, session_ttl_seconds=86400)


def receipt(request_id):
    row = get_db().execute('''SELECT request_id,status,district,state,ward,category,routed_department,sla_due_at,ai_metadata_json
        FROM citizen_requests WHERE request_id=?''', (request_id,)).fetchone()
    if not row:
        return None
    member = get_db().execute('SELECT cluster_id FROM cluster_members WHERE request_id=? LIMIT 1',(request_id,)).fetchone()
    out = {**dict(row), 'planning_url':'/demo?assistant_ticket='+row['request_id']}
    metadata=json.loads(out.pop('ai_metadata_json') or '{}')
    out['processing']={'classification_model':(metadata.get('classification') or {}).get('model'), 'translation_model':(metadata.get('translation') or {}).get('model')}
    out['cluster_id'] = member['cluster_id'] if member else None
    return out


def _district(text, proposed=''):
    available = current_app.extensions['reference_repo'].list_districts()
    for candidate in [proposed, text]:
        if not isinstance(candidate,str):
            continue
        clean = candidate.strip().casefold()
        matches = [d for d in available if re.search(r'(?<!\w)'+re.escape(d.casefold())+r'(?!\w)',clean)]
        matches += [name for alias,name in ALIASES.items() if alias in clean and name in available]
        if len(set(matches)) == 1:
            return matches[0]
    return ''


def _body(row, key, flow='not_checked', intent='', confidence=None):
    return {'session_id':row['session_id'], 'session_state':row['state'], 'language':row['language'],
            'version':row['version'], 'draft':json.loads(row['draft_json']),
            'next_prompt':STRINGS[row['language']][key], 'flow':flow, 'intent':intent or key,
            'confidence':confidence, 'channel':'nvb_assistant',
            'receipt':receipt(row['request_id']) if row['request_id'] else None}


def session_turn():
    started = time.perf_counter()
    data = request.get_json(silent=True)
    if not isinstance(data,dict):
        return jsonify(success=False,error='Expected a JSON object'),400
    language, action, message = data.get('language','en'), data.get('action','message'), data.get('message','')
    if not isinstance(language,str) or not isinstance(action,str) or language not in STRINGS or action not in {'start','resume','message','location','confirm','edit_issue','edit_location','cancel','track'}:
        return jsonify(success=False,error='Unsupported language or action'),400
    if not isinstance(message,str) or len(message)>4000:
        return jsonify(success=False,error='Message must be text, up to 4000 characters'),400
    message = message.strip()
    if action=='message' and not message:
        return jsonify(success=False,error='message is required'),400
    if 'consent_granted' in data and not isinstance(data['consent_granted'],bool):
        return jsonify(success=False,error='consent_granted must be a boolean'),400
    for field,limit in [('district',100),('ward',160)]:
        if field in data and (not isinstance(data[field],str) or len(data[field])>limit):
            return jsonify(success=False,error='Invalid location details'),400
    db, now = get_db(), int(time.time())
    sid, issued_token = data.get('session_id'), None
    if not sid:
        sid, issued_token = uuid4().hex, secrets.token_urlsafe(32)
        db.execute('DELETE FROM assistant_sessions WHERE expires_at<? AND busy_until<?',(now,now))
        db.execute('''INSERT INTO assistant_sessions(session_id,token_hash,draft_json,state,language,expires_at)
                      VALUES(?,?,?,'collect_issue',?,?)''',
                   (sid,hashlib.sha256(issued_token.encode()).hexdigest(),'{}',language,now+86400))
        db.commit()
    elif not isinstance(sid,str) or not re.fullmatch(r'[a-f0-9]{32}',sid):
        return jsonify(success=False,error='Invalid session'),400
    row = db.execute('SELECT * FROM assistant_sessions WHERE session_id=?',(sid,)).fetchone()
    token = issued_token or request.headers.get('X-NVB-Assistant-Token','')
    if not row or not hmac.compare_digest(row['token_hash'],hashlib.sha256(token.encode()).hexdigest()):
        return jsonify(success=False,error='Session unavailable',code='session_unavailable'),403
    row = dict(row)
    if row['expires_at']<now:
        return jsonify(success=False,error=STRINGS[language]['expired'],code='session_expired'),410
    turn_id = data.get('turn_id') or uuid4().hex
    if not isinstance(turn_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,64}',turn_id):
        return jsonify(success=False,error='Invalid turn ID'),400
    digest = hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if row['last_turn_id']==turn_id:
        if row['last_turn_hash']!=digest:
            return jsonify(success=False,error='Turn ID was used for different input'),409
        return jsonify(json.loads(row['response_json']))
    if 'version' in data and (type(data['version']) is not int or data['version']!=row['version']):
        return jsonify(success=False,error='Conversation changed; resume',code='stale_session'),409
    lock = uuid4().hex
    acquired = db.execute('''UPDATE assistant_sessions SET busy_until=?,lock_token=?
        WHERE session_id=? AND version=? AND busy_until<? RETURNING session_id''',
        (now+120,lock,sid,row['version'],now)).fetchone()
    db.commit()
    if not acquired:
        return jsonify(success=False,error='A turn is processing; retry shortly',code='session_busy'),409
    flow, intent, confidence, provider_error = 'not_checked','',None,None
    try:
        row['language'] = language
        draft, state = json.loads(row['draft_json']),row['state']
        if row['request_id']:
            state = 'complete'
        text = scrub_text(message).get('scrubbed') or message
        lower, params = text.casefold().strip().rstrip('.!?。'),{}
        if message and action=='message' and not row['request_id']:
            client = current_app.extensions.get('google_dialogflow_client')
            flow = 'local_guided_fallback'
            if client:
                try:
                    turn = client.detect_intent(sid,text,language,parameters={'nvb_stage':state,
                        'district':draft.get('district',''),'issue_description':draft.get('text','')})
                    flow,intent,confidence = 'dialogflow_cx_live',turn.get('intent',''),turn.get('confidence')
                    params = turn.get('parameters') or {}
                except Exception as err:
                    provider_error = type(err).__name__
                    current_app.logger.warning('Assistant CX unavailable: %s',provider_error)
            else:
                provider_error = 'not_configured'
        if action=='message' and confidence is not None and confidence >= 0.65:
            action = {'nvb_cancel':'cancel','nvb_edit_location':'edit_location','nvb_edit_issue':'edit_issue'}.get(intent,action)
        track_id = re.search(r'\bNVB-\d{8}[A-Fa-f0-9]{4}\b',text,re.I)
        tracked = None
        if action=='track' or lower in TRACK or track_id or intent in {'track_request','request_status','check status'}:
            candidate = track_id.group().upper() if track_id else row['request_id']
            tracked = receipt(candidate) if candidate else None
            key = 'tracked' if tracked else ('not_found' if candidate else 'track_prompt')
            if not candidate:
                draft['tracking_pending'] = True
            else:
                draft.pop('tracking_pending',None)
        elif draft.get('tracking_pending') and action=='message':
            key = 'not_found'
        elif action in {'start','resume'}:
            key = {'collect_issue':'issue','collect_location':'location','collect_confirmation':'review','complete':'complete','cancelled':'cancelled'}.get(state,'issue')
        elif row['request_id']:
            state,key = 'complete','complete'
        elif action=='cancel' or lower in CANCEL:
            draft,state,key = {},'cancelled','cancelled'
        elif action in {'edit_issue','edit_location'}:
            draft.pop('tracking_pending',None)
            state,key = ('collect_issue','issue') if action=='edit_issue' else ('collect_location','location')
        elif state=='cancelled':
            key = 'cancelled'
        elif state=='collect_confirmation':
            key = 'review'
            if action=='confirm' or lower in YES:
                if data.get('consent_granted') is not True:
                    key = 'consent_required'
                else:
                    from ..blueprints.api import _submit_complaint
                    payload = {**draft,'language':draft.get('input_language',language),
                        'source':'NVB Voice Assistant' if draft.get('voice_used') else 'NVB Text Assistant',
                        'consent_granted':True,'consent_scope':'request_processing'}
                    result = current_app.make_response(_submit_complaint(payload,assistant_session_id=sid))
                    saved = db.execute('SELECT request_id FROM assistant_sessions WHERE session_id=?',(sid,)).fetchone()
                    if saved and saved['request_id']:
                        row['request_id'],state,key = saved['request_id'],'complete','saved'
                    else:
                        key = 'failed'
                        provider_error = 'submission_unavailable' if result.status_code>=500 else 'submission_validation'
        elif state=='collect_issue':
            if len(text)<8 or lower in YES or action=='confirm':
                key = 'issue'
            else:
                draft['text'] = text
                draft['input_language'] = language
                draft['voice_used'] = bool(draft.get('voice_used') or data.get('input_mode')=='voice')
                district = _district(text,params.get('district',''))
                if district:
                    draft['district'] = district
                state,key = ('collect_confirmation','review') if draft.get('state') and draft.get('district') else ('collect_location','location')
        elif state=='collect_location':
            district = _district(text,data.get('district','') or params.get('district',''))
            landmark = data.get('ward','')
            if not isinstance(landmark,str) or len(landmark)>160:
                raise ValueError('Invalid landmark')
            if district:
                draft['district'] = district
                draft['ward'] = scrub_text(landmark).get('scrubbed') or landmark
                draft['state'] = str(current_app.extensions['reference_repo'].get_district_row(district)['state'])
                state,key = 'collect_confirmation','review'
            else:
                key = 'location_invalid'
        else:
            key = 'issue'
        row.update(state=state,draft_json=json.dumps(draft,ensure_ascii=False),version=row['version']+1)
        out = _body(row,key,flow,intent,confidence)
        if key in {'saved','complete'} and out['receipt']:
            out['next_prompt'] = STRINGS[language]['saved'].format(ticket=row['request_id'])
        if tracked:
            out['receipt'] = tracked
            out['next_prompt'] = STRINGS[language]['tracked'].format(ticket=tracked['request_id'],status=STRINGS[language].get('status_'+tracked['status'].lower().replace(' ','_'),tracked['status']))
        out.update(prompt_key=key,provider_error=provider_error,latency_ms=round((time.perf_counter()-started)*1000))
        body = {'success':True,'session':out}
        if issued_token:
            body['session_token'] = issued_token
        updated = db.execute('''UPDATE assistant_sessions SET draft_json=?,state=?,language=?,version=?,
            busy_until=0,lock_token=NULL,last_turn_id=?,last_turn_hash=?,response_json=?
            WHERE session_id=? AND lock_token=? RETURNING session_id''',
            (row['draft_json'],state,language,row['version'],turn_id,digest,json.dumps({'success':True,'session':out},ensure_ascii=False),sid,lock)).fetchone()
        db.commit()
        if not updated:
            return jsonify(success=False,error='Conversation changed; resume',code='stale_session'),409
        return jsonify(body)
    except Exception:
        db.rollback()
        saved = db.execute('SELECT request_id FROM assistant_sessions WHERE session_id=?',(sid,)).fetchone()
        if saved and saved['request_id']:
            row['request_id'],row['state'] = saved['request_id'],'complete'
            out = _body(row,'complete',flow,intent,confidence)
            out.update(next_prompt=STRINGS[language]['saved'].format(ticket=row['request_id']),prompt_key='saved',enrichment_pending=True)
            return jsonify(success=True,session=out)
        current_app.logger.exception('Assistant turn failed')
        return jsonify(success=False,error=STRINGS[language]['failed']),503
    finally:
        db.execute('UPDATE assistant_sessions SET busy_until=0,lock_token=NULL WHERE session_id=? AND lock_token=?',(sid,lock))
        db.commit()
