"""Pilot administration, private review and durable public intake."""
import json
import secrets
from flask import Blueprint, current_app, g, jsonify, request, render_template, session
from ..auth import require_auth, require_roles
from ..db import get_db
from ..audit import write_audit_log
from ..services import pilot as work
from ..services.public_safety import allow as allow_public_request

pilot_bp=Blueprint('ministry_pilot',__name__)


@pilot_bp.errorhandler(ValueError)
def bad_request(exc):
    get_db().rollback();return jsonify(success=False,error=str(exc)),400


@pilot_bp.errorhandler(LookupError)
def not_found(exc):
    get_db().rollback();return jsonify(success=False,error=str(exc)),404


@pilot_bp.errorhandler(PermissionError)
def forbidden(exc):
    get_db().rollback();return jsonify(success=False,error=str(exc)),403


def body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict): raise ValueError('A JSON object is required')
    return data


def admin_programme():
    s=work.scope()
    if s.get('state') or s.get('district'): raise PermissionError('Programme-wide administration requires programme-wide assignment')
    return s


@pilot_bp.route('/pilot')
@pilot_bp.route('/pilot/submit')
@pilot_bp.route('/pilot/dashboard')
@pilot_bp.route('/pilot/settings')
def page():
    tokens={}
    if current_app.config.get('DEMO_MODE'):
        tokens={r:current_app.config.get(r.upper()+'_API_TOKEN','') for r in ('admin','analyst','auditor')}
        for district in ('Vellore','Tirupati'):
            users=work.rows('SELECT api_token FROM users WHERE name=?',(district+' demo officer',))
            if users:tokens[district.lower()]=users[0]['api_token']
    session.setdefault('pilot_csrf',secrets.token_urlsafe(24))
    pid=current_app.config.get('PILOT_ID') or work.PILOT_ID
    p=work.programme(pid)
    from ..services.pilot_portal import public_channels, configuration
    page_name={'/pilot':'home','/pilot/submit':'submit','/pilot/dashboard':'dashboard','/pilot/settings':'settings'}[request.path]
    if page_name in ('dashboard','settings') and not current_app.config.get('DEMO_MODE'):
        from ..services.pilot_identity import session_user
        if not session_user():
            from flask import redirect
            return redirect('/pilot/login')
    locations=work.rows('SELECT location_id,state,district,local_body,ward,verification_status FROM pilot_locations WHERE pilot_id=? ORDER BY district,location_id',(pid,))
    counts=[]
    portal_config=configuration(p)
    if portal_config['privacy'].get('public_progress'):
        from ..services.governance import dp_metadata, privatize_count, should_apply_dp
        min_group=max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', 3)), 1)
        counts=work.rows('''SELECT c.district,COUNT(*) AS reports,SUM(CASE WHEN c.status IN ('Resolved','Closed') THEN 1 ELSE 0 END) AS resolved
            FROM citizen_requests c JOIN pilot_requests r ON r.request_id=c.request_id WHERE r.pilot_id=? GROUP BY c.district HAVING COUNT(*)>=?''',(pid,min_group))
        for count in counts:
            if should_apply_dp():
                count['reports']=max(0,privatize_count(int(count['reports']),epsilon=float(dp_metadata().get('epsilon') or 0.75)))
                count['resolved']=max(0,privatize_count(int(count['resolved'] or 0),epsilon=float(dp_metadata().get('epsilon') or 0.75)))
    return render_template('pilot_home.html' if page_name=='home' else 'submit.html' if page_name=='submit' else 'pilot.html',pilot_id=pid,
        pilot_page=page_name,pilot_public=page_name in ('home','submit'),programme=p,channels=public_channels(p),locations=locations,public_counts=counts,
        languages={k:v for k,v in current_app.config['LANGUAGES'].items() if k in p['config']['languages']},
        categories=p['config'].get('categories',current_app.config['CATEGORIES']),portal_config=portal_config,
        role_tokens=tokens,demo=current_app.config.get('DEMO_MODE'),csrf=session['pilot_csrf'],
        intake_view=request.path.endswith('/submit'),oidc_enabled=bool(current_app.config.get('OIDC_ISSUER')))


@pilot_bp.route('/api/v2/pilot/public-config')
def public_config():
    pid=request.args.get('pilot_id') or current_app.config.get('PILOT_ID') or work.PILOT_ID
    p=work.programme(pid)
    locations=work.rows('SELECT location_id,state,district,local_body,ward,location_kind,verification_status FROM pilot_locations WHERE pilot_id=? ORDER BY district,location_id',(pid,))
    from ..services.pilot_portal import public_channels,configuration
    return jsonify(success=True,pilot_id=pid,title=p['title'],status=p['status'],data_mode=p['data_mode'],
        languages=p['config']['languages'],notice=configuration(p)['privacy']['consent_notice'],locations=locations,
        categories=p['config'].get('categories',current_app.config['CATEGORIES']),channels=public_channels(p))


@pilot_bp.route('/api/v2/pilot/intake',methods=['POST'])
def intake():
    data=body();pid=request.args.get('pilot_id') or current_app.config.get('PILOT_ID') or work.PILOT_ID
    from ..services.pilot_portal import configuration
    if not configuration(work.programme(pid))['channels']['web']['enabled']:raise PermissionError('Web intake is paused by the programme administrator')
    result=work.intake(pid,data,request.headers.get('Idempotency-Key',''))
    return jsonify(success=True,**result),200 if result['reused'] else 202


@pilot_bp.route('/api/v2/pilot/track',methods=['POST'])
def track():
    allowed, retry_after = allow_public_request(f"track:{request.remote_addr or 'unknown'}", 20, 60)
    if not allowed:
        return jsonify(success=False,error='Tracking rate limit reached; retry shortly.'), 429, {'Retry-After': str(retry_after)}
    data=body()
    response=jsonify(success=True,record=work.tracking(data.get('request_id'),data.get('tracking_secret')))
    response.headers['Cache-Control']='no-store'
    return response


@pilot_bp.route('/api/v2/pilot/snapshot')
@require_roles('admin','analyst','auditor')
def snapshot():
    from ..services.analyst_workbench import where,stats
    s=work.scope();pid=s.get('pilot_id') or work.PILOT_ID;p=work.programme(pid)
    clause,params=where(s)
    status=work.rows(f'''SELECT p.processing_status,COUNT(*) n FROM pilot_requests p JOIN citizen_requests c
        ON c.request_id=p.request_id WHERE {clause} GROUP BY p.processing_status''',params)
    jobs=work.rows(f'''SELECT o.kind,o.status,COUNT(*) n,MIN(o.created_at) oldest FROM pilot_outbox o
        JOIN citizen_requests c ON c.request_id=o.request_id WHERE {clause} GROUP BY o.kind,o.status''',params)
    usage=work.rows(f'''SELECT ps.step,ps.provider,ps.status,COUNT(*) n,AVG(ps.latency_ms) mean_latency_ms
        FROM pilot_provider_steps ps JOIN citizen_requests c ON c.request_id=ps.request_id
        WHERE {clause} GROUP BY ps.step,ps.provider,ps.status''',params)
    locs=work.rows('SELECT * FROM pilot_locations WHERE pilot_id=? ORDER BY district,location_id',(pid,))
    locs=[r for r in locs if all(not s.get(k) or s[k]==r[k] for k in ('state','district'))]
    users=work.rows('''SELECT u.id,u.name,u.role,m.state,m.district FROM users u JOIN pilot_memberships m
        ON u.id=m.user_id WHERE m.pilot_id=? AND m.active=1 ORDER BY u.name''',(pid,))
    users=[u for u in users if all(not s.get(k) or not u[k] or s[k]==u[k] for k in ('state','district'))]
    from ..services.pilot_portal import configuration
    p['portal']=configuration(p)
    assignment=next((m for m in work.user_memberships() if m['pilot_id']==pid),{})
    return jsonify(success=True,programme=p,scope=s,assignment={k:assignment.get(k,'') for k in ('state','district')},stats=stats(s),processing=status,jobs=jobs,usage=usage,
        locations=locs,members=users,user=g.current_user,checks=work.rows('SELECT * FROM pilot_checks WHERE pilot_id=? ORDER BY check_key',(pid,)),
        cost={'status':'estimate_not_billing','monthly_cloud_budget_inr':p['config']['monthly_cloud_budget_inr'],
              'actual_spend_inr':None,'notice':'Provider steps and latency are measured. Tokens not returned by providers and actual cloud charges remain unavailable.'})


@pilot_bp.route('/api/v2/pilot/requests')
@require_roles('admin','analyst','auditor')
def requests_list():
    from ..services.analyst_workbench import where
    s=work.scope();clause,params=where(s)
    if request.args.get('processing_status'):
        clause+=' AND p.processing_status=?';params.append(request.args['processing_status'])
    search=request.args.get('search','').strip()[:100]
    if search:
        clause+=" AND LOWER(c.request_id || ' ' || c.original_text) LIKE ? ESCAPE '!'"
        params.append('%'+search.lower().replace('!','!!').replace('%','!%').replace('_','!_')+'%')
    limit=min(max(int(request.args.get('limit',50)),1),100);offset=max(int(request.args.get('offset',0)),0)
    records=work.rows(f'''SELECT c.request_id,c.source_channel,c.state,c.district,c.ward,c.original_text,c.translated_text,
        c.category,c.urgency,c.status,c.input_language,c.routed_department,c.created_at,c.ai_metadata_json,
        p.processing_status,p.version,p.assigned_to,p.payload_json FROM citizen_requests c JOIN pilot_requests p ON p.request_id=c.request_id
        WHERE {clause} ORDER BY c.id DESC LIMIT ? OFFSET ?''',[*params,limit,offset])
    for r in records:
        r['processing']=json.loads(r.pop('ai_metadata_json'))
        payload=json.loads(r.pop('payload_json'))
        r['citizen_input']={k:payload[k] for k in ('citizen_translation','citizen_ward','citizen_category','location') if k in payload}
        r['has_audio']=bool(payload.get('audio_base64') or payload.get('audio_object'))
    total=work.rows(f'SELECT COUNT(*) n FROM citizen_requests c JOIN pilot_requests p ON p.request_id=c.request_id WHERE {clause}',params)[0]['n']
    return jsonify(success=True,items=records,total=total,offset=offset,limit=limit)


@pilot_bp.get('/api/v2/pilot/requests/<rid>/audio')
@require_roles('admin','analyst','auditor')
def audio_evidence(rid):
    from flask import Response
    from ..services.pilot_media import read_audio
    row=work.request_row(rid);payload=json.loads(row['payload_json'])
    return Response(read_audio(payload),mimetype=payload.get('mime_type','audio/webm'),
        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@pilot_bp.route('/api/v2/pilot/requests/<rid>/review',methods=['POST'])
@require_roles('admin','analyst')
def review(rid):
    data=body();db=get_db();db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    row=work.request_row(rid)
    if data.get('version')!=row['version']: raise ValueError('Request changed; refresh before reviewing')
    reason=work.text(data.get('reason'),'review rationale',10)
    original=work.text(data.get('original_text'),'original text or corrected transcript',10,4000)
    translated=work.text(data.get('translated_text'),'reviewed English text',10,6000)
    if data.get('category') not in current_app.config['CATEGORIES']: raise ValueError('Choose a valid category')
    if data.get('urgency') not in ('Routine','Urgent','Emergency'): raise ValueError('Choose a valid urgency')
    state=data.get('status','Pending')
    if state not in ('Pending','Acknowledged','In Progress','Resolved','Closed'): raise ValueError('Choose a valid request stage')
    if state in ('Resolved','Closed'):
        from ..services.auditor_workbench import safe_uri
        safe_uri(data.get('completion_reference'))
    assigned=data.get('assigned_to')
    if assigned:
        member=work.rows('''SELECT m.*,u.role FROM pilot_memberships m JOIN users u ON u.id=m.user_id
            WHERE m.pilot_id=? AND m.user_id=? AND m.active=1''',(row['pilot_id'],assigned))
        if not member or member[0]['role'] not in ('analyst','admin') or any(member[0][k] and member[0][k]!=row[k] for k in ('state','district')):
            raise ValueError('Choose an officer assigned to this geography')
    metadata=json.loads(row['ai_metadata_json']);metadata.update(processing_status='human_reviewed',
        human_review={'actor':g.current_user['name'],'at':work.now(),'reason':reason,'completion_reference':data.get('completion_reference')},
        translation_status='human_reviewed')
    db.execute('UPDATE citizen_requests SET original_text=?,translated_text=?,category=?,urgency=?,status=?,ai_metadata_json=? WHERE request_id=?',
        (original,translated,data['category'],data['urgency'],state,json.dumps(metadata),rid))
    db.execute("UPDATE pilot_requests SET processing_status='human_reviewed',assigned_to=?,version=version+1,updated_at=? WHERE request_id=?",
        (assigned,work.now(),rid))
    db.execute("UPDATE pilot_outbox SET status='superseded',updated_at=? WHERE request_id=? AND kind='process_intake' AND status<>'completed'",(work.now(),rid))
    db.execute('''INSERT INTO request_lifecycle_events(request_id,event_type,from_status,to_status,actor,channel,reason,created_at)
        VALUES(?,'officer_review',?,?,?,'pilot',?,?)''',(rid,row['status'],state,g.current_user['name'],reason,work.now()))
    from ..services.pilot_worker import queue_analytics
    queue_analytics(rid)
    write_audit_log(g.current_user['name'],'pilot_request_reviewed','citizen_request',rid,{'reason':reason,'stage':state},commit=False)
    db.commit();return jsonify(success=True,version=row['version']+1)


@pilot_bp.route('/api/v2/pilot/settings',methods=['POST'])
@require_roles('admin')
def settings():
    s=admin_programme();return jsonify(success=True,programme=work.settings_update(s['pilot_id'],body()))


@pilot_bp.route('/api/v2/pilot/members',methods=['POST'])
@require_roles('admin')
def members():
    s=admin_programme();data=body();uid=data.get('user_id')
    if not work.rows('SELECT id FROM users WHERE id=?',(uid,)): raise ValueError('Provision the named identity first')
    state=data.get('state','');district=data.get('district','')
    locs=work.rows('SELECT DISTINCT state,district FROM pilot_locations WHERE pilot_id=?',(s['pilot_id'],))
    if district and not state: raise ValueError('District assignment requires a state')
    if state and not any(r['state']==state and (not district or r['district']==district) for r in locs): raise ValueError('Geography is not in this programme')
    active=data.get('active',True)
    if not isinstance(active,bool):raise ValueError('Active must be true or false')
    if uid==g.current_user['id'] and (not active or state or district):raise ValueError('Another programme administrator must change your own assignment')
    get_db().execute('''INSERT INTO pilot_memberships(pilot_id,user_id,state,district,active) VALUES(?,?,?,?,?)
        ON CONFLICT(pilot_id,user_id) DO UPDATE SET state=excluded.state,district=excluded.district,active=excluded.active''',(s['pilot_id'],uid,state,district,int(active)))
    write_audit_log(g.current_user['name'],'pilot_membership_assigned','pilot',s['pilot_id'],{'user_id':uid,'state':state,'district':district,'active':active},commit=False)
    get_db().commit();return jsonify(success=True)


@pilot_bp.route('/api/v2/pilot/import',methods=['POST'])
@require_roles('admin','analyst')
def import_records():
    s=work.scope();data=body();records=data.get('records')
    if not isinstance(records,list) or not 1<=len(records)<=100: raise ValueError('Import 1–100 records at a time')
    # Validate all geographical assignments before starting any admitted record.
    for r in records:
        if not isinstance(r,dict): raise ValueError('Each record must be an object')
        loc=work.rows('SELECT * FROM pilot_locations WHERE pilot_id=? AND location_id=?',(s['pilot_id'],r.get('location_id')))
        if not loc or any(s.get(k) and s[k]!=loc[0][k] for k in ('state','district')): raise PermissionError('Import contains an unassigned location')
        work.text(r.get('source_id'),'source system ID',1,150)
    result=[]
    for r in records:
        try:result.append(work.intake(s['pilot_id'],r,'import:'+work.digest(r['source_id'])))
        except ValueError as exc:
            get_db().rollback();result.append({'source_id':r['source_id'],'error':str(exc)})
    return jsonify(success=True,items=result,notice='Per-record outcomes shown; retries reuse source IDs and never silently duplicate an import.')


@pilot_bp.post('/api/v2/pilot/checks/<key>')
@require_roles('admin','auditor')
def review_gate(key):
    s=admin_programme()
    return jsonify(success=True,programme=work.update_launch_record(s['pilot_id'],body(),'check',key))


@pilot_bp.post('/api/v2/pilot/locations/<key>')
@require_roles('admin')
def configure_location(key):
    s=admin_programme()
    return jsonify(success=True,programme=work.update_launch_record(s['pilot_id'],body(),'location',key))


@pilot_bp.route('/api/v2/pilot/process',methods=['POST'])
@require_roles('admin')
def process_demo():
    if not current_app.config.get('DEMO_MODE'): raise PermissionError('Use the authenticated worker in an operational deployment')
    from ..services.pilot_worker import run_one
    s=work.scope();data=body();rid=data.get('request_id');work.request_row(rid,s)
    jobs=work.rows("SELECT job_id FROM pilot_outbox WHERE request_id=? AND kind='process_intake'",(rid,))
    return jsonify(success=True,result=run_one(jobs[0]['job_id']) if jobs else {'status':'idle'})


@pilot_bp.route('/health/live')
def live(): return jsonify(status='up')


@pilot_bp.route('/api/v2/pilot/internal/work',methods=['POST'])
def worker_http():
    from ..services.pilot_dispatch import require_worker_identity
    from ..services.pilot_worker import run_one
    require_worker_identity();data=body()
    if data.get('kind')=='evaluation':
        from ..services.auditor_evaluation import run_pending
        run_pending(current_app._get_current_object())
        return jsonify(success=True,result={'status':'evaluations_drained'})
    return jsonify(success=True,result=run_one(work.text(data.get('job_id'),'job ID',8,100)))


@pilot_bp.route('/api/v2/pilot/internal/dispatch',methods=['POST'])
def dispatch_http():
    from ..services.pilot_dispatch import require_worker_identity,dispatch
    require_worker_identity();return jsonify(success=True,**dispatch())


@pilot_bp.route('/health/ready')
def ready():
    try:
        get_db().execute('SELECT pilot_id FROM pilot_programmes LIMIT 1').fetchone()
        return jsonify(status='ready',database='reachable')
    except Exception:
        return jsonify(status='not_ready'),503


def install_pilot(app):
    from .pilot_portal import portal_bp
    app.register_blueprint(portal_bp)
    import click
    from ..services.pilot_identity import register_identity
    register_identity(app)
    from ..services.pilot_operations import register
    register(app)
    if app.config.get('PILOT_ONLY') and not app.config.get('DEMO_MODE'):
        if not app.config.get('SECRET_KEY') or app.config['SECRET_KEY']=='citizenvoice-dev-key-2026':
            raise RuntimeError('Set a deployment secret before operational pilot startup')
        app.config.update(SESSION_COOKIE_SECURE=True,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax')
    app.register_error_handler(PermissionError,forbidden)

    @app.cli.command('pilot-migrate')
    def migrate_command():
        from ..db import migrate_application
        migrate_application(app);click.echo('Schema migration completed; operational mode does not seed demo users or citizen records.')

    @app.cli.command('pilot-worker')
    @click.option('--limit',default=10,type=click.IntRange(1,100))
    def worker_command(limit):
        from ..services.pilot_worker import run_one
        for _ in range(limit):
            result=run_one();click.echo(json.dumps(result))
            if result['status']=='idle':break

    @app.before_request
    def isolated_pilot_boundary():
        if not current_app.config.get('PILOT_ONLY'):return
        if request.path in ('/','/submit','/demo'):
            from flask import redirect
            return redirect('/pilot/submit' if request.path=='/submit' else '/pilot')
        if not request.path.startswith('/api/'):return
        public={'/api/submission/readiness','/api/health','/api/v2/pilot/public-config','/api/v2/pilot/intake','/api/v2/pilot/track'}
        public.update('/api/v2/pilot/portal/'+p for p in ('states','districts','ward-suggest','translate','classify','transcribe-voice','evidence'))
        if request.path in public:return
        if request.path.startswith('/api/v2/pilot/channels/') and request.path.endswith('/webhook'):return
        if request.path.startswith('/api/v2/pilot/internal/'):return
        response=require_auth(lambda:None)()
        if response is not None:return response
        allowed=request.path.startswith(('/api/v2/pilot/','/api/v2/analyst/','/api/v2/auditor/'))
        allowed=allowed or request.path in ('/api/v1/auth/me','/api/stats','/api/states','/api/districts','/api/v1/requests')
        allowed=allowed or (request.path.startswith('/api/v1/policy/decisions/') and request.path.endswith('/approve'))
        if not allowed:raise PermissionError('This deployment exposes the scoped pilot interfaces; the legacy endpoint is unavailable')
