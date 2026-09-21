"""Canonical, scoped decision support. No inferred benefits or causal claims.

Operational SQL is authoritative for this workspace; BigQuery is its analytics
replica. Reference files are inputs, never automatically authenticated evidence.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, g, has_app_context

from ..db import get_db
from ..demo_scenarios import PROJECT_RESPONSES

from . import public_data


def get_official_ulb_population(state, district, ulb_name=None):
    city = ulb_name or district
    result = public_data.summary('ulb', {'city':city},
        {'population':'population', 'gender_women_pct':'women_pct', 'sc_pct':'sc_pct', 'st_pct':'st_pct'})
    if state != 'Tamil Nadu':
        result.update(population=None, gender_women_pct=None, sc_pct=None, st_pct=None, records=[], matched_count=0)
    return {**result, 'state':state, 'district':district, 'ulb_name':city,
            'population_scope':'urban_local_body', 'use':'Population context only; not project beneficiaries'}


def get_official_lgd_crosswalk(state, district, pincode=None):
    result = public_data.select('lgd', state=state, district=district, pincode=pincode)
    result.update(state_lgd_code=None, district_lgd_code=None, local_body_lgd_code=None,
                  local_body_name=None, local_body_type=None, mapping_validated=False,
                  boundary_status='LGD crosswalk requires a current, exact directory match')
    if result['complete'] and result['status'] in ('retrieved','cached') and result['records']:
        codes = {(str(public_data.value('lgd',r,'state_code') or ''), str(public_data.value('lgd',r,'district_code') or ''))
                 for r in result['records']}
        if len(codes)==1 and all(x.isdigit() for x in next(iter(codes))):
            result['state_lgd_code'],result['district_lgd_code']=next(iter(codes))
            result.update(mapping_validated=True, boundary_status='Exact directory match; boundary polygons and historical crosswalk not verified')
        if pincode and len(result['records'])==1:
            row=result['records'][0]
            result.update(local_body_lgd_code=public_data.value('lgd',row,'local_body_code'),
                          local_body_name=public_data.value('lgd',row,'city'),
                          local_body_type=public_data.value('lgd',row,'local_body_type'))
    return result


def get_official_vellore_sanitation_coverage(block=None, panchayat=None):
    result=public_data.summary('sbm', {'district':'Vellore','block':block,'panchayat':panchayat},
                              {'ihhl_target':'target','ihhl_achieved':'achieved'})
    target,ach=result['ihhl_target'],result['ihhl_achieved']
    return {**result,'block':block,'gram_panchayat':panchayat,
            'coverage_pct':round(ach/target*100,2) if target and ach is not None else None}


def get_official_jjm_fiscal_status(state):
    result=public_data.summary('jjm_funds',{'state':state},
        {'available_fund_cr':'available','reported_utilization_cr':'utilized','state_share_expenditure_cr':'state_share'})
    available,used=result['available_fund_cr'],result['reported_utilization_cr']
    return {**result,'state':state,'fy':public_data.config('PUBLIC_DATA_OBSERVATION_PERIODS',{}).get('jjm_funds'),
            'utilization_pct':round(used/available*100,2) if available and used is not None else None,
            'use':'Historical state context; not a project allocation or uncommitted balance'}


def get_official_tirupati_amrut_projects(state='Andhra Pradesh',city='Tirupati'):
    result=public_data.select('amrut',state=state,city=city)
    rows=result['records']
    ids=[public_data.value('amrut',r,'project_id') for r in rows]
    costs=[public_data.number(public_data.value('amrut',r,'cost')) for r in rows]
    valid=bool(rows) and result['complete'] and result['status'] in ('retrieved','cached') and all(ids) and len(set(ids))==len(ids) and all(c is not None for c in costs)
    return {**result,'project_count':len(rows) if valid else None,
            'total_sanctioned_cr':round(sum(costs),2) if valid else None,
            'use':'Reported city projects; proposal overlap and current sanction require officer review'}


def get_official_tirupati_rural_jjm(mandal='Tirupati (Rural)'):
    result=public_data.select('jjm_rural',state='Andhra Pradesh',district='Tirupati',mandal=mandal)
    return {**result,'total_habitations':None,'fhtc_reported':None,
            'use':'Inspect dated habitation records; no current coverage or reliability inferred'}


def get_official_ndap_indicators(state_name=None,state_lgd_code=None):
    filters={'state':state_name,'state_code':state_lgd_code,
             'year':public_data.config('PUBLIC_DATA_OBSERVATION_PERIODS',{}).get('ndap')}
    return public_data.summary('ndap', filters,
        {'projects_count':'projects','area_protected_ha':'area','population_benefited':'beneficiaries','state_code':'state_code'})


VERSION = 'nvb-decision-v1'
WEIGHTS = {'demand': 0.40, 'equity': 0.35, 'gap': 0.25}
COSTS = {'Water Supply': (60, 180), 'Road': (80, 240), 'Sanitation': (35, 105),
         'Electricity': (25, 75), 'Health': (100, 300), 'Education': (50, 150),
         'Transport': (20, 60), 'Digital Connectivity': (15, 45), 'Housing': (100, 300), 'Other': (25, 75)}
OUTCOMES = {'Water Supply': 'Hours of reliable water supply per day', 'Road': 'Travel time to essential services (minutes)',
            'Sanitation': 'Days of waterlogging per month', 'Electricity': 'Outage hours per month',
            'Health': 'Travel time to primary care (minutes)', 'Education': 'School attendance days per term',
            'Transport': 'Scheduled services operated per day', 'Digital Connectivity': 'Service availability (%)',
            'Housing': 'Homes meeting the approved safety standard', 'Other': 'Service availability against an agreed baseline'}
FIELDS = ('state', 'district', 'category', 'urgency', 'language', 'channel', 'ward')


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def scope_from(args):
    scope = {key: str(args.get(key) or '').strip() for key in FIELDS}
    for key in ('date_from', 'date_to'):
        value = str(args.get(key) or '').strip()
        if value:
            try:
                parsed = datetime.strptime(value, '%Y-%m-%d')
                if parsed.strftime('%Y-%m-%d') != value:
                    raise ValueError('Noncanonical date')
            except ValueError:
                raise ValueError(f'{key} must be YYYY-MM-DD')
        scope[key] = value
    if scope['date_from'] and scope['date_to'] and scope['date_from'] > scope['date_to']:
        raise ValueError('date_from must not be after date_to')
    if has_app_context():
        from .pilot import resolve_scope
        return resolve_scope(scope,args)
    return scope


def where(scope, alias='c'):
    conditions, params = [], []
    columns = {'language': 'input_language', 'channel': 'source_channel'}
    for key in FIELDS:
        if scope.get(key):
            conditions.append(f'{alias}.{columns.get(key, key)} = ?')
            params.append(scope[key])
    if scope.get('date_from'):
        conditions.append(f'{alias}.created_at >= ?')
        params.append(scope['date_from'] + 'T00:00:00')
    if scope.get('date_to'):
        end = datetime.strptime(scope['date_to'], '%Y-%m-%d') + timedelta(days=1)
        conditions.append(f'{alias}.created_at < ?')
        params.append(end.strftime('%Y-%m-%dT00:00:00'))
    from .pilot import citizen_clause
    pilot_clause,pilot_params=citizen_clause(scope,alias)
    return (' AND '.join(conditions) or '1=1')+pilot_clause, params+pilot_params


def query(sql, params=()):
    return [dict(row) for row in get_db().execute(sql, params).fetchall()]


def provenance(scope):
    cache = getattr(g, 'analyst_provenance_cache', {})
    key = tuple(sorted(scope.items()))
    if key in cache:
        return cache[key]
    clause, params = where(scope)
    row = query(f'''SELECT COUNT(*) AS n, MAX(c.created_at) AS as_of,
        SUM(CASE WHEN REPLACE(c.ai_metadata_json, ' ', '') LIKE '%"is_synthetic":true%' THEN 1 ELSE 0 END) AS synthetic
        FROM citizen_requests c WHERE {clause}''', params)[0]
    n, syn = row['n'], int(row['synthetic'] or 0)
    result = {'source': 'operational_database', 'analytics_replica': 'BigQuery (not queried for this snapshot)',
            'as_of': row['as_of'], 'calculated_at': utcnow().isoformat(), 'scope': scope, 'version': VERSION,
            'data_mode': 'empty' if not n else ('synthetic' if syn == n else ('mixed' if syn else 'operational')),
            'synthetic_requests': syn, 'operational_requests': n-syn,
            'quality': 'Synthetic events demonstrate workflow; operational records still require verification.'}
    cache[key] = result
    g.analyst_provenance_cache = cache
    return result


def metric(name, value, unit, meta, numerator=None, denominator=None, assumptions=None):
    return {'name': name, 'value': value, 'unit': unit, 'numerator': numerator, 'denominator': denominator,
            'assumptions': assumptions or [], **meta}


def stats(scope):
    clause, params = where(scope)
    row = query(f'''SELECT COUNT(*) AS total_complaints,
        COUNT(DISTINCT c.state) AS states_covered, COUNT(DISTINCT c.district) AS districts_covered,
        COUNT(DISTINCT c.input_language) AS languages_supported,
        SUM(CASE WHEN LOWER(c.status) IN ('closed','resolved') THEN 1 ELSE 0 END) AS resolved,
        SUM(CASE WHEN c.urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count
        FROM citizen_requests c WHERE {clause}''', params)[0]
    for key in ('resolved', 'emergency_count'):
        row[key] = int(row[key] or 0)
    row['resolution_rate'] = round(100*row['resolved']/row['total_complaints'], 2) if row['total_complaints'] else 0
    for key, column in [('categories','category'), ('urgencies','urgency'), ('channels','source_channel'), ('languages','input_language')]:
        row[key] = {r['label']: r['n'] for r in query(f'SELECT c.{column} AS label, COUNT(*) AS n FROM citizen_requests c WHERE {clause} GROUP BY c.{column} ORDER BY c.{column}', params)}
    daily = query(f"SELECT SUBSTR(c.created_at,1,10) AS day, COUNT(*) AS n FROM citizen_requests c WHERE {clause} GROUP BY SUBSTR(c.created_at,1,10) ORDER BY day", params)
    trend = {r['day']: r['n'] for r in daily}
    if daily or (scope.get('date_from') and scope.get('date_to')):
        start = parse_time(scope.get('date_from') or daily[0]['day'])
        end = parse_time(scope.get('date_to') or daily[-1]['day'])
        if (end-start).days > 3660:
            raise ValueError('Select a date window of at most ten years')
        row['daily_trend'] = {(start+timedelta(days=i)).strftime('%Y-%m-%d'): trend.get((start+timedelta(days=i)).strftime('%Y-%m-%d'),0) for i in range((end-start).days+1)}
    else:
        row['daily_trend'] = {}
    row['distinct_issues'] = query(f'''SELECT COUNT(DISTINCT COALESCE(m.cluster_id,c.request_id)) AS n
        FROM citizen_requests c LEFT JOIN (SELECT request_id, MIN(cluster_id) AS cluster_id FROM cluster_members GROUP BY request_id) m
        ON c.request_id=m.request_id WHERE {clause}''', params)[0]['n']
    meta = provenance(scope)
    row['metadata'] = meta
    row['metrics'] = [metric('Requests',row['total_complaints'],'reports',meta),
                      metric('Distinct issue assignments',row['distinct_issues'],'clusters or unassigned tickets',meta,
                             assumptions=['Synthetic cluster assignments are not evaluated AI deduplication.']),
                      metric('Operational closure',row['resolution_rate'],'percent',meta,row['resolved'],row['total_complaints'])]
    return row


def reference_index():
    repo = current_app.extensions['reference_repo']
    return {(str(r['state']),str(r['district'])): r.to_dict() for _,r in repo.df_districts.iterrows()}


def evidence_sources():
    from .layer4_fusion import SOURCE_KEYS, SOURCE_METADATA, _source_path
    sources = []
    for key in SOURCE_KEYS:
        p = Path(_source_path(key))
        loaded = p.is_file()
        rows = []
        if loaded:
            try:
                if p.suffix.lower() == '.csv':
                    with p.open(encoding='utf-8-sig', newline='') as handle:
                        rows = list(csv.DictReader(handle))
                else:
                    raw = json.loads(p.read_text(encoding='utf-8'))
                    rows = raw if isinstance(raw,list) else list(raw.values())
            except (OSError, ValueError):
                loaded = False
        meta = SOURCE_METADATA.get(key,{})
        sources.append({'source':key,'name':meta.get('name',key),'publisher':meta.get('publisher'),
                        'url':meta.get('url'),'file':str(p) if loaded else None,'loaded':loaded,'records':len(rows),
                        'sha256':hashlib.sha256(p.read_bytes()).hexdigest() if loaded else None,
                        'status':'unverified_reference' if loaded else 'missing', 'publisher_verified':False,
                        'observation_year':2011 if key in ('secc','census') else None,
                        'license_status':'Verify publisher and redistribution terms before reuse',
                        'boundary_status':'Name-based join; LGD code/crosswalk validation required',
                        'rows':rows})
    sources.extend(public_data.registry())
    return sources


def evidence(scope):
    sources = evidence_sources()
    allowed = None
    if scope.get('pilot_id'):
        allowed = {(r['state'], r['district']) for r in query('SELECT DISTINCT state,district FROM pilot_locations WHERE pilot_id=?', (scope['pilot_id'],))}
    scoped = []
    for source in sources:
        records = [r for r in source.pop('rows') if (not scope.get('state') or r.get('state')==scope['state']) and (not scope.get('district') or r.get('district')==scope['district'])]
        if allowed is not None:
            records = [r for r in records if (r.get('state'), r.get('district')) in allowed or (not r.get('district') and any(s == r.get('state') for s, _ in allowed))]
        scoped.append({**source,'scoped_records':len(records),'sample':records[:5]})
    return {'sources':scoped, 'loaded_sources':sum(s['loaded'] for s in sources),'verified_sources':0,
            'scope':scope,'limitations':['File integrity is recorded; publisher authenticity has not been independently verified.',
            'Old census geography and current district boundaries require an explicit validated crosswalk.',
            'Budget outlays describe reference plans, not actual expenditure or project-specific available funding.']}


def validate_weights(values=None):
    result = dict(WEIGHTS)
    if values is not None and not isinstance(values,dict):
        raise ValueError('weights must be an object')
    if values:
        if set(values)-set(WEIGHTS):
            raise ValueError('Unknown scoring component')
        for key,value in values.items():
            try:
                value = float(value)
            except (TypeError,ValueError):
                raise ValueError('Weights must be numbers between zero and one')
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('Weights must be finite values between zero and one')
            result[key] = value
    total = sum(result.values())
    if total <= 0:
        raise ValueError('At least one scoring weight must be positive')
    return {k:v/total for k,v in result.items()}


def candidate_id(state,district,ward,category):
    return 'NVB-P-' + hashlib.sha256(json.dumps([state,district,ward,category],ensure_ascii=False).encode()).hexdigest()[:16].upper()


def geography(state, district):
    lgd = get_official_lgd_crosswalk(state, district)
    if lgd.get('mapping_validated'):
        return {
            'state_code': f'LGD-S-{lgd.get("state_lgd_code")}',
            'district_code': f'LGD-D-{lgd.get("district_lgd_code")}',
            'code_system': 'MoPR-LGD-v1',
            'lgd_code': lgd.get('district_lgd_code'),
            'geographic_level': 'district',
            'boundary_validation': lgd['boundary_status'],
            'local_body_name': lgd.get('local_body_name'),
            'local_body_type': lgd.get('local_body_type'),
        }
    return {
        'state_code': 'NVB-S-' + hashlib.sha256(state.encode()).hexdigest()[:8].upper(),
        'district_code': 'NVB-D-' + hashlib.sha256((state + '|' + district).encode()).hexdigest()[:12].upper(),
        'code_system': 'NVB-local-v1',
        'lgd_code': None,
        'boundary_validation': 'LGD crosswalk required; local codes are not official codes',
    }


def candidates(scope, weights=None):
    clause, params = where(scope)
    # Keep retained audit records inspectable while honoring explicit withdrawal
    # or refusal in subsequent planning. This does not assert an external deletion.
    clause += " AND NOT EXISTS (SELECT 1 FROM auditor_processing_restrictions pr WHERE pr.request_id=c.request_id AND pr.purpose IN ('request_processing','policy_analytics') AND pr.state='restricted')"
    clause += " AND NOT EXISTS (SELECT 1 FROM pilot_requests pr WHERE pr.request_id=c.request_id AND pr.processing_status NOT IN ('ready','human_reviewed'))"
    rows = query(f'''SELECT c.state,c.district,COALESCE(c.ward,'') AS ward,c.category,
        COUNT(*) AS reports,COUNT(DISTINCT COALESCE(m.cluster_id,c.request_id)) AS issues,
        MIN(c.routed_department) AS department,
        SUM(CASE WHEN LOWER(c.status) NOT IN ('resolved','closed') THEN 1 ELSE 0 END) AS active_reports
        FROM citizen_requests c LEFT JOIN (SELECT request_id,MIN(cluster_id) AS cluster_id FROM cluster_members GROUP BY request_id) m
        ON c.request_id=m.request_id WHERE {clause} AND c.urgency <> 'Emergency'
        GROUP BY c.state,c.district,COALESCE(c.ward,''),c.category''', params)
    references = reference_index()
    decisions = project_decisions(scope)
    weights = validate_weights(weights)
    items = []
    for r in rows:
        if not r['active_reports']:
            continue
        ref = references.get((r['state'],r['district']),{})
        population = float(ref.get('population') or 0)
        density = r['issues'] / population * 100000 if population else None
        coverage_key = {'Water Supply':'water_coverage','Road':'road_coverage','Electricity':'electricity_coverage'}.get(r['category'])
        coverage = ref.get(coverage_key) if coverage_key else None
        components = {'demand':100*density/(density+10) if density is not None else None,
                      'equity':float(ref['deprivation_index'])*100 if ref.get('deprivation_index') is not None else None,
                      'gap':100-float(coverage) if coverage is not None else None}
        available = sum(weights[k] for k,v in components.items() if v is not None)
        contributions = {k:(weights[k]*v/available if available and v is not None else 0) for k,v in components.items()}
        low,high = COSTS.get(r['category'],COSTS['Other'])
        title, schemes = PROJECT_RESPONSES.get(r['category'],PROJECT_RESPONSES['Other'])
        ulb_data = get_official_ulb_population(r['state'], r['district'])
        items.append({**r,'project_id':candidate_id(r['state'],r['district'],r['ward'],r['category']),
                      'geography':geography(r['state'],r['district']),
                      'title':title,'priority_score':round(sum(contributions.values()),6),
                      'components':components,'contributions':contributions,'weights':weights,
                      'score_status':'screening_only_unverified_reference' if available else 'insufficient_evidence',
                      'missing_components':[k for k,v in components.items() if v is None],
                      'demand_per_100k_district_population':density,'district_population':int(population),
                      'cost_low_lakh':low,'cost_high_lakh':high,'estimated_cost_lakh':(low+high)/2,
                      'annual_operating_cost_lakh':round((low+high)/2*.03,2),
                      'cost_basis':'Illustrative category planning envelope; no engineering estimate. Annual operation assumed at 3% of midpoint.',
                      'catchment':r['ward'] or 'Ward not supplied','beneficiaries':None,
                      'beneficiary_status':'survey_required',
                      'population_context':ulb_data if ulb_data.get('population') is not None else None,
                      'review_status':'needs_engineering_and_catchment_review', 'schemes':schemes,
                      'outcome_measure':OUTCOMES.get(r['category'],OUTCOMES['Other']),
                      'outcome_baseline':None,'expected_outcome':None,
                      'alternatives':['Repair and maintain the existing service','Expand or replace the asset after an engineering survey'],
                      'grouping':'Planning bundle by ward and service; an issue cluster is not automatically an approved project.'})
    for item in items:
        decision = decisions.get(item['project_id'])
        item['committed'] = bool(decision and decision['status'] in ('approved','funded'))
        item['decision_id'] = decision['decision_id'] if decision else None
        review = decision['source'].get('engineering_review') if decision else None
        if review:
            cost = float(review['cost_lakh'])
            item.update(cost_low_lakh=cost,cost_high_lakh=cost,estimated_cost_lakh=cost,
                        annual_operating_cost_lakh=round(cost*.03,2),beneficiaries=review['beneficiary_count'],
                        beneficiary_status='human_reviewed_project_count_not_deduplicated',
                        review_status='human_reviewed',engineering_review=review,
                        cost_basis='Human-reviewed capital estimate; annual operation still assumed at 3%. No uncertainty range supplied.')
        if item['committed']:
            item['review_status']='already_committed'
    items.sort(key=lambda r:(-r['priority_score'],r['project_id']))
    for rank,item in enumerate(items,1):
        item['rank']=rank
    return items


def bounded(value, default, low, high, name):
    try:
        val = float(default if value is None or value == '' else value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be a number')
    if not math.isfinite(val) or not low <= val <= high:
        raise ValueError(f'{name} must be between {low} and {high}')
    return val


def integer(value, default, low, high, name):
    val=bounded(value,default,low,high,name)
    if not val.is_integer(): raise ValueError(f'{name} must be a whole number')
    return int(val)


def project_decisions(scope=None):
    from .pilot import decision_allowed
    scope=scope if scope is not None else scope_from({})
    result={}
    for row in query("SELECT decision_id,status,source_json,estimated_project_cost_lakh FROM policy_decisions WHERE status IN ('draft','approved','funded') ORDER BY updated_at DESC,decision_id"):
        source=json.loads(row.pop('source_json') or '{}')
        if not decision_allowed(source,scope): continue
        pid=source.get('project_id')
        if source.get('version')!=VERSION or not pid: continue
        previous=result.get(pid)
        if previous and (previous['status'] in ('approved','funded') or row['status']=='draft'): continue
        result[pid]={**row,'source':source}
    return result


def commitments(scope):
    """Known versioned commitments in this planning geography/service, all intake dates."""
    rows=[]
    for pid,d in project_decisions(scope).items():
        if d['status'] not in ('approved','funded'): continue
        c=d['source']['candidate']
        if any(scope.get(k) and c.get(k)!=scope[k] for k in ('state','district','ward','category')): continue
        rows.append({'project_id':pid,'decision_id':d['decision_id'],'state':c['state'],'district':c['district'],
                     'cost_lakh':float(d['estimated_project_cost_lakh'])})
    return rows


def lock_policy_decisions():
    db=get_db()
    db.execute('BEGIN IMMEDIATE' if db.backend=='sqlite' else 'LOCK TABLE policy_decisions IN EXCLUSIVE MODE')


def check_approval(source,budget):
    review=source.get('engineering_review')
    if not review: raise ValueError('Engineering cost and catchment review are required before approval')
    pid=source['project_id']
    all_decisions=project_decisions()
    if pid in all_decisions and all_decisions[pid]['status'] in ('approved','funded'):
        raise ValueError('This project already has an approved commitment')
    existing=commitments(source.get('scenario_scope',source['scope']))
    capital=sum(r['cost_lakh'] for r in existing)+float(review['cost_lakh'])
    if capital>budget: raise ValueError('Reviewed cost plus existing commitments exceeds the saved capital budget; create a revised scenario')
    constraints=source.get('constraints',{})
    if capital*.03>constraints.get('operating_budget_lakh',1000):
        raise ValueError('Reviewed cost exceeds the saved operating budget assumption')
    if len(existing)>=constraints.get('capacity',10): raise ValueError('Saved delivery capacity is already committed')
    c=source['candidate']
    count=sum(r['state']==c['state'] and r['district']==c['district'] for r in existing)
    if count>=constraints.get('max_per_district',2): raise ValueError('Saved district capacity is already committed')


def allocate(items,budget,capacity,cost_case,equity_share=0,max_per_district=2,operating_budget=1000,existing=()):
    key = {'low':'cost_low_lakh','mid':'estimated_cost_lakh','high':'cost_high_lakh'}[cost_case]
    committed_cost=sum(r['cost_lakh'] for r in existing)
    available_budget=max(0,budget-committed_cost)
    remaining = available_budget
    chosen = []
    district_count = {}
    for r in existing:
        key_d=(r['state'],r['district']);district_count[key_d]=district_count.get(key_d,0)+1
    operating_used = 0
    operating_available=max(0,operating_budget-committed_cost*.03)
    capacity=max(0,capacity-len(existing))
    reserved = available_budget*equity_share
    # Transparent two-pass greedy policy, not an optimality claim.
    for restricted in (True,False):
        for item in items:
            if len(chosen)>=capacity: break
            if item['project_id'] in chosen or item.get('committed'): continue
            cost = item[key]
            district_key=(item['state'],item['district'])
            annual=cost*.03
            if district_count.get(district_key,0)>=max_per_district or operating_used+annual>operating_available: continue
            if item['score_status']=='insufficient_evidence': continue
            if restricted and ((item['components']['equity'] or 0)<50 or cost>reserved): continue
            if cost<=remaining:
                chosen.append(item['project_id']); remaining-=cost
                operating_used+=annual;district_count[district_key]=district_count.get(district_key,0)+1
                if restricted: reserved-=cost
    selected = [r for r in items if r['project_id'] in chosen]
    equity_spend = sum(r[key] for r in selected if (r['components']['equity'] or 0)>=50)
    return {'selected_ids':chosen,'selected_count':len(chosen),'budget_used_lakh':round(available_budget-remaining,2),
            'existing_commitments_lakh':round(committed_cost,2),'existing_commitments_count':len(existing),
            'available_for_new_projects_lakh':round(available_budget,2),
            'commitment_over_budget_lakh':round(max(0,committed_cost-budget),2),
            'budget_remaining_lakh':round(remaining,2),'reports_linked':sum(r['reports'] for r in selected),
            'issue_assignments_linked':sum(r['issues'] for r in selected),
            'beneficiaries':None if selected else 0,
            'project_beneficiary_sum':sum(r.get('beneficiaries') or 0 for r in selected) if selected and all(r.get('beneficiaries') is not None for r in selected) else None,
            'beneficiary_status':'Cross-project overlap must be measured before reporting unique portfolio beneficiaries' if selected else 'No projects selected',
            'annual_operating_cost_lakh':round(operating_used,2),
            'equity_spend_lakh':round(equity_spend,2),'equity_reservation_shortfall_lakh':round(max(0,available_budget*equity_share-equity_spend),2),
            'method':'Two-pass score-ordered greedy allocation with an equity reservation; no optimality guarantee.'}


def scenario(scope, options=None):
    options = options or {}
    budget = bounded(options.get('budget_lakh',options.get('total_budget_lakh')),8500,0,1e8,'budget')
    capacity = integer(options.get('capacity'),10,0,200,'capacity')
    share = bounded(options.get('equity_share'),0,0,1,'equity_share')
    max_per_district=integer(options.get('max_per_district'),2,1,200,'projects per district')
    operating_budget=bounded(options.get('operating_budget_lakh'),1000,0,1e8,'annual operating budget')
    cost_case = options.get('cost_case','mid')
    if cost_case not in ('low','mid','high'): raise ValueError('cost_case must be low, mid or high')
    weights = validate_weights(options.get('weights'))
    items = candidates(scope,weights)
    baseline_items = candidates(scope) if weights != WEIGHTS else [dict(item) for item in items]
    existing=commitments(scope)
    plan = allocate(items,budget,capacity,cost_case,share,max_per_district,operating_budget,existing)
    base = allocate(baseline_items,budget,capacity,'mid',0,max_per_district,operating_budget,existing)
    ranks = {r['project_id']:r['rank'] for r in baseline_items}
    for item in items:
        item['selected'] = item['project_id'] in plan['selected_ids']
        item['rank_change'] = ranks[item['project_id']] - item['rank']
    selected = set(plan['selected_ids']); before = set(base['selected_ids'])
    stable = {'scope':scope,'weights':weights,'budget_lakh':budget,'capacity':capacity,'equity_share':share,'cost_case':cost_case,'max_per_district':max_per_district,'operating_budget_lakh':operating_budget,
              'commitments':existing,
              'candidates':[(r['project_id'],r['priority_score'],r['reports'],r['issues'],r['estimated_cost_lakh'],r['committed']) for r in items]}
    return {'scenario_id':hashlib.sha256(json.dumps(stable,sort_keys=True).encode()).hexdigest()[:20],
            'scope':scope,'metadata':provenance(scope),'weights':weights,'formula':'Weighted mean of available demand-density, deprivation and service-gap indicators (0–100). Missing components are disclosed; weights renormalize.',
            'budget_lakh':budget,'capacity':capacity,'equity_share':share,'cost_case':cost_case,
            'max_per_district':max_per_district,'operating_budget_lakh':operating_budget,
            'total_candidates':len(items),'items':items,'allocation':plan,'baseline':base,'commitments':existing,
            'delta':{'newly_selected':sorted(selected-before),'removed':sorted(before-selected),
                     'ranks_changed':sum(r['rank_change']!=0 for r in items),
                     'budget_used_lakh':round(plan['budget_used_lakh']-base['budget_used_lakh'],2),
                     'reports_linked':plan['reports_linked']-base['reports_linked']},
            'sensitivity':{case:allocate(items,budget,capacity,case,share,max_per_district,operating_budget,existing) for case in ('low','mid','high')},
            'outcomes':{'planned_projects':len(selected),'monetary_npv':None,'irr':None,
                        'projected_beneficiaries':0 if not selected else (plan.get('beneficiaries') or None),
                        'status':'No projects selected' if not selected else 'Catchment surveys and cross-project deduplication required for unique beneficiary estimates'},
            'limitations':['Costs are illustrative unless a recorded human review replaces them; annual operation remains an assumption.',
                          'Known versioned commitments consume this planning envelope across all intake dates/languages/channels. Legacy district estimates are not reconciled to project commitments; this is not a treasury ledger.',
                          'Emergency response is excluded from capital selection and remains in the execution queue.',
                          'Reference indicators and scheme eligibility need verification; human approval is required.']}


def project_detail(project_id,scope):
    # Resolve both active candidates and completed bundles, without inventing a project.
    clause,params=where(scope)
    groups=query(f"SELECT DISTINCT c.state,c.district,COALESCE(c.ward,'') AS ward,c.category FROM citizen_requests c WHERE {clause}",params)
    found=next((g for g in groups if candidate_id(g['state'],g['district'],g['ward'],g['category'])==project_id),None)
    if not found: raise LookupError('Project not found in this scope')
    local={**scope,**found}
    clause,params=where(local)
    if not found['ward']:
        clause += " AND COALESCE(c.ward,'') = ''"
    rows=query(f'''SELECT c.request_id,c.original_text,c.translated_text,c.input_language,c.source_channel,c.status,c.urgency,
        c.created_at,c.ai_metadata_json,c.routed_department,m.cluster_id FROM citizen_requests c
        LEFT JOIN (SELECT request_id,MIN(cluster_id) AS cluster_id FROM cluster_members GROUP BY request_id) m ON m.request_id=c.request_id
        WHERE {clause} ORDER BY c.created_at DESC''',params)
    ids=[r['request_id'] for r in rows]
    capital_ids=[r['request_id'] for r in rows if r['urgency']!='Emergency']
    for r in rows:
        meta=json.loads(r.pop('ai_metadata_json') or '{}')
        r['is_synthetic']=bool(meta.get('is_synthetic'))
        r['cluster_basis']=meta.get('cluster_assignment','Operational clustering; requires review')
    events=query(f'''SELECT e.request_id,e.to_status,e.created_at,e.reason FROM request_lifecycle_events e
        JOIN citizen_requests c ON c.request_id=e.request_id WHERE {clause} ORDER BY e.created_at DESC,e.id DESC LIMIT 100''',params)
    decisions=[]
    for d in query('SELECT decision_id,status,source_json,approved_at,notes FROM policy_decisions ORDER BY created_at DESC'):
        src=json.loads(d.pop('source_json') or '{}')
        if src.get('project_id')==project_id:
            d['review']=src.get('engineering_review')
            d['source']=src
            decisions.append(d)
    refs=evidence(local)
    ulb_data = get_official_ulb_population(found['state'], found['district'])
    review = next((d['review'] for d in decisions if d.get('review')), None)
    surveyed_beneficiaries = review['beneficiary_count'] if review else None
    return {'project_id':project_id,**found,'geography':geography(found['state'],found['district']),'scope':scope,'requests':rows[:100],'request_ids':ids,
            'capital_request_ids':capital_ids,'excluded_emergency_request_ids':list(set(ids)-set(capital_ids)),
            'total_requests':len(rows),'request_preview_limit':100,'events':events,'decisions':decisions,
            'evidence':refs,'beneficiaries':surveyed_beneficiaries,
            'population_context':ulb_data if ulb_data.get('population') is not None else None,
            'catchment_status':'Human-reviewed project catchment; independent validation still required' if review else 'Beneficiary count requires a recorded catchment survey',
            'actual_expenditure_lakh':None,'citizen_outcomes':'No independent field measurement attached'}


def responsiveness(scope):
    clause,params=where(scope)
    rows=query(f'''SELECT c.state,c.district,c.request_id,c.urgency,c.created_at,c.status,c.sla_due_at,
        a.ack_at,z.resolved_at,f.rating,f.reopen_triggered FROM citizen_requests c
        LEFT JOIN (SELECT request_id,MIN(created_at) AS ack_at FROM request_lifecycle_events
        WHERE to_status='Acknowledged' GROUP BY request_id) a ON a.request_id=c.request_id
        LEFT JOIN (SELECT request_id,MIN(created_at) AS resolved_at FROM request_lifecycle_events
        WHERE to_status IN ('Resolved','Closed') GROUP BY request_id) z ON z.request_id=c.request_id
        LEFT JOIN closure_feedback f ON f.request_id=c.request_id WHERE {clause}''',params)
    buckets={}; now=utcnow()
    for r in rows:
        key=(r['state'],r['district'])
        b=buckets.setdefault(key,{'state':key[0],'district':key[1],'requests':0,'eligible':0,'on_time':0,'closed':0,'reopened':0,'feedback':0,'positive_feedback':0,'ack_hours':[],'resolution_days':[],'open_age_days':[],'by_urgency':{}})
        b['requests']+=1
        start=parse_time(r['created_at']); due=parse_time(r['sla_due_at']); ack=parse_time(r['ack_at']); resolved=parse_time(r['resolved_at'])
        eligible=bool(due and (due<=now or (ack and ack<=now)))
        timely=bool(eligible and ack and ack<=due)
        b['eligible']+=int(eligible); b['on_time']+=int(timely)
        b['closed']+=int(r['status'].lower() in ('closed','resolved'))
        b['reopened']+=int(r['status']=='Reopened')
        if r['rating'] is not None:
            b['feedback']+=1; b['positive_feedback']+=int(r['rating']>=4 and not r['reopen_triggered'])
        if start and ack and start<=ack<=now: b['ack_hours'].append((ack-start).total_seconds()/3600)
        if start and resolved and start<=resolved<=now: b['resolution_days'].append((resolved-start).total_seconds()/86400)
        if start and r['status'].lower() not in ('closed','resolved'): b['open_age_days'].append(max(0,(now-start).total_seconds()/86400))
        u=b['by_urgency'].setdefault(r['urgency'],{'requests':0,'eligible':0,'on_time':0}); u['requests']+=1; u['eligible']+=int(eligible); u['on_time']+=int(timely)
    out=[]
    for b in buckets.values():
        for field in ('ack_hours','resolution_days','open_age_days'):
            values=sorted(b.pop(field)); b['median_'+field]=round(statistics.median(values),2) if values else None
            b['p90_'+field]=round(values[max(0,math.ceil(.9*len(values))-1)],2) if values else None
        b['coverage_pct']=round(100*b['on_time']/b['eligible'],2) if b['eligible'] else None
        b['closure_pct']=round(100*b['closed']/b['requests'],2)
        b['citizen_confirmation_pct']=round(100*b['positive_feedback']/b['feedback'],2) if b['feedback'] else None
        out.append(b)
    return {'items':sorted(out,key=lambda r:(r['state'],r['district'])),'metadata':provenance(scope),
            'definitions':{'coverage':'On-time first acknowledgments / requests with a reached response deadline or recorded acknowledgment.',
                           'closure':'Current Resolved or Closed status; not an independently verified service outcome.',
                           'confirmation':'Ratings >=4 without reopen / feedback received; nonresponses are not assumed satisfied.'},
            'limitations':['No composite governance league score; case mix and sample sizes differ.',
                          'Response SLA and capital delivery deadline are different measures.']}


def outcomes(scope,anchor=None,days=28,project_id=None,cluster_id=None):
    days=integer(days,28,7,365,'window days')
    dt=parse_time(anchor)
    if anchor and not dt: raise ValueError('Delivery date must be a valid ISO date or timestamp')
    if project_id:
        detail=project_detail(project_id,scope)
        scope={**scope,**{k:detail[k] for k in ('state','district','ward','category')}}
    if not dt:
        return {'status':'delivery_date_required','scope':scope,'counts':None,'change_pct':None,'causal_claim':False,
                'message':'Select an actual delivery date and a catchment before comparing observed windows.'}
    if not scope.get('district'):
        raise ValueError('Select a district or project catchment')
    # An outcome window is independent of the intake date filter, explicitly disclosed.
    window_scope={**scope,'date_from':'','date_to':''}
    clause,params=where(window_scope)
    if project_id and not scope.get('ward'):
        clause += " AND COALESCE(c.ward,'') = ''"
    if cluster_id:
        if not query(f'SELECT c.request_id FROM citizen_requests c JOIN cluster_members m ON m.request_id=c.request_id WHERE {clause} AND m.cluster_id=? LIMIT 1',[*params,cluster_id]):
            raise LookupError('Cluster not found in this catchment')
        clause+=' AND EXISTS (SELECT 1 FROM cluster_members m WHERE m.request_id=c.request_id AND m.cluster_id=?)'; params.append(cluster_id)
    start=dt-timedelta(days=days); end=dt+timedelta(days=days)
    counts=[]
    for lo,hi in [(start,dt),(dt,min(end,utcnow()))]:
        counts.append(query(f'SELECT COUNT(*) AS n FROM citizen_requests c WHERE {clause} AND c.created_at>=? AND c.created_at<?',[*params,lo.isoformat().replace('+00:00','Z'),hi.isoformat().replace('+00:00','Z')])[0]['n'])
    mature=end<=utcnow()
    change=round(100*(counts[0]-counts[1])/counts[0],2) if mature and counts[0]>0 else None
    return {'status':'observed_association' if mature and counts[0]>0 else ('no_baseline' if mature else 'follow_up_incomplete'),
            'scope':window_scope,'cluster_id':cluster_id,'project_id':project_id,'delivery_at':dt.isoformat(),
            'delivery_verification':'User-supplied date; verify delivery evidence before interpretation',
            'windows':{'pre_start':start.isoformat(),'post_end':end.isoformat(),'days_each':days,
                       'observed_post_days':round(max(0,min(days,(utcnow()-dt).total_seconds()/86400)),2)},
            'counts':{'before':counts[0],'after_observed':counts[1]},'change_pct':change,
            'confidence_interval':None,'causal_claim':False,'metadata':provenance(window_scope),
            'cautions':['Association only: reporting access, seasonality and concurrent work can change request volume.',
                        'No causal estimator or confidence interval is asserted without an appropriate reviewed study.',
                        'Intake date filters are replaced by the displayed outcome windows; all other scope filters apply.']}


def inclusion(scope):
    clause,params=where(scope)
    counts={(r['state'],r['district']):r['n'] for r in query(f'SELECT c.state,c.district,COUNT(*) AS n FROM citizen_requests c WHERE {clause} GROUP BY c.state,c.district',params)}
    rows=[]
    allowed=None
    if scope.get('pilot_id'):
        allowed={(r['state'],r['district']) for r in query('SELECT state,district FROM pilot_locations WHERE pilot_id=?',(scope['pilot_id'],))}
    for (state,district),ref in reference_index().items():
        if allowed is not None and (state,district) not in allowed:continue
        if scope['state'] and scope['state']!=state: continue
        if scope['district'] and scope['district']!=district: continue
        n=counts.get((state,district),0); pop=float(ref.get('population') or 0); dep=ref.get('deprivation_index')
        density=n/pop*100000 if pop else None
        rows.append({'state':state,'district':district,'requests':n,'district_population':int(pop),
                     'reports_per_100k':round(density,3) if density is not None else None,
                     'deprivation_index':dep,'voice_access_index':None,'latent_requests':None,
                     'investigate_access':bool(dep is not None and dep>=.5 and density is not None and density<10),
                     'reason':'Low reporting plus a deprivation proxy suggests a survey; this is not an estimate of hidden demand.',
                     'missing_indicators':['Validated telecom access','Validated literacy/access by channel','Independent reporting propensity survey']})
    rows.sort(key=lambda r:(not r['investigate_access'],-(r['deprivation_index'] or 0),r['district']))
    return {'items':rows,'metadata':provenance(scope),'access_investigation_count':sum(r['investigate_access'] for r in rows),
            'outreach_status':'Planning only. No citizens contacted and no outreach automatically queued.'}
