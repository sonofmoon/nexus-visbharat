"""Transactional review actions; no external dispatch or financial operations."""
import json
import re
import uuid
from datetime import timedelta
from flask import g
from ..db import get_db
from ..audit import write_audit_log
from . import auditor_workbench as work


class Conflict(ValueError): pass


def identifier(prefix):
    return prefix+'-'+work.now().strftime('%Y%m%d%H%M%S%f')+'-'+uuid.uuid4().hex[:8]


def actor(): return g.current_user['name']


def lock():
    get_db().execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')


def log(action,resource,rid,details):
    write_audit_log(actor(),action,resource,rid,details,commit=False)


def request_record(request_id,scope):
    clause,params=work.citizen_where({**scope,'request_id':request_id})
    rows=work.query(f'SELECT c.* FROM citizen_requests c WHERE {clause}',params)
    if not rows: raise LookupError('Ticket not found in the authorized scope')
    return rows[0]


def create_case(data,scope):
    kind=data.get('kind','discrepancy')
    if kind not in ('discrepancy','rights','security','quality'): raise ValueError('Unknown case kind')
    severity=data.get('severity','MEDIUM')
    if severity not in ('LOW','MEDIUM','HIGH','CRITICAL'): raise ValueError('Unknown severity')
    title=work.text(data.get('title'),'title',8,200)
    notes=work.text(data.get('notes'),'review reason',10)
    owner=work.text(data.get('owner') or actor(),'owner',1,150)
    if not work.query("SELECT id FROM users WHERE name=? AND role IN ('admin','auditor')",(owner,)):
        raise ValueError('Assign an existing Auditor or Admin user')
    mode=data.get('data_mode','operational')
    if mode not in ('synthetic','operational'): raise ValueError('Choose synthetic or operational evidence')
    due=work.analyst.parse_time(data.get('due_at')) if data.get('due_at') else work.now()+timedelta(days=7)
    if not due: raise ValueError('Invalid due date')
    pid=data.get('project_id') or scope.get('project_id') or None
    rid=data.get('request_id') or None
    geo={k:'' for k in ('state','district','category')}
    if pid:
        p=work.resolve_project(pid,scope);geo.update({k:p[k] for k in geo})
    if rid:
        r=request_record(rid,scope)
        if pid and work.analyst.candidate_id(r['state'],r['district'],r['ward'] or '',r['category'])!=pid: raise ValueError('Ticket belongs to a different project')
        geo.update({k:r[k] for k in geo})
        if json.loads(r['ai_metadata_json']).get('is_synthetic'): mode='synthetic'
    if kind=='rights' and not rid: raise ValueError('A rights request must link a ticket')
    if not pid and not rid and (work.has_report_filter(scope) or kind!='security'):
        raise ValueError('Link a scoped project or ticket; global cases are only for security')
    from .pilot import validate_reviewer
    validate_reviewer(owner,scope,geo)
    key=data.get('idempotency_key')
    if key: key=work.text(key,'idempotency key',8,150)
    lock();db=get_db()
    if key:
        prior=work.query('SELECT * FROM auditor_cases WHERE idempotency_key=?',(str(g.current_user['id'])+':'+key,))
        if prior:
            db.rollback()
            return work.case_detail(prior[0]['case_id'],scope)
    cid=identifier('NVB-AUD');timestamp=work.now().isoformat()
    db.execute('''INSERT INTO auditor_cases(case_id,kind,project_id,request_id,state,district,category,title,severity,status,owner,due_at,
        data_mode,created_by,created_at,updated_at,version,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,'open',?,?,?,?,?,?,1,?)''',
        (cid,kind,pid,rid,geo['state'],geo['district'],geo['category'],title,severity,owner,due.isoformat(),mode,actor(),timestamp,timestamp,
         str(g.current_user['id'])+':'+key if key else None))
    db.execute('''INSERT INTO auditor_case_events(case_id,actor,action,to_status,notes,created_at) VALUES(?,?,'created','open',?,?)''',(cid,actor(),notes,timestamp))
    log('auditor_case_created','audit_case',cid,{'kind':kind,'project_id':pid,'request_id':rid,'data_mode':mode,'notes':notes})
    db.commit()
    return work.case_detail(cid,scope)


def update_case(cid,data,scope):
    action=data.get('action');notes=work.text(data.get('notes'),'action rationale',10)
    lock();db=get_db();row=work.case_detail(cid,scope)
    if data.get('version')!=row['version']: raise Conflict('Case changed; refresh before acting')
    old=row['status'];target=old;owner=row['owner'];evidence=None
    if action=='assign':
        owner=work.text(data.get('owner'),'owner',1,150)
        if not work.query("SELECT id FROM users WHERE name=? AND role IN ('admin','auditor')",(owner,)): raise ValueError('Assign an existing Auditor or Admin')
        from .pilot import validate_reviewer
        validate_reviewer(owner,scope,{k:row[k] for k in ('state','district')})
    elif action=='start' and old in ('open','awaiting_evidence'): target='investigating'
    elif action=='request_evidence' and old in ('open','investigating'): target='awaiting_evidence'
    elif action=='reopen' and old=='resolved': target='open'
    elif action=='resolve' and old in ('investigating','awaiting_evidence'):
        if actor()==row['created_by']: raise ValueError('An independent Auditor or Admin must close this finding')
        evidence=work.safe_uri(data.get('evidence_reference'))
        if row['kind']=='rights':
            restriction=work.query('SELECT * FROM auditor_processing_restrictions WHERE request_id=?',(row['request_id'],))
            if not restriction: raise ValueError('Record the verified purpose/basis action before closing this rights case')
        target='resolved'
    elif action=='request_hold' and old!='resolved':
        # This is a documented recommendation only. There is no bank/PFMS connector.
        target='awaiting_evidence'
        notes='Financial hold recommended; no funds frozen. '+notes
    else: raise ValueError('Action is not allowed from the current case state')
    timestamp=work.now().isoformat()
    db.execute('''UPDATE auditor_cases SET status=?,owner=?,updated_at=?,version=version+1,
        resolution=?,closure_evidence=?,closed_by=? WHERE case_id=?''',
        (target,owner,timestamp,notes if target=='resolved' else None,evidence,actor() if target=='resolved' else None,cid))
    db.execute('''INSERT INTO auditor_case_events(case_id,actor,action,from_status,to_status,notes,evidence,created_at)
        VALUES(?,?,?,?,?,?,?,?)''',(cid,actor(),action,old,target,notes,evidence,timestamp))
    log('auditor_case_'+action,'audit_case',cid,{'before':old,'after':target,'owner':owner,'notes':notes,'evidence_reference':evidence})
    db.commit();return work.case_detail(cid,scope)


def add_evidence(pid,data,scope):
    work.resolve_project(pid,scope)
    kind=data.get('kind')
    if kind not in ('source','milestone','payment','site','outcome','evaluation_protocol'): raise ValueError('Unknown evidence type')
    title=work.text(data.get('title'),'title',5,200);uri=work.safe_uri(data.get('source_uri'))
    observed=work.analyst.parse_time(data.get('observed_at'))
    if not observed or observed>work.now(): raise ValueError('Observation time must be valid and cannot be in the future')
    mode=data.get('data_mode')
    if mode not in ('synthetic','operational'): raise ValueError('Declare synthetic or operational evidence')
    sha=data.get('sha256') or None
    if sha and not re.fullmatch('[a-fA-F0-9]{64}',sha): raise ValueError('SHA-256 must contain 64 hexadecimal characters')
    m=data.get('metadata',{})
    if not isinstance(m,dict) or len(json.dumps(m))>16000: raise ValueError('Evidence metadata must be an object smaller than 16 KB')
    for field in ('sanctioned_lakh','released_lakh','spent_lakh','certified_progress_pct','observed_progress_pct','value','sample_size'):
        if field in m:
            m[field]=work.number(m[field],field,0 if field!='value' else -1e9,100 if field.endswith('_pct') else 1e9)
    if kind in ('payment','milestone','site'):
        for k in ('milestone','period'): m[k]=work.text(m.get(k),k,1,150)
    if kind=='outcome':
        for k in ('measure','unit','catchment'): m[k]=work.text(m.get(k),k,1,150)
        if m.get('phase') not in ('before','after'): raise ValueError('Outcome phase must be before or after')
        if 'value' not in m or m.get('sample_size',0)<1: raise ValueError('Observed value and sample size are required')
    if kind=='source':
        for k in ('publisher','release','redistribution_terms','boundary_crosswalk'):
            m[k]=work.text(m.get(k),k,3,1000)
    lock();db=get_db();eid=identifier('NVB-EV')
    db.execute('''INSERT INTO auditor_evidence(evidence_id,project_id,kind,title,source_uri,sha256,observed_at,data_mode,metadata_json,
        status,submitted_by,submitted_at,version) VALUES(?,?,?,?,?,?,?,?,?,'verification_pending',?,?,1)''',
        (eid,pid,kind,title,uri,sha,observed.isoformat(),mode,json.dumps(m),actor(),work.now().isoformat()))
    log('auditor_evidence_submitted','project',pid,{'evidence_id':eid,'kind':kind,'data_mode':mode})
    db.commit();return next(r for r in work.evidence_rows(pid,scope) if r['evidence_id']==eid)


def review_evidence(eid,data,scope):
    lock();db=get_db();rows=work.query('SELECT * FROM auditor_evidence WHERE evidence_id=?',(eid,))
    if not rows: raise LookupError('Evidence not found')
    row=rows[0];work.resolve_project(row['project_id'],scope)
    if actor()==row['submitted_by']: raise ValueError('The submitter cannot independently review their own evidence')
    if data.get('version')!=row['version']: raise Conflict('Evidence changed; refresh before reviewing')
    status=data.get('status')
    if status not in ('human_reviewed','rejected'): raise ValueError('Choose human_reviewed or rejected')
    notes=work.text(data.get('notes'),'review criteria and finding',20)
    db.execute('UPDATE auditor_evidence SET status=?,reviewed_by=?,reviewed_at=?,review_notes=?,version=version+1 WHERE evidence_id=?',
               (status,actor(),work.now().isoformat(),notes,eid))
    log('auditor_evidence_reviewed','project',row['project_id'],{'evidence_id':eid,'status':status,'notes':notes})
    db.commit();return {'evidence_id':eid,'status':status,'version':row['version']+1}


def consent_action(data,scope):
    rid=work.text(data.get('request_id'),'ticket ID',1,150);row=request_record(rid,scope)
    state=data.get('event_state')
    if state not in ('granted','declined','withdrawn','missing','other_basis'): raise ValueError('Unknown receipt state')
    purpose=work.text(data.get('purpose'),'purpose',3,150)
    basis=work.text(data.get('legal_basis') or 'consent','documented processing basis',3,200)
    if state=='other_basis' and basis=='consent': raise ValueError('Document the applicable non-consent basis')
    reference=work.safe_uri(data.get('receipt_reference'))
    reason=work.text(data.get('reason'),'identity check and action reason',10)
    lock();db=get_db()
    from .governance import record_consent_event
    m={'event_state':state,'consent_text_version':work.text(data.get('notice_version'),'notice version',1,100),
       'receipt_reference':reference,'reason':reason,'is_synthetic':bool(json.loads(row['ai_metadata_json']).get('is_synthetic'))}
    result=record_consent_event(rid,row['source_channel'],purpose,state=='granted',row['input_language'],actor(),basis,m,commit=False)
    restricted=state in ('declined','withdrawn','missing')
    db.execute('''INSERT INTO auditor_processing_restrictions(request_id,purpose,state,receipt_id,updated_at) VALUES(?,?,?,?,?)
        ON CONFLICT(request_id,purpose) DO UPDATE SET state=excluded.state,receipt_id=excluded.receipt_id,updated_at=excluded.updated_at''',
        (rid,purpose,'restricted' if restricted else 'documented_basis',result['event_id'],work.now().isoformat()))
    log('consent_'+state,'citizen_request',rid,{'purpose':purpose,'event_id':result['event_id'],'legal_basis':basis,'reason':reason})
    if work.query('SELECT request_id FROM pilot_requests WHERE request_id=?',(rid,)):
        from .pilot_worker import queue_analytics
        db.execute('UPDATE pilot_requests SET version=version+1,updated_at=? WHERE request_id=?',(work.now().isoformat(),rid))
        queue_analytics(rid)
    db.commit();return {**result,'state':state,'processing_restriction':restricted,
                         'notice':'The local purpose restriction is recorded. External-system retention or erasure requires its own verified completion evidence.'}


def save_export(kind,payload,scope):
    sid=identifier('NVB-PACK');raw=json.dumps(payload,sort_keys=True,ensure_ascii=False)
    get_db().execute('INSERT INTO auditor_snapshots(snapshot_id,owner_id,role,kind,scope_json,payload_json,created_at) VALUES(?,?,?,?,?,?,?)',
                    (sid,str(g.current_user['id']),g.current_user['role'],kind,json.dumps(scope),raw,work.now().isoformat()))
    log('auditor_export_prepared','evidence_pack',sid,{'kind':kind,'sha256':work.digest(payload)})
    get_db().commit();return {'snapshot_id':sid,'sha256':work.digest(payload),'download_url':'/api/v2/auditor/exports/'+sid}
