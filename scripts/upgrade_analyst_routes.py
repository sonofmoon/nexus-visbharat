"""Apply the reviewed legacy API compatibility migration once."""
import ast
from pathlib import Path

p=Path('visbharat/blueprints/api.py');s=p.read_text(encoding='utf-8')
replacements={
'stats': '''    from ..services.analyst_workbench import stats as scoped_stats, scope_from
    data=scoped_stats(scope_from(request.args))
    return jsonify(success=True,stats=data,categories=data['categories'],daily_trend=data['daily_trend'],source='operational_database')''',
'priority_projects': '''    from ..services.analyst_compat import priorities
    return jsonify(priorities(request.args))''',
'get_social_priority_score_rankings': '''    from ..services.analyst_compat import priorities
    return jsonify(priorities(request.args))''',
'geo_layers': '''    from ..services.analyst_compat import geo
    return jsonify(geo(request.args))''',
'hotspots': '''    from ..services.analyst_compat import geo
    return jsonify(geo(request.args))''',
'policy_priority_rankings_secure': '''    from ..services.analyst_compat import rankings
    return jsonify(rankings(request.args))''',
'policy_impact_brief_secure': '''    from ..services.analyst_compat import rankings
    data=rankings(request.args)
    data['impact_metrics']={'funding_coverage_ratio':data['funded_count']/max(data['total_ranked'],1),'population_covered_estimate':None,'human_capital_npv_earnings_lakh':None}
    data['auto_brief']={'summary':'Draft scenario only. Benefits require catchment, baseline and engineering review.','human_capital_roi':data['outcomes']}
    return jsonify(data)''',
'policy_human_capital_roi_secure': '''    return jsonify(success=True,human_capital_roi={'status':'not_estimated','npv_earnings_uplift_lakh':None,'human_capital_irr':None,'reason':'No validated intervention cost, baseline, catchment or earnings cash-flow model.'})''',
'analyst_district_responsiveness_secure': '''    from ..services.analyst_workbench import responsiveness,scope_from
    data=responsiveness(scope_from(request.args))
    return jsonify(success=True,league_table=data['items'],total_districts_evaluated=len(data['items']),metadata=data['metadata'],definitions=data['definitions'])''',
'analyst_demand_surface': '''    from ..services.analyst_workbench import inclusion,scope_from
    data=inclusion(scope_from(request.args))
    return jsonify(success=True,surface={'raw_surface':data['items'],'latent_need_surface':[],'summary':{'total_districts_analyzed':len(data['items']),'corrected_red_zones_count':0,'max_vai_multiplier':None},'status':'Unvalidated latent inference retired; use observed access-risk screening'},inclusion=data)''',
'policy_impact_decay_secure': '''    from ..services.analyst_workbench import outcomes,scope_from
    data=outcomes(scope_from(request.args),request.args.get('anchor_at'),request.args.get('post_days',28),request.args.get('project_id'),request.args.get('cluster_id'))
    return jsonify(success=True,**data)''',
'get_prediction_markets': '''    return jsonify(success=True,projects=[],count=0,status='research_only',notice='Fixture probabilities retired from policy decisions. No model success probabilities asserted.')''',
'post_prediction_stake': '''    return jsonify(success=False,error='Research staking is retired; use a documented expert review.'),410''',
'get_super_prediction_detail': '''    return jsonify(success=False,error='Uncalibrated fixture predictions are retired.'),410''',
'get_rct_experiments': '''    return jsonify(success=True,count=0,experiments=[],status='design_only',notice='No live trial outcomes. Preregister a reviewed evaluation before collecting trial data.')''',
'get_rct_telemetry': '''    return jsonify(success=True,status='not_evaluated',high_freq_telemetry={},late_causal_lift_pct=None,mab_status_label='No adaptive rollout decisions')''',
'policy_brief': '''    import html
    from ..services.analyst_workbench import stats as scoped_stats,scope_from
    scope=scope_from(request.args);scope['district']=district
    data=scoped_stats(scope)
    summary=f"{data['total_complaints']} requests; {data['emergency_count']} emergencies in the selected scope."
    brief=f"<h4>{html.escape(district)} decision evidence</h4><p>{html.escape(summary)}</p><p>Open Budget Scenarios for a project-specific, cited decision brief. No unsupported benefit estimate is generated.</p>"
    return jsonify(success=True,brief=brief,summary=summary,citations=[{'source':'operational_database','scope':scope,'as_of':data['metadata']['as_of']}],ai_mode='deterministic_evidence_summary')''',
}
nodes={n.name:n for n in ast.parse(s).body if isinstance(n,ast.FunctionDef)}
lines=s.splitlines(keepends=True)
for name,body in sorted(replacements.items(),key=lambda entry:nodes[entry[0]].lineno,reverse=True):
    n=nodes[name]
    # All these handlers have a single-line function signature.
    lines[n.lineno:n.end_lineno]=[body+'\n']
s=''.join(lines)
# Require a reviewed estimate for the new project-linked workflow; legacy drafts retain their existing contract.
old="row = db.execute('SELECT decision_id, status FROM policy_decisions WHERE decision_id = ?', (decision_id,)).fetchone()"
pos=s.index(old,s.index('def approve_policy_decision_secure'))
s=s[:pos]+s[pos:].replace(old,"row = db.execute('SELECT decision_id, status, source_json FROM policy_decisions WHERE decision_id = ?', (decision_id,)).fetchone()",1)
anchor="    db.execute(\n        '''\n        UPDATE policy_decisions"
pos=s.index(anchor,s.index('def approve_policy_decision_secure'))
guard='''    source=json.loads(row['source_json'] or '{}')
    if source.get('version') == 'nvb-decision-v1' and not source.get('engineering_review'):
        return jsonify(success=False,error='Engineering cost and catchment review are required before approval'),400

'''
s=s[:pos]+guard+s[pos:]
p.write_text(s,encoding='utf-8')

p=Path('static/js/dashboard.js');s=p.read_text(encoding='utf-8')
s=s.replace("return document.querySelector('[data-subtab=\"analyst-futures\"]')?.click();","return window.NVBAnalyst?.selectTab('projects');")
s=s.replace("const text = ok ? 'Live' : 'Fallback';","const text = ok ? 'Configured' : 'Not configured';")
s=s.replace("['#b91c1c', 'High Demand, Low Spend (Critical Gap)']","['#b91c1c', 'High service coverage gap (reference)']").replace("['#16a34a', 'Spend Ahead of Demand']","['#16a34a', 'Lower service coverage gap']")
s=s.replace('Critical Demand (Top quartile)','High reporting density (>50/100k)').replace('Highest Public Spend','Highest approved cost estimates')
s=s.replace('Concerned Ward:','Geographic scope:').replace("h.ward || 'Ward 01 (Primary Cluster)'","h.ward || 'District aggregate'")
s=s.replace('Investment (INR Lakh):','Approved estimates (INR lakh, all dates):').replace('Alignment Gap:','Reference service gap:').replace('Hotspot Score:','Reports per 100,000:')
s=s.replace('<strong>Predicted Risk:</strong> ${(h.predicted_risk * 100).toFixed(0)}%','<strong>Forecast:</strong> Not estimated')
p.write_text(s,encoding='utf-8')
p=Path('static/js/dashboard-ui.js');s=p.read_text(encoding='utf-8').replace('    setStats(stats) {','    setSnapshot(update) { publish({ ...update, updatedAt: new Date() }); },\n    setStats(stats) {')
s=s.replace('Share of requests resolved','Resolved or Closed / scoped requests').replace('Supported languages','Languages represented')
p.write_text(s,encoding='utf-8')
print('Canonical compatibility routes installed')
