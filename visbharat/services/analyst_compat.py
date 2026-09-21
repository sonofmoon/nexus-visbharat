"""Compatibility payloads for the overview, using the canonical Analyst model."""
from . import analyst_workbench as work


def rankings(args):
    options=dict(args)
    if isinstance(options.get('weights'),str):
        import json
        options['weights']=json.loads(options['weights'])
    p=work.scenario(work.scope_from(args),options)
    limit=int(work.bounded(args.get('limit'),25,1,500,'limit'))
    rows=[{**r,'funded_in_draft_plan':r['selected'],'complaints':r['reports'],
           'base_priority_score':r['priority_score'],'cosign_boost':0,
           'population':r['district_population'],'estimated_project_cost_lakh':r['estimated_cost_lakh'],
           'score_components':r['components'],'score_explainability':{'formula':p['formula'],'weights':p['weights'],'normalized':r['components'],'raw':r['components']}}
          for r in p['items'][:limit]]
    return {'success':True,'items':rows,'total_ranked':p['total_candidates'],'funded_count':p['allocation']['selected_count'],
            'total_budget_lakh':p['budget_lakh'],'budget_used_lakh':p['allocation']['budget_used_lakh'],
            'budget_remaining_lakh':p['allocation']['budget_remaining_lakh'],'metadata':p['metadata'],
            'scenario_id':p['scenario_id'],'delta':p['delta'],'outcomes':p['outcomes']}


def priorities(args):
    scope=work.scope_from(args)
    rows=work.candidates(scope)
    limit=int(work.bounded(args.get('limit'),10,1,500,'limit'))
    projects=[]
    for p in rows[:limit]:
        projects.append({**p,'project_title':p['title']+' — '+p['district']+' '+p['ward'],
                         'social_priority_score':p['priority_score'],'sps_rank':p['rank'],
                         'sub_indices':p['components'],'policy_recommendation':{
                             'estimated_cost':f"INR {p['cost_low_lakh']}–{p['cost_high_lakh']} lakh (illustrative)",
                             'implementing_agency':p['department'],'beneficiary_count':None,
                             'fiscal_scheme_fit':'; '.join(p['schemes']),'expected_impact':'Field baseline and catchment review required'}})
    return {'success':True,'ranked_projects':projects,'projects':projects,'version':work.VERSION,'scope':scope}


def geo(args):
    scope=work.scope_from(args); clause,params=work.where(scope)
    counts=work.query(f'''SELECT c.state,c.district,COUNT(*) AS n,SUM(CASE WHEN c.urgency='Emergency' THEN 1 ELSE 0 END) AS emergencies
        FROM citizen_requests c WHERE {clause} GROUP BY c.state,c.district''',params)
    ref=work.reference_index(); priorities=work.candidates(scope)
    district_scores={}
    for p in priorities:
        k=(p['state'],p['district']);district_scores[k]=max(district_scores.get(k,0),p['priority_score'])
    layer=args.get('layer','demand');items=[]
    if layer not in ('demand','gap','priority','spend'): raise ValueError('Invalid map layer')
    commitments={}
    for d in work.query("SELECT district,SUM(estimated_project_cost_lakh) AS total FROM policy_decisions WHERE status IN ('approved','funded') GROUP BY district"):
        commitments[d['district']]=d['total']
    for c in counts:
        r=ref.get((c['state'],c['district']),{}); population=float(r.get('population') or 0)
        density=100000*c['n']/population if population else 0
        coverages=[float(r[k]) for k in ('water_coverage','road_coverage','electricity_coverage') if r.get(k) is not None]
        gap=100-sum(coverages)/len(coverages) if coverages else None
        priority=district_scores.get((c['state'],c['district']),0)
        spend=float(commitments.get(c['district'],0))
        score={'demand':density,'gap':gap or 0,'priority':priority,'spend':spend}[layer]
        items.append({'state':c['state'],'district':c['district'],'lat':r.get('lat',0),'lng':r.get('lng',0),
                      'complaint_count':c['n'],'emergency_count':c['emergencies'],'layer':layer,'layer_score':score,
                      'investment_lakh':spend,'investment_type':'Approved estimates; not expenditure; district-wide, all dates',
                      'alignment_gap':gap,'hotspot_score':density,'priority_score':priority,
                      'deprivation_index':r.get('deprivation_index'),'predicted_risk':None,'predicted_next_quarter':None,
                      'score_explainability':{'demand_component':density,'gap_component':gap,'spend_component':spend},
                      'label':'Observed screening indicators; not a forecast'})
    items.sort(key=lambda r:(-r['layer_score'],r['district']))
    return {'success':True,'layer':layer,'items':items,'hotspots':items,'scope':scope,'version':work.VERSION}
