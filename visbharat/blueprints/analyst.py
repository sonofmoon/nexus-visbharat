"""Versioned Analyst API. Scenarios are read-only; drafts use policy_decisions."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request, g, current_app
from ..auth import require_roles
from ..audit import write_audit_log
from ..db import get_db
from ..services import analyst_workbench as work

analyst_bp = Blueprint('analyst_workbench',__name__)


@analyst_bp.errorhandler(PermissionError)
def forbidden(error):
    get_db().rollback()
    return jsonify(success=False,error=str(error)),403


@analyst_bp.errorhandler(ValueError)
def invalid(error):
    return jsonify(success=False,error=str(error)),400


@analyst_bp.errorhandler(LookupError)
def missing(error):
    return jsonify(success=False,error=str(error)),404


def options():
    opts=dict(request.args)
    if request.method=='POST':
        body=request.get_json(silent=True)
        if not isinstance(body,dict): raise ValueError('A JSON object is required')
        opts.update(body)
    if isinstance(opts.get('weights'),str):
        try: opts['weights']=json.loads(opts['weights'])
        except ValueError: raise ValueError('Invalid weights JSON')
    if opts.get('weights') is not None and not isinstance(opts['weights'],dict): raise ValueError('weights must be an object')
    return opts


@analyst_bp.route('/api/v2/analyst/snapshot',methods=['GET','POST'])
@require_roles('admin','analyst','auditor')
def snapshot():
    opts=options(); scope=work.scope_from(opts)
    db=get_db();db.commit()
    db.execute('BEGIN' if db.backend=='sqlite' else 'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    plan=work.scenario(scope,opts)
    limit=work.integer(opts.get('limit'),50,1,200,'limit')
    offset=work.integer(opts.get('offset'),0,0,100000,'offset')
    selected=[p for p in plan['items'] if p['selected']]
    plan['top_projects']=plan['items'][:10]
    search=str(opts.get('search') or '').strip().lower()
    matches=[p for p in plan['items'] if not search or search in ' '.join(str(p.get(k,'')) for k in ('title','state','district','ward','category','project_id')).lower()]
    plan['items']=matches[offset:offset+limit]
    plan['matching_candidates']=len(matches)
    plan['selected_projects']=selected
    plan.update(limit=limit,offset=offset)
    result={'success':True,'stats':work.stats(scope),'scenario':plan,'inclusion':work.inclusion(scope)}
    db.commit()
    return jsonify(result)


@analyst_bp.route('/api/v2/analyst/evidence')
@require_roles('admin','analyst','auditor')
def evidence():
    return jsonify(success=True,**work.evidence(work.scope_from(request.args)))


@analyst_bp.route('/api/v2/analyst/delivery')
@require_roles('admin','analyst','auditor')
def delivery():
    scope=work.scope_from(request.args)
    data=work.responsiveness(scope)
    decisions=[]
    for d in work.query('SELECT decision_id,district,status,source_json,notes,approved_at,estimated_project_cost_lakh FROM policy_decisions ORDER BY created_at DESC'):
        src=json.loads(d.pop('source_json') or '{}')
        from ..services.pilot import decision_allowed
        if not decision_allowed(src,scope): continue
        if scope['district'] and d['district']!=scope['district']: continue
        if any(scope[k] and src.get('candidate',src.get('scope',{})).get(k)!=scope[k] for k in ('state','category','ward')): continue
        d['project_id']=src.get('project_id');d['review']=src.get('engineering_review'); decisions.append(d)
    return jsonify(success=True,**data,decisions=decisions[:100],decisions_total=len(decisions))


@analyst_bp.route('/api/v2/analyst/outcomes')
@require_roles('admin','analyst','auditor')
def outcomes():
    return jsonify(success=True,**work.outcomes(work.scope_from(request.args),request.args.get('delivery_at'),request.args.get('days',28),request.args.get('project_id'),request.args.get('cluster_id')))


@analyst_bp.route('/api/v2/analyst/projects/<project_id>')
@require_roles('admin','analyst','auditor')
def project(project_id):
    return jsonify(success=True,**work.project_detail(project_id,work.scope_from(request.args)))


@analyst_bp.route('/api/v2/analyst/projects/<project_id>/export')
@require_roles('admin','analyst','auditor')
def export_project(project_id):
    opts=options();scope=work.scope_from(opts)
    detail=work.project_detail(project_id,scope)
    plan=work.scenario(scope,opts)
    candidate=next((p for p in plan['items'] if p['project_id']==project_id),None)
    if not candidate: raise ValueError('No active planning candidate in this scope; use the project evidence endpoint for historical records')
    record={'schema_version':work.VERSION,'project_id':project_id,'geography':candidate['geography'],'scenario_id':plan['scenario_id'],
            'request_ids':detail['capital_request_ids'],'status':'approved' if candidate['committed'] else ('reviewed' if candidate.get('engineering_review') else 'draft'),
            'score':{'value':candidate['priority_score'],'version':work.VERSION,'components':candidate['components']},
            'cost':{'currency':'INR','unit':'lakh','low':candidate['cost_low_lakh'],'high':candidate['cost_high_lakh'],
                    'status':'human_reviewed' if candidate.get('engineering_review') else 'illustrative'},
            'provenance':work.provenance(scope),'decision_id':candidate.get('decision_id'),
            'excluded_emergency_request_ids':detail['excluded_emergency_request_ids']}
    return jsonify(success=True,record=record)


@analyst_bp.route('/api/v2/analyst/exchange/validate',methods=['POST'])
@require_roles('admin','analyst','auditor')
def validate_exchange():
    from jsonschema import Draft202012Validator
    if request.content_length and request.content_length>1024*1024: raise ValueError('Evidence document exceeds 1 MiB')
    body=request.get_json(silent=True)
    schema_path=Path(current_app.root_path).parent/'docs/release/analyst-decision.schema.json'
    schema=json.loads(schema_path.read_text(encoding='utf-8'))
    errors=sorted(Draft202012Validator(schema).iter_errors(body),key=lambda e:str(e.path))
    if errors: raise ValueError('Invalid exchange document: '+errors[0].message[:300])
    if body['cost']['low']>body['cost']['high']: raise ValueError('Cost low must not exceed high')
    return jsonify(success=True,valid=True,record=body,persisted=False,
                   message='Contract validated only; source authenticity, boundary matching and approval were not verified. No local decision created.')


@analyst_bp.route('/api/v2/analyst/projects/<project_id>/draft',methods=['POST'])
@require_roles('admin','analyst')
def draft(project_id):
    opts=options();scope=work.scope_from(opts);plan=work.scenario(scope,opts)
    item=next((p for p in plan['items'] if p['project_id']==project_id),None)
    if not item or not item['selected']: raise ValueError('Select the project in the current budget scenario first')
    notes=str(opts.get('notes') or '').strip()
    if len(notes)<10: raise ValueError('Add a review rationale of at least ten characters')
    key=hashlib.sha256((plan['scenario_id']+project_id).encode()).hexdigest()[:24]
    decision_id='PD-NVB-'+key.upper()
    db=get_db()
    work.lock_policy_decisions()
    existing=db.execute('SELECT decision_id,status FROM policy_decisions WHERE decision_id=?',(decision_id,)).fetchone()
    if existing: return jsonify(success=True,reused=True,decision=dict(existing))
    detail=work.project_detail(project_id,scope)
    source={'version':work.VERSION,'project_id':project_id,'scenario_id':plan['scenario_id'],'scope':scope,'scenario_scope':scope,
            'candidate':item,'request_ids':detail['capital_request_ids'],'excluded_emergency_request_ids':detail['excluded_emergency_request_ids'],
            'engineering_review':item.get('engineering_review'),
            'constraints':{k:plan[k] for k in ('capacity','max_per_district','operating_budget_lakh')},
            'cost_status':'human_reviewed' if item.get('engineering_review') else 'illustrative_not_reviewed','weights':plan['weights'],'data_mode':plan['metadata']['data_mode']}
    now=work.utcnow().isoformat()
    db.execute('''INSERT INTO policy_decisions (decision_id,district,priority_score,estimated_project_cost_lakh,total_budget_lakh,status,notes,source_json,created_by,created_at,updated_at)
        VALUES (?,?,?,?,?,'draft',?,?,?,?,?)''',(decision_id,item['district'],item['priority_score'],item['estimated_cost_lakh'],plan['budget_lakh'],notes,json.dumps(source),g.current_user['name'],now,now))
    db.commit()
    write_audit_log(g.current_user['name'],'analyst_project_drafted','policy',decision_id,{'project_id':project_id,'scenario_id':plan['scenario_id']})
    return jsonify(success=True,reused=False,decision={'decision_id':decision_id,'status':'draft'})


@analyst_bp.route('/api/v2/analyst/decisions/<decision_id>/review',methods=['POST'])
@require_roles('admin')
def review(decision_id):
    opts=options();db=get_db()
    work.lock_policy_decisions()
    row=db.execute('SELECT source_json,status FROM policy_decisions WHERE decision_id=?',(decision_id,)).fetchone()
    if not row: raise LookupError('Decision not found')
    if row['status']!='draft': raise ValueError('Only a draft may be reviewed')
    src=json.loads(row['source_json'])
    from ..services.pilot import authorize_decision
    authorize_decision(src)
    if src.get('version')!=work.VERSION: raise ValueError('Use the original workflow for this legacy decision')
    cost=work.bounded(opts.get('engineering_cost_lakh'),None,.01,1e8,'engineering cost')
    count=work.integer(opts.get('beneficiary_count'),None,1,1e9,'beneficiaries')
    evidence=str(opts.get('evidence_reference') or '').strip()
    if len(evidence)<10: raise ValueError('An engineering and catchment evidence reference is required')
    src['engineering_review']={'cost_lakh':cost,'beneficiary_count':count,'evidence_reference':evidence,
                               'reviewed_by':g.current_user['name'],'reviewed_at':work.utcnow().isoformat()}
    src['cost_status']='human_reviewed'
    db.execute('UPDATE policy_decisions SET source_json=?,estimated_project_cost_lakh=?,updated_at=? WHERE decision_id=?',
               (json.dumps(src),cost,work.utcnow().isoformat(),decision_id));db.commit()
    write_audit_log(g.current_user['name'],'analyst_engineering_review','policy',decision_id,src['engineering_review'])
    return jsonify(success=True,review=src['engineering_review'],status='draft',next_step='Admin approval through the policy decision workflow')


@analyst_bp.route('/api/v2/analyst/brief',methods=['POST'])
@require_roles('admin','analyst','auditor')
def brief():
    opts=options();scope=work.scope_from(opts);plan=work.scenario(scope,opts)
    sources=work.evidence(scope)
    selected=[p for p in plan['items'] if p['selected']]
    # Structured, source-bound brief: no model is permitted to invent numbers.
    claims=[{'text':f"{len(selected)} proposed projects; scenario capital {plan['allocation']['budget_used_lakh']} lakh. See each project's cost basis.",'source':'scenario.allocation'}]
    claims.extend({'text':f"{p['title']} — {p['district']}, {p['ward']}: {p['reports']} reports, {p['issues']} issue assignments.",
                   'source':f"/api/v2/analyst/projects/{p['project_id']}?"+urlencode({k:v for k,v in scope.items() if v}),'project_id':p['project_id']} for p in selected)

    # Reference context retains scope and dates; state expenditure does not
    # establish local eligibility, available funds, or duplicate funding.
    reference_context=[]
    for source in sources['sources']:
        if source['source'].startswith('public_data_'):
            reference_context.append({k:source.get(k) for k in
                ('source','name','url','resource_id','status','granularity','retrieved_at','source_updated_at','sha256','complete','scoped_records','sample')})
    claims.append({'text':'Public reference snapshots provide context only. Confirm geographic compatibility, observation period, scheme eligibility and existing sanctions before approval.',
                   'source':'sources.limitations'})
    plan['reference_context']=reference_context

    plan['items']=selected
    return jsonify(success=True,title='NVB draft investment decision brief',generated_at=work.utcnow().isoformat(),
                   status='For human review; not an allocation order',claims=claims,scenario=plan,sources=sources,
                   assumptions=plan['limitations'],evaluation_required=['Engineering survey','Catchment validation','Scheme eligibility','Independent source verification'])


@analyst_bp.route('/api/v2/analyst/readiness')
@require_roles('admin','analyst','auditor')
def readiness():
    folder=Path(current_app.root_path).parent/'docs'/'evaluation'
    reports={}
    for name in ('quality','load','interoperability'):
        p=folder/(name+'.json')
        reports[name]=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'status':'not_evaluated'}
    return jsonify(success=True,reports=reports,license='Apache-2.0',certification='Not asserted',
                   research={'prediction_markets':'Research fixture retired from decision support','rct':'Evaluation design only; no trial results claimed'})


@analyst_bp.get('/api/v2/analyst/requests/<request_id>/journey')
@require_roles('admin','analyst','auditor')
def request_journey(request_id):
    scope = work.scope_from(request.args)
    clause, params = work.where(scope)
    found = work.query(f"SELECT c.* FROM citizen_requests c WHERE c.request_id=? AND {clause}", [request_id, *params])
    if not found:
        raise LookupError('Request not found in this assignment')
    row = found[0]
    metadata = json.loads(row['ai_metadata_json'] or '{}')
    project_id = work.candidate_id(row['state'], row['district'], row.get('ward') or '', row['category'])
    detail = work.project_detail(project_id, scope)
    clusters = work.query('SELECT cluster_id,similarity_score FROM cluster_members WHERE request_id=?', [request_id])
    steps = {}
    for key in ('classification','translation','stt'):
        result = metadata.get(key) or {}
        if isinstance(result, dict):
            steps[key] = {k:result.get(k) for k in ('model','provider_mode','fallback_used','provider_evidence','confidence_basis','citizen_review','provider_output')}
    decisions = [d for d in detail['decisions'] if request_id in d.get('source',{}).get('request_ids', [])]
    return jsonify(success=True, request={k:row.get(k) for k in
        ('request_id','state','district','ward','category','urgency','input_language','source_channel','original_text','translated_text','status','routed_department')},
        data_mode='synthetic' if metadata.get('is_synthetic') else metadata.get('data_mode', 'unverified_submission'),
        processing=steps, clusters=clusters, project_id=project_id,
        planning_eligible=request_id in detail['capital_request_ids'],
        project_request_count=detail['total_requests'],
        decisions=[{k:d.get(k) for k in ('decision_id','status','approved_at','review')} for d in decisions],
        evidence=detail['evidence'], events=[e for e in detail['events'] if e['request_id']==request_id],
        limitations=['A planning candidate is not an approved investment.',
                     'Only decisions whose saved evidence includes this ticket are linked here.',
                     'Provider execution and synthetic demonstrations do not establish field impact.'])
