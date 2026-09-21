"""Programme scope and durable citizen intake. SQL remains the source of truth."""
import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from flask import current_app, g, has_request_context, request
from ..db import get_db
from ..audit import write_audit_log

PILOT_ID = 'vellore-tirupati-water'
GATES = ('agency_participation','processing_basis','district_boundaries','source_releases',
         'officer_identity','language_evaluation','access_review','restore_drill','regional_recovery','cloud_cost_review')


def now(): return datetime.now(timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def rows(sql,args=()): return [dict(r) for r in get_db().execute(sql,args).fetchall()]
def text(value,name,minimum=1,maximum=2000):
    if not isinstance(value,str) or not minimum<=len(value.strip())<=maximum:
        raise ValueError(f'{name} must contain {minimum}–{maximum} characters')
    return value.strip()


def programme(pid):
    found=rows('SELECT * FROM pilot_programmes WHERE pilot_id=?',(pid,))
    if not found: raise LookupError('Pilot programme not found')
    p=found[0];p['config']=json.loads(p.pop('config_json'));return p


def user_memberships():
    user=getattr(g,'current_user',None)
    if not user: return []
    m = rows('SELECT * FROM pilot_memberships WHERE user_id=? AND active=1',(user['id'],))
    if not m and current_app.config.get('DEMO_MODE'):
        state = 'Tamil Nadu' if 'Vellore' in user.get('name','') else 'Andhra Pradesh' if 'Tirupati' in user.get('name','') else ''
        district = 'Vellore' if 'Vellore' in user.get('name','') else 'Tirupati' if 'Tirupati' in user.get('name','') else ''
        get_db().execute('INSERT OR IGNORE INTO pilot_memberships(pilot_id,user_id,state,district,active) VALUES(?,?,?,?,1)',(PILOT_ID,user['id'],state,district))
        get_db().commit()
        m = rows('SELECT * FROM pilot_memberships WHERE user_id=? AND active=1',(user['id'],))
    return m


def resolve_scope(scope, args):
    pid=str(args.get('pilot_id') or current_app.config.get('PILOT_ID') or '').strip()
    user=getattr(g,'current_user',None)
    if not user and has_request_context():
        from .pilot_identity import session_user
        user = session_user()
        if not user:
            from ..auth import _extract_token, hash_api_token
            token = _extract_token(request.headers.get('Authorization', ''))
            if token:
                db = get_db()
                token_hash = hash_api_token(token)
                row = db.execute('SELECT id, name, role FROM users WHERE api_token_hash = ? OR api_token = ?', (token_hash, token)).fetchone()
                if row:
                    user = {'id': row['id'], 'name': row['name'], 'role': row['role']}
                    g.current_user = user
        else:
            g.current_user = user
    if not user and current_app.config.get('DEMO_MODE') and pid:
        user = {'id': 2, 'name': 'Policy Analyst', 'role': 'analyst'}
        g.current_user = user
    memberships=user_memberships()
    if current_app.config.get('PILOT_ONLY') and user and not memberships:
        raise PermissionError('An active programme assignment is required')
    is_pilot_route = has_request_context() and (request.path.startswith('/api/v2/pilot') or request.path.startswith('/pilot'))
    if not pid and (current_app.config.get('PILOT_ONLY') or is_pilot_route) and memberships:
        if len(memberships)!=1: raise PermissionError('Select an assigned pilot programme')
        pid=memberships[0]['pilot_id']
    if not pid: return scope
    if not user: raise PermissionError('Sign in to inspect pilot records')
    p=programme(pid)
    member=next((m for m in memberships if m['pilot_id']==pid),None)
    if not member and not (user['role']=='admin' and not memberships):
        if current_app.config.get('DEMO_MODE'):
            get_db().execute('INSERT OR IGNORE INTO pilot_memberships(pilot_id,user_id,state,district,active) VALUES(?,?,?,?,1)',(pid,user['id'],'',''))
            get_db().commit()
            member={'pilot_id':pid,'user_id':user['id'],'state':'','district':'','active':1}
        else:
            raise PermissionError('Pilot is outside this account assignment')
    scope['pilot_id']=pid
    for field in ('state','district'):
        if member and member[field]:
            if scope.get(field) and scope[field]!=member[field]: raise PermissionError('Geography is outside this account assignment')
            scope[field]=member[field]
    return scope


def scope(args=None):
    from .analyst_workbench import scope_from
    return scope_from(args if args is not None else request.args)


def citizen_clause(scope,alias='c'):
    if not scope.get('pilot_id'): return '',[]
    return f' AND EXISTS(SELECT 1 FROM pilot_requests pr WHERE pr.request_id={alias}.request_id AND pr.pilot_id=?)',[scope['pilot_id']]


def decision_allowed(source,s):
    if not s.get('pilot_id'): return True
    c=source.get('candidate',{}); saved=source.get('scope',{})
    return saved.get('pilot_id')==s['pilot_id'] and all(not s.get(k) or s[k]==c.get(k) for k in ('state','district','ward','category'))


def authorize_decision(source):
    args=dict(request.args)
    if has_request_context() and request.is_json:
        body=request.get_json(silent=True)
        if isinstance(body,dict) and body.get('pilot_id'): args['pilot_id']=body['pilot_id']
    s=scope(args)
    if not decision_allowed(source,s): raise PermissionError('Decision is outside this pilot assignment')


def request_row(rid,s=None):
    from .analyst_workbench import where
    s=s or scope();clause,params=where(s)
    found=rows(f'''SELECT c.*,p.pilot_id,p.location_id,p.processing_status,p.assigned_to,p.version,
        p.payload_json,p.tracking_hash FROM citizen_requests c JOIN pilot_requests p ON p.request_id=c.request_id
        WHERE c.request_id=? AND {clause}''',[rid,*params])
    if not found: raise LookupError('Request not found in this assignment')
    return found[0]


def create_default():
    if rows('SELECT pilot_id FROM pilot_programmes WHERE pilot_id=?',(PILOT_ID,)): return
    stamp=now();config={'languages':['ta','te','en'],'category':'Water Supply','monthly_report_capacity':10000,
        'monthly_cloud_budget_inr':150000,'start_date':None,'review_days':2,
        'notice_version':'pilot-demo-v1','live_intake_enabled':False,
        'routing':{'Vellore':'Vellore water services review team (demo)','Tirupati':'Tirupati water services review team (demo)'},
        'notice':'Synthetic rehearsal. Communities, agency participation and historical boundary joins require review.'}
    get_db().execute('INSERT INTO pilot_programmes VALUES(?,?,?,?,?,1,?,?)',
        (PILOT_ID,'Vellore–Tirupati Water Services Pilot','rehearsal','synthetic',json.dumps(config),stamp,stamp))
    for district,state in [('Vellore','Tamil Nadu'),('Tirupati','Andhra Pradesh')]:
        for i,kind in enumerate(['urban_ward','rural_panchayat','rural_panchayat'],1):
            lid=f'{district.lower()}-{i}';ward=f'Demo catchment {i}'
            get_db().execute('''INSERT INTO pilot_locations(location_id,pilot_id,state,district,local_body,ward,
                location_kind,verification_status) VALUES(?,?,?,?,?,?,?,'proposed')''',
                (lid,PILOT_ID,state,district,f'Proposed {kind.replace("_"," ")} {i}',ward,kind))
    for gate in GATES:
        get_db().execute('INSERT INTO pilot_checks(pilot_id,check_key,status,notes) VALUES(?,?,?,?)',
            (PILOT_ID,gate,'pending','External evidence has not been recorded'))
    get_db().commit()


def settings_update(pid,data):
    get_db().execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    p=programme(pid)
    if data.get('version')!=p['version']: raise ValueError('Settings changed; refresh before saving')
    config=p['config']
    if 'translation_provider' in data:
        if data['translation_provider'] not in ('vertex','cloud_translation'):raise ValueError('Choose Vertex or Cloud Translation')
        config['translation_provider']=data['translation_provider']
    if 'portal' in data:
        from .pilot_portal import validate_settings
        config['portal']=validate_settings(p,data['portal'])
        config['review_days']=config['portal']['workflow']['review_days']
    if 'categories' in data:
        categories=data['categories']
        if not isinstance(categories,list) or not categories or any(c not in current_app.config['CATEGORIES'] for c in categories):raise ValueError('Select valid service categories')
        config['categories']=list(dict.fromkeys(categories))
    if 'languages' in data:
        if not isinstance(data['languages'],list) or not data['languages'] or any(l not in ('ta','te','en') for l in data['languages']):raise ValueError('Select Tamil, Telugu or English')
        config['languages']=list(dict.fromkeys(data['languages']))
    if 'title' in data:
        get_db().execute('UPDATE pilot_programmes SET title=? WHERE pilot_id=?',(text(data['title'],'programme title',3,180),pid))
    for key in ('monthly_report_capacity','monthly_cloud_budget_inr','review_days'):
        if key in data:
            n=data[key]
            if isinstance(n,bool) or not isinstance(n,int) or not 1<=n<=10000000: raise ValueError(f'Invalid {key}')
            config[key]=n
    if 'routing' in data:
        for district in ('Vellore','Tirupati'):
            config['routing'][district]=text(data['routing'].get(district),'review department',3,150)
    if 'start_date' in data:
        value=data['start_date']
        if value: datetime.strptime(value,'%Y-%m-%d')
        config['start_date']=value or None
    # Moving from rehearsal to real intake requires provisioning an operational programme.
    # A settings click cannot relabel synthetic records as real or certify launch gates.
    get_db().execute('UPDATE pilot_programmes SET config_json=?,version=version+1,updated_at=? WHERE pilot_id=? AND version=?',
        (json.dumps(config),now(),pid,p['version']))
    write_audit_log(g.current_user['name'],'pilot_settings_updated','pilot',pid,{'version':p['version']+1},commit=False)
    get_db().commit();return programme(pid)


def intake(pid,data,key,source_channel=None):
    p=programme(pid);config=p['config'];key=text(key,'Idempotency-Key',12,150)
    if p['status'] not in ('rehearsal','active'): raise ValueError('Intake is not open')
    if p['data_mode']!='synthetic' and not config.get('live_intake_enabled'): raise PermissionError('Operational intake is not enabled')
    language=data.get('language')
    if language not in config['languages']: raise ValueError('Choose an enabled pilot language')
    locations=rows('SELECT * FROM pilot_locations WHERE pilot_id=? AND location_id=?',(pid,data.get('location_id')))
    if not locations: raise ValueError('Choose an enrolled location')
    loc=locations[0]
    if p['data_mode']!='synthetic' and loc['verification_status']!='reviewed': raise ValueError('Location boundary review is pending')
    if data.get('consent_granted') is not True: raise ValueError('An explicit processing agreement is required for this opt-in intake')
    message=data.get('text','').strip() if isinstance(data.get('text',''),str) else ''
    if len(message)>4000:raise ValueError('Report text exceeds 4,000 characters')
    audio=data.get('audio_base64','')
    if audio:
        if not isinstance(audio,str) or len(audio)>4*1024*1024: raise ValueError('Audio exceeds the pilot upload limit')
        try: content=base64.b64decode(audio,validate=True)
        except Exception: raise ValueError('Invalid audio encoding')
        if not 1<=len(content)<=3*1024*1024: raise ValueError('Audio exceeds 3 MiB')
        if data.get('mime_type') not in ('audio/webm','audio/ogg','audio/wav','audio/mpeg','audio/webm;codecs=opus'):
            raise ValueError('Unsupported audio type')
    elif not 10<=len(message)<=4000: raise ValueError('Describe the request in 10–4,000 characters')
    clean={'text':message,'language':language,'location_id':loc['location_id'],'consent_granted':True,
        'audio_base64':audio,'mime_type':data.get('mime_type',''),'source_id':data.get('source_id')}
    from .pilot_portal import decode_evidence, save_evidence, SOURCES
    evidence=decode_evidence(data.get('evidence',[]))
    if source_channel and source_channel not in SOURCES.values():raise ValueError('Unknown source channel')
    if source_channel:clean['source_channel']=source_channel
    if evidence:clean['evidence_manifest']=[{k:v for k,v in item.items() if k!='data'} for item in evidence]
    if data.get('category'):
        if data['category'] not in config.get('categories',current_app.config['CATEGORIES']):raise ValueError('Choose an enabled service category')
        clean['citizen_category']=data['category']
    if data.get('translated_text'):clean['citizen_translation']=text(data['translated_text'],'citizen translation',0,6000)
    if data.get('tracking_secret'):clean['receipt_secret_hash']=digest(text(data['tracking_secret'],'private receipt code',32,128))
    if data.get('ward'):clean['citizen_ward']=text(data['ward'],'ward / landmark',0,200)
    if data.get('location'):
        import math
        coordinates=data['location']
        if not isinstance(coordinates,dict):raise ValueError('Invalid location')
        for k,limit in [('lat',90),('lng',180)]:
            v=coordinates.get(k)
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or abs(v)>limit:raise ValueError('Invalid coordinates')
        clean['location']={k:coordinates[k] for k in ('lat','lng')}
    fingerprint=digest(clean);db=get_db()
    db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    prior=rows('SELECT request_id,payload_sha256 FROM pilot_requests WHERE pilot_id=? AND idempotency_key=?',(pid,key))
    if prior:
        db.rollback()
        if prior[0]['payload_sha256']!=fingerprint: raise ValueError('Idempotency key was used for different content')
        return {'request_id':prior[0]['request_id'],'reused':True,'processing_status':'accepted','tracking_secret':data.get('tracking_secret')}
    if clean['source_id']:
        clean['source_id']=text(clean['source_id'],'source ID',1,150)
        prior=rows('SELECT request_id FROM pilot_requests WHERE pilot_id=? AND source_id=?',(pid,clean['source_id']))
        if prior: db.rollback();return {'request_id':prior[0]['request_id'],'reused':True,'tracking_secret':None}
    stamp=now();month=stamp[:7]
    if rows('SELECT COUNT(*) AS n FROM pilot_requests WHERE pilot_id=? AND created_at>=?',(pid,month))[0]['n']>=config['monthly_report_capacity']:
        raise ValueError('Pilot admission capacity reached; contact the pilot support officer')
    for _ in range(100):
        rid='NVB-'+datetime.now(timezone.utc).strftime('%Y%m%d')+secrets.token_hex(2).upper()
        if not rows('SELECT request_id FROM citizen_requests WHERE request_id=?',(rid,)): break
    else: raise ValueError('Daily ticket space is busy; contact the pilot officer')
    tracking=data.get('tracking_secret') or secrets.token_urlsafe(32)
    metadata={'is_synthetic':p['data_mode']=='synthetic','pilot_id':pid,'processing_status':'queued',
        'location_status':loc['verification_status'],'geolocation_status':'not_observed','provider_mode':'not_called'}
    db.execute('''INSERT INTO citizen_requests(request_id,source_channel,input_language,district,state,ward,lat,lng,
        original_text,translated_text,category,urgency,sentiment,status,ai_metadata_json,created_at,routed_department)
        VALUES(?,?,?,?,?,?,0,0,?,'','Other','Routine','Unknown','Pending',?,?,?)''',
        (rid,source_channel or ('Voice IVR' if audio else 'Web Form'),language,loc['district'],loc['state'],loc['ward'],
         message or 'Audio received; transcription pending',json.dumps(metadata),stamp,config['routing'][loc['district']]))
    # Coordinates are explicitly unknown; do not interpret the schema's 0/0 placeholder as a location.
    db.execute('''INSERT INTO pilot_requests(request_id,pilot_id,location_id,source_id,idempotency_key,payload_sha256,
        payload_json,tracking_hash,processing_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        (rid,pid,loc['location_id'],clean['source_id'],key,fingerprint,json.dumps(clean),digest(tracking),'queued',stamp,stamp))
    if clean.get('citizen_category'):
        db.execute('UPDATE citizen_requests SET category=? WHERE request_id=?',(clean['citizen_category'],rid))
    if clean.get('location'):
        metadata['geolocation_status']='citizen_supplied_unverified'
        db.execute('UPDATE citizen_requests SET lat=?,lng=?,ai_metadata_json=? WHERE request_id=?',
            (clean['location']['lat'],clean['location']['lng'],json.dumps(metadata),rid))
    if evidence:save_evidence(rid,data['evidence'])
    from datetime import timedelta
    due=(datetime.now(timezone.utc)+timedelta(days=config.get('review_days',2))).isoformat()
    db.execute('UPDATE citizen_requests SET sla_due_at=? WHERE request_id=?',(due,rid))
    db.execute('''INSERT INTO pilot_outbox(job_id,request_id,kind,status,next_attempt_at,created_at,updated_at)
        VALUES(?,?,'process_intake','queued',?,?,?)''',('JOB-'+secrets.token_hex(12),rid,stamp,stamp,stamp))
    from .governance import record_consent_event
    record_consent_event(rid,'pilot','request_processing',True,language,'citizen','consent',
        {'event_state':'granted','notice_version':config['notice_version'],'is_synthetic':metadata['is_synthetic']},commit=False)
    write_audit_log('citizen','pilot_intake_accepted','citizen_request',rid,{'pilot_id':pid,'data_mode':p['data_mode']},commit=False)
    db.commit()
    return {'request_id':rid,'reused':False,'processing_status':'queued','tracking_secret':tracking,
        'notice':'Saved durably. AI results are pending. Keep the private tracking code.'}


def tracking(rid,secret):
    found=rows('''SELECT p.tracking_hash,p.processing_status,c.request_id,c.status,c.translated_text,c.routed_department,
        c.ai_metadata_json FROM pilot_requests p JOIN citizen_requests c ON c.request_id=p.request_id WHERE p.request_id=?''',(rid,))
    if not found or not secret or not hmac.compare_digest(found[0]['tracking_hash'],digest(secret)):
        raise LookupError('Ticket or private tracking code is invalid')
    r=found[0];r.pop('tracking_hash');r['processing']=json.loads(r.pop('ai_metadata_json'));return r


def reviewers(s):
    found=rows("SELECT u.name,u.role,m.state,m.district FROM users u JOIN pilot_memberships m ON m.user_id=u.id WHERE m.pilot_id=? AND m.active=1 AND u.role IN ('admin','auditor')",(s['pilot_id'],))
    return [r for r in found if all(not r[k] or (s.get(k) and r[k]==s[k]) for k in ('state','district'))]


def validate_reviewer(name,s,geography):
    if s.get('pilot_id') and not any(r['name']==name for r in reviewers({**s,**geography})):
        raise ValueError('Reviewer must be assigned to this programme and geography')


def update_launch_record(pid,data,kind,record_id):
    from .auditor_workbench import safe_uri
    db=get_db();db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    p=programme(pid)
    if data.get('version')!=p['version']:raise ValueError('Programme changed; refresh before saving')
    if kind=='check':
        if record_id not in GATES:raise LookupError('Unknown launch gate')
        status=data.get('status')
        if status not in ('pending','blocked','rehearsed','accepted'):raise ValueError('Invalid gate state')
        reference=safe_uri(data.get('reference_uri')) if status in ('rehearsed','accepted') else None
        if status=='accepted' and (p['data_mode']=='synthetic' or reference.startswith('urn:nvb:demo:')):
            raise ValueError('Use rehearsed for synthetic evidence; operational acceptance needs real evidence')
        notes=text(data.get('notes'),'review notes',10)
        db.execute('UPDATE pilot_checks SET status=?,reference_uri=?,notes=?,reviewed_by=?,reviewed_at=? WHERE pilot_id=? AND check_key=?',
            (status,reference,notes,g.current_user['name'],now(),pid,record_id))
    else:
        found=rows('SELECT * FROM pilot_locations WHERE pilot_id=? AND location_id=?',(pid,record_id))
        if not found:raise LookupError('Unknown pilot community')
        loc=found[0];status=data.get('verification_status')
        if status not in ('proposed','reviewed'):raise ValueError('Invalid boundary state')
        name=text(data.get('local_body'),'community name',3,200)
        ward=text(data.get('ward'),'ward or panchayat',1,150)
        code=text(data.get('official_code'),'official location code',1,100) if status=='reviewed' else None
        reference=safe_uri(data.get('reference_uri')) if status=='reviewed' else None
        if status=='reviewed' and reference.startswith('urn:nvb:demo:'):raise ValueError('Official boundary review requires a real reference')
        if ward!=loc['ward'] and rows('SELECT request_id FROM pilot_requests WHERE location_id=? LIMIT 1',(record_id,)):
            raise ValueError('Existing reports retain their recorded catchment; configure real communities in a fresh operational database')
        db.execute('UPDATE pilot_locations SET local_body=?,ward=?,official_code=?,reference_uri=?,verification_status=? WHERE pilot_id=? AND location_id=?',
            (name,ward,code,reference,status,pid,record_id))
    db.execute('UPDATE pilot_programmes SET version=version+1,updated_at=? WHERE pilot_id=?',(now(),pid))
    write_audit_log(g.current_user['name'],'pilot_'+kind+'_reviewed','pilot',pid,{'record':record_id,'status':status},commit=False)
    db.commit();return programme(pid)
