"""Scoped, evidence-led Auditor reads. No model calls during rendering."""
from collections import OrderedDict
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import math
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import current_app, g
from itsdangerous import URLSafeTimedSerializer, BadSignature
from ..db import get_db
from . import analyst_workbench as analyst

VERSION = 'nvb-auditor-v1'
now = analyst.utcnow
query = analyst.query
LOCK = threading.RLock()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def text(value, name, minimum=1, maximum=2000):
    if not isinstance(value,str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f'{name} must contain {minimum}–{maximum} characters')
    return value.strip()


def number(value, name, low=0, high=1e9):
    if isinstance(value,bool): raise ValueError(f'{name} must be numeric')
    try: n=float(value)
    except (ValueError,TypeError): raise ValueError(f'{name} must be numeric')
    if not math.isfinite(n) or not low<=n<=high: raise ValueError(f'{name} must be between {low} and {high}')
    return n


def page_limit(value, maximum=100):
    try: n=int(value if value is not None else 50)
    except (ValueError,TypeError): raise ValueError('limit must be an integer')
    if n<1: raise ValueError('limit must be positive')
    return min(n,maximum)


def safe_uri(value):
    uri=text(value,'source reference',5,1000)
    p=urlparse(uri)
    if p.scheme not in ('https','http','gs','ee','urn') or p.username or p.password:
        raise ValueError('Use an http(s), gs, ee or urn evidence reference without credentials')
    return uri


def scope_from(args):
    scope=analyst.scope_from(args)
    for key in ('project_id','request_id','event_from','event_to'):
        scope[key]=str(args.get(key) or '').strip()[:150]
    dates=analyst.scope_from({'date_from':scope['event_from'],'date_to':scope['event_to']})
    scope['event_from'],scope['event_to']=dates['date_from'],dates['date_to']
    # Geographical clearance comes from deployment configuration, never a header.
    user=getattr(g,'current_user',{}) or {}
    allowed=current_app.config.get('AUDITOR_USER_STATES',{}).get(user.get('name'),[])
    if allowed and scope['state'] and scope['state'] not in allowed: raise PermissionError('State outside assigned audit scope')
    scope['_allowed_states']=list(allowed)
    return scope


def citizen_where(scope, alias='c', include_project=True):
    local=dict(scope)
    if include_project and scope.get('project_id'):
        p=resolve_project(scope['project_id'],{**scope,'project_id':''})
        local.update({k:p[k] for k in ('state','district','ward','category')})
    clause,params=analyst.where(local,alias)
    if include_project and scope.get('project_id') and not local['ward']:
        clause+=f" AND COALESCE({alias}.ward,'')=''"
    if scope.get('request_id'):
        clause+=f' AND {alias}.request_id=?';params.append(scope['request_id'])
    if scope.get('_allowed_states'):
        clause+=f" AND {alias}.state IN ({','.join('?' for _ in scope['_allowed_states'])})";params+=scope['_allowed_states']
    return clause,params


def event_dates(scope, column):
    clause='';params=[]
    if scope.get('event_from'): clause+=f' AND {column}>=?';params.append(scope['event_from']+'T00:00:00')
    if scope.get('event_to'):
        end=analyst.parse_time(scope['event_to'])+timedelta(days=1)
        clause+=f' AND {column}<?';params.append(end.strftime('%Y-%m-%dT00:00:00'))
    return clause,params


def projects(scope, search='', limit=50, cursor=0):
    clause,params=citizen_where(scope,include_project=False)
    rows=query(f'''SELECT c.state,c.district,COALESCE(c.ward,'') AS ward,c.category,COUNT(*) AS reports,
        SUM(CASE WHEN c.urgency='Emergency' THEN 1 ELSE 0 END) AS emergency_reports
        FROM citizen_requests c WHERE {clause} GROUP BY c.state,c.district,COALESCE(c.ward,''),c.category''',params)
    for r in rows:
        r['project_id']=analyst.candidate_id(r['state'],r['district'],r['ward'],r['category'])
        r['title']=f"{r['district']} · {r['ward'] or 'Ward unspecified'} · {r['category']}"
    rows.sort(key=lambda r:r['project_id'])
    if search:
        rows=[r for r in rows if search.lower() in ' '.join(str(v) for v in r.values()).lower()]
    return {'items':rows[cursor:cursor+limit],'total':len(rows),'next_cursor':cursor+limit if cursor+limit<len(rows) else None}


def resolve_project(project_id,scope):
    rows=projects(scope,limit=100000)['items']
    p=next((r for r in rows if r['project_id']==project_id),None)
    if not p: raise LookupError('Project not found in the authorized scope')
    return p


def meta(scope, total=0, synthetic=0):
    return {'version':VERSION,'scope':scope,'as_of':now().isoformat(),'source':'operational_database',
            'data_mode':'empty' if not total else ('synthetic' if synthetic==total else ('mixed' if synthetic else 'operational')),
            'model_calls':0,'analytics_replica':'BigQuery; no cloud scan required for this operational view',
            'date_semantics':'Intake dates select reports. Event dates select events/observations. Global unlinked events are excluded under report filters.'}


def metric(name,value,unit='count',numerator=None,denominator=None,status='observed',definition=''):
    return dict(name=name,value=value,unit=unit,numerator=numerator,denominator=denominator,status=status,definition=definition,version=VERSION)


def chain_summary():
    h=query('SELECT * FROM auditor_chain_state WHERE id=1')[0]
    return {**h,'status':'not_checked','external_anchor':'Not independently anchored',
            'message':'Run verification to check content, sequence and completeness. Historical unsigned events remain unverified.'}


def summary(scope):
    head=chain_summary()
    key=digest([VERSION,scope,(g.current_user or {}).get('id'),head['head_seq']])
    cache=current_app.extensions.setdefault('auditor_cache',OrderedDict())
    with LOCK:
        cached=cache.get(key)
        if cached and time.monotonic()-cached[0]<15:
            out=deepcopy(cached[1]);out['cache_hit']=True;return out
    clause,params=citizen_where(scope)
    counts=query(f'''SELECT COUNT(*) AS total,
        SUM(CASE WHEN REPLACE(c.ai_metadata_json,' ','') LIKE '%"is_synthetic":true%' THEN 1 ELSE 0 END) AS synthetic,
        SUM(CASE WHEN LOWER(c.status) IN ('resolved','closed') THEN 1 ELSE 0 END) AS closed,
        SUM(CASE WHEN c.state='' OR c.district='' OR c.input_language='' OR c.category='' OR c.created_at='' THEN 1 ELSE 0 END) AS incomplete
        FROM citizen_requests c WHERE {clause}''',params)[0]
    counts={k:int(v or 0) for k,v in counts.items()}
    distributions={}
    for key2,col in [('languages','input_language'),('channels','source_channel'),('states','state'),('urgencies','urgency')]:
        rows=query(f"SELECT COALESCE(NULLIF(c.{col},''),'Unknown') AS label,COUNT(*) AS value FROM citizen_requests c WHERE {clause} GROUP BY c.{col} ORDER BY value DESC",params)
        for r in rows: r['percent']=round(100*r['value']/counts['total'],2) if counts['total'] else 0
        distributions[key2]=rows
    consent=consent_coverage(scope)
    cases=case_list(scope,limit=1)
    metadata=meta(scope,counts['total'],counts['synthetic'])
    metrics=[metric('Citizen reports',counts['total'],definition='All reports matching the intake scope'),
             metric('Open review cases',cases['open'],definition='Persisted, unresolved cases in the selected scope'),
             metric('Overdue cases',cases['overdue'],definition='Open cases whose due date has passed'),
             metric('Linked processing receipts',consent['linked_requests'],'requests',consent['linked_requests'],counts['total'],definition='Reports with any recorded purpose/basis receipt; not legal-compliance certification'),
             metric('Required fields complete',round(100*(counts['total']-counts['incomplete'])/counts['total'],2) if counts['total'] else None,'percent',counts['total']-counts['incomplete'],counts['total'],'observed' if counts['total'] else 'insufficient_data',definition='Nonempty state, district, language, category and intake timestamp; authenticity is a separate check')]
    out={'metadata':metadata,'counts':counts,'metrics':metrics,'distributions':distributions,'consent':consent,'integrity':head,
         'snapshot_id':digest([metadata,counts,distributions,head['head_seq']]),'cache_hit':False}
    for item in metrics:
        item['scope']=scope;item['as_of']=metadata['as_of'];item['data_mode']=metadata['data_mode']
    with LOCK:
        cache[key]=(time.monotonic(),deepcopy(out));cache.move_to_end(key)
        while len(cache)>64: cache.popitem(last=False)
    return out


def has_report_filter(scope):
    return any(scope.get(k) for k in (*analyst.FIELDS,'date_from','date_to','project_id','request_id','_allowed_states','pilot_id'))


def case_where(scope, alias='a'):
    conditions=['1=1'];params=[]
    if scope.get('pilot_id'):
        pids=[p['project_id'] for p in projects(scope,limit=100000)['items']]
        members='SELECT jsonb_array_elements_text(?::jsonb)' if get_db().backend=='postgres' else 'SELECT value FROM json_each(?)'
        conditions.append(f'({alias}.request_id IN (SELECT request_id FROM pilot_requests WHERE pilot_id=?) OR {alias}.project_id IN ({members}))')
        params.extend([scope['pilot_id'],json.dumps(pids)])
    for k in ('state','district','category','project_id','request_id'):
        if scope.get(k): conditions.append(f'{alias}.{k}=?');params.append(scope[k])
    if scope.get('_allowed_states'):
        conditions.append(f"{alias}.state IN ({','.join('?' for _ in scope['_allowed_states'])})");params+=scope['_allowed_states']
    # Intake/language/channel/urgency filters only apply when a case links a matching report.
    if any(scope.get(k) for k in ('language','channel','urgency','ward','date_from','date_to')):
        clause,args=citizen_where(scope)
        conditions.append(f'EXISTS(SELECT 1 FROM citizen_requests c WHERE c.request_id={alias}.request_id AND {clause})');params+=args
    date,args=event_dates(scope,alias+'.created_at')
    return ' AND '.join(conditions)+date,params+args


def case_list(scope,limit=50,cursor='',status='',kind=''):
    clause,params=case_where(scope)
    if kind: clause+=' AND a.kind=?';params.append(kind)
    stats=query(f'''SELECT COUNT(*) AS total,SUM(CASE WHEN status<>'resolved' THEN 1 ELSE 0 END) AS open,
        SUM(CASE WHEN status<>'resolved' AND due_at<? THEN 1 ELSE 0 END) AS overdue FROM auditor_cases a WHERE {clause}''',[now().isoformat(),*params])[0]
    if status: clause+=' AND a.status=?';params.append(status)
    if cursor: clause+=' AND a.case_id<?';params.append(cursor)
    rows=query(f'SELECT a.* FROM auditor_cases a WHERE {clause} ORDER BY case_id DESC LIMIT ?',[*params,limit+1])
    return {**{k:int(v or 0) for k,v in stats.items()},'items':rows[:limit],'next_cursor':rows[limit-1]['case_id'] if len(rows)>limit else None,'scope':scope}


def case_detail(case_id,scope):
    clause,params=case_where(scope)
    rows=query(f'SELECT a.* FROM auditor_cases a WHERE a.case_id=? AND {clause}',[case_id,*params])
    if not rows: raise LookupError('Case not found in this scope')
    return {**rows[0],'events':query('SELECT * FROM auditor_case_events WHERE case_id=? ORDER BY id',(case_id,))}


def consent_coverage(scope):
    clause,params=citizen_where(scope)
    total=query(f'SELECT COUNT(*) AS n FROM citizen_requests c WHERE {clause}',params)[0]['n']
    linked=query(f'''SELECT COUNT(*) AS n FROM citizen_requests c WHERE {clause}
        AND EXISTS(SELECT 1 FROM consent_ledger l WHERE l.request_id=c.request_id)''',params)[0]['n']
    return {'total_requests':total,'linked_requests':linked,'missing_receipts':total-linked,
            'status':'receipts_recorded' if linked else 'missing_evidence','notice':'Receipt presence is not proof of lawful processing or current consent.'}


def consent_events(scope,limit=50,cursor=0):
    clause,params=citizen_where(scope)
    condition=f'EXISTS(SELECT 1 FROM citizen_requests c WHERE c.request_id=l.request_id AND {clause})' if has_report_filter(scope) else '1=1'
    args=list(params) if has_report_filter(scope) else []
    date,dp=event_dates(scope,'l.created_at');condition+=date;args+=dp
    if cursor: condition+=' AND l.id<?';args.append(cursor)
    rows=query(f'''SELECT l.*,
        CASE WHEN EXISTS(SELECT 1 FROM citizen_requests c WHERE c.request_id=l.request_id) THEN 1 ELSE 0 END AS linked,
        CASE WHEN NOT EXISTS(SELECT 1 FROM consent_ledger newer WHERE newer.request_id=l.request_id AND newer.consent_scope=l.consent_scope AND newer.id>l.id) THEN 1 ELSE 0 END AS is_current
        FROM consent_ledger l WHERE {condition} ORDER BY l.id DESC LIMIT ?''',[*args,limit+1])
    for r in rows:
        m=json.loads(r.pop('metadata_json') or '{}')
        r.pop('subject_ref',None)
        r['event_state']=m.get('event_state') or ('granted' if r['consent_granted'] else 'declined')
        r['data_mode']='synthetic' if m.get('is_synthetic') else 'origin_unverified'
        r['notice_version']=m.get('consent_text_version')
        r['receipt_reference']=m.get('receipt_reference')
        r['reason']=m.get('reason','')
    return {'items':rows[:limit],'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None,'coverage':consent_coverage(scope),'scope':scope}


def serializer():
    secret=current_app.secret_key or current_app.extensions.setdefault('auditor_cursor_secret',secrets.token_hex(32))
    return URLSafeTimedSerializer(secret,salt=VERSION)


def event_page(scope,args):
    limit=page_limit(args.get('limit'))
    search=str(args.get('search') or '').strip()[:200]
    actor=str(args.get('actor') or '').strip()[:200]
    action=str(args.get('action') or '').strip()[:200]
    owner=str(g.current_user['id'])
    identity={'scope':scope,'search':search,'actor':actor,'action':action,'owner':owner}
    token=args.get('snapshot')
    if token:
        try: frozen=serializer().loads(token,max_age=3600)
        except BadSignature: raise ValueError('Snapshot expired or invalid; refresh the audit trail')
        if any(frozen.get(k)!=v for k,v in identity.items()): raise PermissionError('Snapshot belongs to a different user or scope')
        head=frozen['head']
    else:
        head=query('SELECT COALESCE(MAX(id),0) AS n FROM audit_logs')[0]['n']
        token=serializer().dumps({**identity,'head':head})
    clause='a.id<=?';params=[head]
    if has_report_filter(scope):
        cw,cp=citizen_where(scope)
        # Ticket actions and Auditor project/case actions use actual stable resource IDs.
        project_ids=[p['project_id'] for p in projects(scope,limit=100000)['items']]
        related=[]
        if scope.get('project_id'): project_ids=[scope['project_id']]
        for d in query('SELECT decision_id,source_json FROM policy_decisions'):
            if json.loads(d['source_json'] or '{}').get('project_id') in project_ids: related.append(d['decision_id'])
        cc,cparams=case_where(scope)
        related += [c['case_id'] for c in query(f'SELECT a.case_id FROM auditor_cases a WHERE {cc}',cparams)]
        related+=project_ids
        # Avoid backend parameter limits for large pilot scopes using a bounded JSON
        # string membership parameter with escaped exact resource IDs generated here.
        clause+=f' AND (EXISTS(SELECT 1 FROM citizen_requests c WHERE c.request_id=a.resource_id AND {cw})'
        params+=cp
        if related:
            members='SELECT jsonb_array_elements_text(?::jsonb)' if get_db().backend=='postgres' else 'SELECT value FROM json_each(?)'
            clause+=f' OR a.resource_id IN ({members})';params.append(json.dumps(related))
        clause+=')'
    date,dp=event_dates(scope,'a.created_at');clause+=date;params+=dp
    for col,value in [('actor',actor),('action',action)]:
        if value: clause+=f' AND a.{col}=?';params.append(value)
    if search:
        escaped=search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        clause+=" AND (LOWER(a.actor || ' ' || a.action || ' ' || COALESCE(a.resource_id,'') || ' ' || a.resource_type) LIKE ? ESCAPE '\\')";params.append('%'+escaped.lower()+'%')
    # A declared audit head is immutable under the append-only contract. Reuse its
    # filtered total for paging/export, and coalesce simultaneous identical scans.
    count_key=digest([clause,params])
    totals=current_app.extensions.setdefault('auditor_event_totals',OrderedDict())
    with LOCK:
        if count_key not in totals:
            totals[count_key]=query(f'SELECT COUNT(*) AS n FROM audit_logs a WHERE {clause}',params)[0]['n']
        total=totals[count_key];totals.move_to_end(count_key)
        while len(totals)>256: totals.popitem(last=False)
    cursor=args.get('cursor')
    if cursor:
        c=int(number(cursor,'cursor',1,1e12));clause+=' AND a.id<?';params.append(c)
    rows=query(f'''SELECT a.id,a.created_at,a.actor,a.action,a.resource_type,a.resource_id,a.event_version,
        a.chain_seq FROM audit_logs a WHERE {clause} ORDER BY a.id DESC LIMIT ?''',[*params,limit+1])
    return {'items':rows[:limit],'total':total,'snapshot':token,'head_id':head,'scope':scope,
            'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None,'limit':limit,
            'integrity_notice':'A hash-linked event requires verification; legacy entries remain unverified.'}


def evidence_rows(project_id,scope):
    clause,args=event_dates(scope,'observed_at')
    rows=query('SELECT * FROM auditor_evidence WHERE project_id=?'+clause+' ORDER BY observed_at DESC,evidence_id',[project_id,*args])
    for r in rows: r['metadata']=json.loads(r.pop('metadata_json'))
    return rows


def dossier(project_id,scope,redacted=False):
    p=resolve_project(project_id,scope)
    local={**scope,**{k:p[k] for k in ('state','district','ward','category')}}
    detail=analyst.project_detail(project_id,local)
    assets=evidence_rows(project_id,scope)
    decisions=[]
    for d in query('SELECT decision_id,status,source_json,created_by,approved_by,approved_at FROM policy_decisions ORDER BY created_at'):
        src=json.loads(d.pop('source_json') or '{}')
        if src.get('project_id')==project_id:
            decisions.append({**d,'scenario_id':src.get('scenario_id'),'score_version':src.get('version'),
                              'weights':src.get('weights'),'engineering_review':src.get('engineering_review'),
                              'candidate':src.get('candidate')})
    checks={'source_tickets':bool(detail['request_ids']),'engineering_review':any(d['engineering_review'] for d in decisions),
            'human_decision':any(d['status'] in ('approved','funded') for d in decisions),
            'verified_reference':any(e['kind']=='source' and e['status']=='human_reviewed' and e['data_mode']=='operational' for e in assets)}
    nodes=[{'id':rid,'kind':'citizen_report'} for rid in detail['capital_request_ids']]
    nodes += [{'id':project_id,'kind':'proposal'},*[{'id':d['decision_id'],'kind':'decision'} for d in decisions],*[{'id':e['evidence_id'],'kind':e['kind']} for e in assets]]
    edges=[{'from':rid,'to':project_id,'relationship':'supports'} for rid in detail['capital_request_ids']]
    edges += [{'from':project_id,'to':d['decision_id'],'relationship':'reviewed_in'} for d in decisions]
    edges += [{'from':e['evidence_id'],'to':project_id,'relationship':'evidence_for'} for e in assets]
    if redacted:
        detail['requests']=[];detail['request_ids']=[];detail['capital_request_ids']=[];detail['events']=[]
        detail['excluded_emergency_request_ids']=[]
        detail['decisions']=[]
        nodes=[n for n in nodes if n['kind']!='citizen_report'];edges=[e for e in edges if e['relationship']!='supports']
        # Public packs expose counts, checks and identifiers, never submitted notes,
        # document URLs, names, or free-text metadata without a publication review.
        assets=[{k:e[k] for k in ('evidence_id','kind','status','data_mode','observed_at')} for e in assets]
        decisions=[{k:d[k] for k in ('decision_id','status','scenario_id','score_version')} for d in decisions]
        for source in detail['evidence']['sources']:
            source.pop('file',None)
    return {'project':p,'detail':detail,'decisions':decisions,'evidence':assets,'readiness':checks,
            'ready':all(checks.values()),'nodes':nodes,'edges':edges,'redacted':redacted,
            'metadata':{'version':VERSION,'scope':scope,'as_of':now().isoformat(),
                        'notice':'File digests and human review are distinct from publisher authentication and legal approval.'}}


def financial_review(project_id,scope):
    p=resolve_project(project_id,scope);assets=evidence_rows(project_id,scope)
    rows=[]
    for e in assets:
        if e['kind'] not in ('milestone','payment','site'): continue
        m=e['metadata']
        rows.append({'evidence_id':e['evidence_id'],'kind':e['kind'],'milestone':m.get('milestone'),
            'period':m.get('period'),'status':e['status'],'data_mode':e['data_mode'],
            'sanctioned_lakh':m.get('sanctioned_lakh'),'released_lakh':m.get('released_lakh'),
            'spent_lakh':m.get('spent_lakh'),'certified_progress_pct':m.get('certified_progress_pct'),
            'observed_progress_pct':m.get('observed_progress_pct'),'observed_at':e['observed_at']})
    discrepancies=[]
    for m in rows:
        if m['certified_progress_pct'] is None or not m['milestone'] or not m['period']: continue
        for s in rows:
            if s['observed_progress_pct'] is not None and s['milestone']==m['milestone'] and s['period']==m['period'] and s['data_mode']==m['data_mode']:
                delta=round(m['certified_progress_pct']-s['observed_progress_pct'],2)
                discrepancies.append({'claim':m['evidence_id'],'observation':s['evidence_id'],'difference_pp':delta,
                    'status':'review_required' if delta else 'no_difference_observed','data_mode':m['data_mode'],
                    'meaning':'Difference between same-milestone progress observations; not a fraud finding.'})
    return {'project':p,'records':rows,'discrepancies':discrepancies,'frozen_capital_lakh':None,
            'financial_action':'No financial-system integration; review cases cannot freeze or approve funds.',
            'satellite_status':'Satellite indices are contextual proxies; construction progress requires suitable site evidence.'}


def outcome_review(project_id,scope):
    p=resolve_project(project_id,scope);assets=evidence_rows(project_id,scope)
    measures=[e for e in assets if e['kind']=='outcome' and e['status']=='human_reviewed']
    pairs=[]
    for before in measures:
        a=before['metadata']
        if a.get('phase')!='before': continue
        for after in measures:
            b=after['metadata']
            if b.get('phase')!='after' or any(a.get(k)!=b.get(k) for k in ('measure','unit','catchment')) or before['data_mode']!=after['data_mode']: continue
            if analyst.parse_time(after['observed_at'])<=analyst.parse_time(before['observed_at']): continue
            pairs.append({'measure':a['measure'],'unit':a['unit'],'before':a['value'],'after':b['value'],
                          'change':b['value']-a['value'],'before_n':a['sample_size'],'after_n':b['sample_size'],
                          'source_ids':[before['evidence_id'],after['evidence_id']],
                          'status':'illustrative' if before['data_mode']=='synthetic' else 'observed_association',
                          'causal_claim':False,'confidence_interval':None})
    protocols=[e for e in assets if e['kind']=='evaluation_protocol']
    return {'project':p,'observations':pairs,'status':'observed_association' if pairs else 'insufficient_data',
            'readiness':{'reviewed_observations':bool(pairs),'protocol_attached':bool(protocols),
                         'causal_method_independently_validated':False},'causal_claim':False,
            'message':'Service measurements can show change. Causal attribution requires an independently reviewed study, valid comparisons and uncertainty.'}


def security_feed(scope,limit=50,cursor=0,severity=''):
    clause='1=1';params=[]
    if has_report_filter(scope):
        cw,cp=citizen_where(scope);clause=f'EXISTS(SELECT 1 FROM citizen_requests c WHERE c.request_id=a.resource_id AND {cw})';params+=cp
    date,dp=event_dates(scope,'a.occurred_at');clause+=date;params+=dp
    if severity: clause+=' AND a.severity=?';params.append(severity)
    total=query(f'SELECT COUNT(*) AS n FROM auditor_detections a WHERE {clause}',params)[0]['n']
    if cursor: clause+=' AND a.id<?';params.append(cursor)
    rows=query(f'SELECT a.* FROM auditor_detections a WHERE {clause} ORDER BY id DESC LIMIT ?',[*params,limit+1])
    return {'items':rows[:limit],'total':total,'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None,
        'coverage':[{'detector':'authentication_and_role_denials','status':'instrumented','scope':'This application'},
                    {'detector':'webhook_signatures_and_replay','status':'see_connector_logs'},
                    {'detector':'external_tokens_and_infrastructure','status':'not_connected'}],
        'notice':'No detections observed does not establish absence of threats. Global detections are excluded by report geography filters.'}
