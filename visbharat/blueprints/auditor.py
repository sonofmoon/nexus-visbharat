"""Versioned Auditor API. Review actions never dispatch teams or control funds."""
import csv
import io
import json
from pathlib import Path
from flask import Blueprint, current_app, g, jsonify, request, Response
from ..auth import require_roles
from ..db import get_db
from ..services import auditor_workbench as work, auditor_actions as actions, auditor_evaluation as evaluation
from ..log.chain import verify_chain_integrity

auditor_bp=Blueprint('auditor_workbench',__name__)


@auditor_bp.errorhandler(ValueError)
@auditor_bp.errorhandler(LookupError)
@auditor_bp.errorhandler(PermissionError)
def invalid(error):
    get_db().rollback()
    status=409 if isinstance(error,actions.Conflict) else 403 if isinstance(error,PermissionError) else 404 if isinstance(error,LookupError) else 400
    return jsonify(success=False,error=str(error)),status


def body():
    if request.content_length and request.content_length>1024*1024: raise ValueError('Request exceeds 1 MiB')
    value=request.get_json(silent=True)
    if not isinstance(value,dict): raise ValueError('A JSON object is required')
    return value


def scope(): return work.scope_from(request.args)


@auditor_bp.route('/api/v2/auditor/snapshot')
@require_roles('admin','auditor')
def snapshot():
    sc = scope()
    db=get_db();db.commit()
    db.execute('BEGIN' if db.backend=='sqlite' else 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    result=work.summary(sc);db.commit()
    return jsonify(success=True,**result)


@auditor_bp.route('/api/v2/auditor/intelligence')
@require_roles('admin', 'auditor')
def intelligence():
    pid = request.args.get('project_id')
    intel = work.forensic_audit_intelligence(scope(), pid)
    return jsonify(success=True, **intel)


@auditor_bp.route('/api/v2/auditor/copilot/ask', methods=['POST'])
@require_roles('admin', 'auditor')
def ask_copilot():
    data = body()
    query_text = str(data.get('query') or '').strip()
    if not query_text:
        raise ValueError('Query is required')
    pid = data.get('project_id') or request.args.get('project_id')
    res = work.ask_auditor_copilot(query_text, scope(), pid)
    return jsonify(success=True, **res)


@auditor_bp.route('/api/v2/auditor/projects')
@require_roles('admin','auditor','analyst')
def projects():
    return jsonify(success=True,**work.projects(scope(),str(request.args.get('search') or '')[:200],work.page_limit(request.args.get('limit')),int(work.number(request.args.get('cursor',0),'cursor',0,1e7))))


@auditor_bp.route('/api/v2/auditor/projects/<pid>')
@require_roles('admin','auditor','analyst')
def project(pid): return jsonify(success=True,**work.dossier(pid,scope()))


@auditor_bp.route('/api/v2/auditor/projects/<pid>/financial')
@require_roles('admin','auditor')
def financial(pid): return jsonify(success=True,**work.financial_review(pid,scope()))


@auditor_bp.route('/api/v2/auditor/projects/<pid>/outcomes')
@require_roles('admin','auditor')
def outcomes(pid): return jsonify(success=True,**work.outcome_review(pid,scope()))


@auditor_bp.route('/api/v2/auditor/events')
@require_roles('admin','auditor')
def events(): return jsonify(success=True,**work.event_page(scope(),request.args))


def redact(value):
    if isinstance(value,dict):
        return {k:('[redacted]' if any(s in k.lower() for s in ('token','secret','password','phone','email','subject','ip_address')) else redact(v)) for k,v in value.items()}
    if isinstance(value,list): return [redact(x) for x in value]
    return value


@auditor_bp.route('/api/v2/auditor/events/<int:event_id>')
@require_roles('admin','auditor')
def event_detail(event_id):
    permitted=work.event_page(scope(),{'cursor':event_id+1,'limit':1})['items']
    if not permitted or permitted[0]['id']!=event_id: raise LookupError('Event not found in this scope')
    row=work.query('SELECT details_json,prev_hash,current_hash FROM audit_logs WHERE id=?',(event_id,))[0]
    row['details']=redact(json.loads(row.pop('details_json') or '{}'))
    return jsonify(success=True,event={**permitted[0],**row})


def csv_cell(value):
    s='' if value is None else str(value)
    return "'"+s if s.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else s


def event_export_payload(scope,args):
    params=dict(args);params['limit']=100;params.pop('cursor',None)
    first=work.event_page(scope,params)
    if first['total']>10000: raise ValueError('Export exceeds 10,000 events; narrow the event dates or resource filter')
    records=list(first['items']);cursor=first['next_cursor'];params['snapshot']=first['snapshot']
    while cursor:
        params['cursor']=cursor
        page=work.event_page(scope,params);records+=page['items'];cursor=page['next_cursor']
    return {'schema_version':work.VERSION,'kind':'audit_events','scope':scope,'head_id':first['head_id'],
            'total':first['total'],'records':records,'redacted':True,'record_sha256':work.digest(records)}


@auditor_bp.route('/api/v2/auditor/exports',methods=['POST'])
@require_roles('admin','auditor')
def prepare_export():
    data=body();s=scope();kind=data.get('kind')
    if kind=='events': payload=event_export_payload(s,{**dict(request.args),**data})
    elif kind=='project': payload=work.dossier(work.text(data.get('project_id'),'project ID',1,150),s,redacted=True)
    elif kind=='evaluation':
        items=evaluation.results(s)['items'];payload=next((r for r in items if r['job_id']==data.get('job_id') and r['status']=='completed'),None)
        if not payload: raise LookupError('Completed evaluation not found')
    else: raise ValueError('Export kind must be events, project or evaluation')
    return jsonify(success=True,**actions.save_export(kind,payload,s))


@auditor_bp.route('/api/v2/auditor/exports/<sid>')
@require_roles('admin','auditor')
def download_export(sid):
    rows=work.query('SELECT * FROM auditor_snapshots WHERE snapshot_id=? AND owner_id=? AND role=?',(sid,str(g.current_user['id']),g.current_user['role']))
    if not rows: raise LookupError('Export not found for this user')
    row=rows[0];payload=json.loads(row['payload_json']);s=scope()
    saved=json.loads(row['scope_json']);allowed=s.get('_allowed_states')
    if s.get('pilot_id') and (saved.get('pilot_id')!=s['pilot_id'] or any(s.get(k) and saved.get(k)!=s[k] for k in ('state','district'))):
        raise PermissionError('Export is outside the current pilot assignment')
    if allowed and (saved.get('state') not in allowed and saved.get('_allowed_states')!=allowed): raise PermissionError('Export outside current state clearance')
    if request.args.get('format')=='csv' and row['kind']=='events':
        out=io.StringIO();writer=csv.writer(out);keys=['id','created_at','actor','action','resource_type','resource_id','event_version','chain_seq']
        writer.writerow(keys)
        for record in payload['records']: writer.writerow([csv_cell(record.get(k)) for k in keys])
        return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename="{sid}.csv"','X-NVB-Manifest-SHA256':row.get('payload_sha256') or work.digest(payload)})
    manifest = {
        'schema_version': work.VERSION,
        'sha256': row.get('payload_sha256') or work.digest(payload),
        'created_at': row['created_at'],
        'external_signature': None,
    }
    return jsonify(success=True, manifest=manifest, record=payload)


@auditor_bp.route('/api/v2/auditor/exports/<sid>/verify')
def verify_export(sid):
    """Public digest check; returns metadata only, never the evidence payload."""
    rows=work.query('SELECT snapshot_id,kind,created_at,payload_json,payload_sha256,manifest_json FROM auditor_snapshots WHERE snapshot_id=?',(sid,))
    if not rows: raise LookupError('Evidence pack not found')
    row=rows[0];current=work.digest(json.loads(row['payload_json']))
    stored=row.get('payload_sha256')
    manifest=json.loads(row.get('manifest_json') or '{}')
    verified=bool(stored) and stored==current
    return jsonify(success=True,verified=verified,status='verified' if verified else ('legacy_digest_not_stored' if not stored else 'tamper_detected'),
                   snapshot_id=row['snapshot_id'],kind=row['kind'],created_at=row['created_at'],payload_sha256=current,
                   stored_payload_sha256=stored,manifest=manifest,
                   notice='Public integrity check only. It does not certify publisher authenticity, legal compliance, or administrative approval.')


@auditor_bp.route('/api/v2/auditor/integrity')
@require_roles('admin','auditor')
def integrity(): return jsonify(success=True,**verify_chain_integrity())


@auditor_bp.route('/api/v2/auditor/cases')
@require_roles('admin','auditor')
def cases(): return jsonify(success=True,**work.case_list(scope(),work.page_limit(request.args.get('limit')),request.args.get('cursor',''),request.args.get('status',''),request.args.get('kind','')))


@auditor_bp.route('/api/v2/auditor/cases',methods=['POST'])
@require_roles('admin','auditor')
def create_case(): return jsonify(success=True,case=actions.create_case(body(),scope())),201


@auditor_bp.route('/api/v2/auditor/cases/<cid>')
@require_roles('admin','auditor')
def case(cid): return jsonify(success=True,case=work.case_detail(cid,scope()))


@auditor_bp.route('/api/v2/auditor/cases/<cid>/actions',methods=['POST'])
@require_roles('admin','auditor')
def case_action(cid): return jsonify(success=True,case=actions.update_case(cid,body(),scope()))


@auditor_bp.route('/api/v2/auditor/projects/<pid>/evidence',methods=['POST'])
@require_roles('admin','auditor')
def add_evidence(pid): return jsonify(success=True,evidence=actions.add_evidence(pid,body(),scope())),201


@auditor_bp.route('/api/v2/auditor/evidence/<eid>/review',methods=['POST'])
@require_roles('admin','auditor')
def review_evidence(eid): return jsonify(success=True,evidence=actions.review_evidence(eid,body(),scope()))


@auditor_bp.route('/api/v2/auditor/consent')
@require_roles('admin','auditor')
def consent(): return jsonify(success=True,**work.consent_events(scope(),work.page_limit(request.args.get('limit')),int(work.number(request.args.get('cursor',0),'cursor',0,1e12))))


@auditor_bp.route('/api/v2/auditor/consent/actions',methods=['POST'])
@require_roles('admin','auditor')
def consent_action(): return jsonify(success=True,receipt=actions.consent_action(body(),scope())),201


@auditor_bp.route('/api/v2/auditor/security')
@require_roles('admin','auditor')
def security(): return jsonify(success=True,**work.security_feed(scope(),work.page_limit(request.args.get('limit')),int(work.number(request.args.get('cursor',0),'cursor',0,1e12)),request.args.get('severity','')))


@auditor_bp.route('/api/v2/auditor/reviewers')
@require_roles('admin','auditor')
def reviewers():
    from ..services import pilot
    s=pilot.scope()
    return jsonify(success=True,items=pilot.reviewers(s) if s.get('pilot_id') else work.query("SELECT name,role FROM users WHERE role IN ('admin','auditor') ORDER BY name"))


@auditor_bp.route('/api/v2/auditor/evaluations')
@require_roles('admin','auditor')
def evaluations(): return jsonify(success=True,**evaluation.results(scope()))


@auditor_bp.route('/api/v2/auditor/evaluations',methods=['POST'])
@require_roles('admin','auditor')
def evaluate():
    data=body();job=evaluation.enqueue(data,scope())
    if current_app.config.get('LOCAL_EVALUATION_WORKER',True):
        evaluation.start_worker(current_app._get_current_object())
    return jsonify(success=True,**job),202


@auditor_bp.route('/api/v2/auditor/readiness')
@require_roles('admin','auditor')
def readiness():
    return jsonify(success=True,version=work.VERSION,license='Apache-2.0',certification='Not asserted',
        integrations={'financial_holds':'Not connected','satellite_progress':'No validated construction estimator','external_evidence_verifier':'Public digest verification endpoint','external_anchor':'No independent trust anchor or public-key signature configured'},
        external_gates=['Publisher/redistribution verification','Human-reviewed representative language/audio labels','Field outcome observations','Financial authority integration','Independent trust anchor or public-key signature for evidence packs'],
        export_limit=10000,evaluation_sample_limit=500)


@auditor_bp.route('/api/v2/auditor/exchange/validate',methods=['POST'])
@require_roles('admin','auditor')
def validate_exchange():
    from jsonschema import Draft202012Validator, FormatChecker
    data=body()
    path=Path(current_app.root_path).parent/'docs/release/auditor-evidence.schema.json'
    # Minimal test apps can have a different root_path; the shipped schema is a package asset.
    if not path.exists(): path=Path(__file__).resolve().parents[2]/'docs/release/auditor-evidence.schema.json'
    validator=Draft202012Validator(json.loads(path.read_text(encoding='utf-8')),format_checker=FormatChecker())
    errors=list(validator.iter_errors(data))
    if errors: raise ValueError('Invalid evidence pack: '+errors[0].message[:250])
    if work.digest(data['record'])!=data['manifest']['sha256']: raise ValueError('Evidence-pack checksum mismatch')
    return jsonify(success=True,valid=True,persisted=False,record=data['record'],
                   notice='Structure and checksum checked only. Publisher authenticity, legal basis, signatures and administrative approval are not certified.')
