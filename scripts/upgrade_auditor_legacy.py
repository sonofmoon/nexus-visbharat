"""One-time migration of legacy entry points onto the canonical Auditor services."""
import ast
from pathlib import Path

root=Path(__file__).resolve().parents[1]
path=root/'visbharat/blueprints/api.py'
source=path.read_text(encoding='utf-8');lines=source.splitlines(keepends=True)
replacements={
'list_audit_logs_secure': '''    from ..services import auditor_workbench as audit
    result=audit.event_page(audit.scope_from(request.args),request.args)
    records=result.pop('items')
    # Preserve one established legacy field without repeating every record three times.
    return jsonify(success=True,audit_logs=records,**result)
''',
'export_audit_logs_secure': '''    from .auditor import event_export_payload, csv_cell
    from ..services import auditor_workbench as audit
    payload=event_export_payload(audit.scope_from(request.args),request.args)
    output=io.StringIO();writer=csv.writer(output)
    keys=['id','created_at','actor','action','resource_type','resource_id','event_version','chain_seq']
    writer.writerow(keys)
    for row in payload['records']: writer.writerow([csv_cell(row.get(k)) for k in keys])
    return current_app.response_class(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=nvb_audit_trail.csv','X-NVB-Manifest-SHA256':audit.digest(payload)})
''',
'get_security_alerts_secure': '''    from ..services import auditor_workbench as audit
    result=audit.security_feed(audit.scope_from(request.args),audit.page_limit(request.args.get('limit')))
    return jsonify(success=True,**result)
''',
'get_auditor_ai_telemetry': '''    from ..services import auditor_workbench as audit, auditor_evaluation
    if request.args.get('mode')=='recompute':
        return jsonify(success=False,error='Submit a labelled evaluation job through /api/v2/auditor/evaluations'),410
    result=audit.summary(audit.scope_from(request.args))
    return jsonify(success=True,source='operational_database',generated_at=result['metadata']['as_of'],
        model_backend={'is_model_backed':False,'live':False,'backend':'measured_records','badge_label':'Recorded observations'},
        kpis={'model_drift_score':None,'bias_risk_index':None,'data_quality_score':None,'anomaly_count':None},
        slices=result['distributions'],evaluations=auditor_evaluation.results(audit.scope_from(request.args)),
        notice='Unvalidated heuristic scores retired. Use versioned evaluation results; no model calls during rendering.')
''',
'get_auditor_did_proof_secure': '''    from ..services.causal_matching import compute_did_causal_impact
    return jsonify(success=True,did_proof=compute_did_causal_impact(district=request.args.get('district'),decision_id=request.args.get('decision_id')))
''',
'get_anti_capture_feed': '''    from ..services import auditor_workbench as audit
    scope=audit.scope_from(request.args)
    if scope.get('project_id'):
        result=audit.financial_review(scope['project_id'],scope)
        return jsonify(success=True,projects=[result],count=1)
    result=audit.projects(scope,limit=audit.page_limit(request.args.get('limit')))
    return jsonify(success=True,projects=result['items'],count=result['total'],notice='Select a project to inspect documented financial and site evidence. No automated fraud or financial decisions.')
''',
'dispatch_third_party_audit': '''    from ..services import auditor_actions, auditor_workbench as audit
    data=request.get_json(silent=True) or {}
    result=auditor_actions.create_case(data,audit.scope_from(request.args))
    return jsonify(success=True,case=result,dispatch_status='REVIEW_CASE_CREATED',disbursement_state='NO_FINANCIAL_ACTION'),201
''',
'simulate_anti_capture_divergence': '''    from ..services.anti_capture_triangulation import compute_triangulation_divergence
    data=request.get_json(silent=True) or {}
    result=compute_triangulation_divergence(str(data.get('project_id') or 'illustrative'),data.get('pfms_disbursed_pct',90),
        data.get('satellite_progress_pct',25),data.get('citizen_complaint_pct',85),data.get('gemini_defect_score',.85))
    return jsonify(success=True,triangulation_result=result)
''',
}
tree=ast.parse(source)
for node in sorted([n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in replacements],key=lambda n:n.lineno,reverse=True):
    start=node.body[0].lineno-1
    lines[start:node.end_lineno]=[replacements[node.name]]
source=''.join(lines)
for name in ('get_anti_capture_feed','dispatch_third_party_audit','simulate_anti_capture_divergence'):
    source=source.replace('def '+name+'(',"@require_roles('admin','auditor')\ndef "+name+'(',1)
source=source.replace("consent_granted = (data or {}).get('consent_granted', True)","consent_granted = (data or {}).get('consent_granted')\n    if consent_granted is not None and not isinstance(consent_granted,bool):\n        raise ValueError('consent_granted must be a boolean')")
source=source.replace("'actor_role': actor,","'actor_role': actor,\n        'event_state': 'missing' if consent_granted is None else ('granted' if consent_granted else 'declined'),",1)
path.write_text(source,encoding='utf-8')

path=root/'templates/dashboard.html';s=path.read_text(encoding='utf-8')
start=s.index(' <!-- Auditor Role Suite');end=s.index(' <!-- Admin Role Suite',start)
s=s[:start]+" {% include 'auditor_workbench.html' %}\n\n"+s[end:]
s=s.replace('<link rel="stylesheet" href="/static/css/analyst-workbench.css">','<link rel="stylesheet" href="/static/css/analyst-workbench.css">\n<link rel="stylesheet" href="/static/css/auditor-workbench.css">')
s=s.replace('<script src="/static/js/analyst-workbench.js?v=1"></script>','<script src="/static/js/analyst-workbench.js?v=1"></script>\n <script src="/static/js/auditor-workbench.js?v=1"></script>')
s=s.replace('<div id="adminReportView" class="rbac-view-panel" style="display:none;">','<div id="adminReportView" class="rbac-view-panel" style="display:none;">\n <button class="btn btn-secondary" type="button" onclick="window.NVBAuditor.openForAdmin()">Open audit review tools as Admin</button>')
path.write_text(s,encoding='utf-8')

path=root/'static/js/dashboard.js';s=path.read_text(encoding='utf-8')
# Retire the unused Auditor renderers and formulas, while keeping legacy handlers safe.
for start_name,end_name in [('loadConsentLedger','loadUserManagement'),('loadAuditorDidProof','loadAdminSecurityAlerts'),('loadAntiCaptureTriangulation',None)]:
    start=s.index('async function '+start_name+'(')
    if end_name:
        end=s.index('async function '+end_name+'(',start)
        block=s[start:end]
        import re
        names=re.findall(r'(?:async )?function (\w+)\(',block)
        replacement='\n'.join('function '+n+'(){ window.NVBAuditor?.refresh(); }' for n in names)+'\n\n'
        s=s[:start]+replacement+s[end:]
    else:
        end=s.index('window.dispatchThirdPartyAudit = dispatchThirdPartyAudit;',start)+len('window.dispatchThirdPartyAudit = dispatchThirdPartyAudit;')
        s=s[:start]+"function loadAntiCaptureTriangulation(){ window.NVBAuditor?.refresh(); }\nfunction runAntiCaptureSimulation(){ window.NVBAuditor?.refresh(); }\nfunction dispatchThirdPartyAudit(){ window.NVBAuditor?.refresh(); }\n"+s[end:]
s=s.replace("if (role === 'auditor') {\n if (topStats) topStats.style.display = 'grid';", "if (role === 'auditor') {\n if (topStats) topStats.style.display = 'none';",1)
s=s.replace("setDisplayForSelectors(['#aiRuntimePanel', '.panel-charts'], 'block');","setDisplayForSelectors(['#aiRuntimePanel', '.panel-charts'], 'none');",1)
s=s.replace("function refreshAll() {\n const role = getActiveRole();", "function refreshAll() {\n const role = getActiveRole();\n if (role === 'auditor' || (document.getElementById('auditorReportView')?.style.display === 'block')) { window.NVBAuditor?.refresh(); return; }")
s=s.replace('Auditor Executive Suite - Immutable Audit Trail & Consent Ledger','Auditor workspace — evidence and accountable review')
start=s.index(" } else if (role === 'auditor') {",s.index('function switchRbacRoleView'))
end=s.index(" } else if (role === 'admin')",start)
block=s[start:end]
for line in [" if (typeof loadCharts === 'function') loadCharts();\n",' loadAuditorDidProof();\n',' loadAuditorLogs();\n',' loadConsentLedger();\n'," loadAdminSecurityAlerts('auditorAlertsContainer');\n"]: block=block.replace(line,'')
block+=' window.NVBAuditor?.refresh();\n'
s=s[:start]+block+s[end:]
# Avoid loading invisible Analyst/maps/provider panels when landing in Auditor.
start=s.index(' initMapWithRetry();',s.index('// Initialize on load'));end=s.index(' if (window.NVB_DEMO_TICKET)',start)
s=s[:start]+" if (getActiveRole() !== 'auditor') {\n initMapWithRetry();\n loadStats();\n loadHotspots();\n loadPriorityProjects();\n loadComplaintFeed();\n if (getActiveRole() === 'admin') loadDeliveryHealthPanel();\n loadAiRuntimeStatus();\n deferNonCriticalLoads();\n }\n loadStates();\n loadDistrictsForBrief();\n"+s[end:]
path.write_text(s,encoding='utf-8')
print('Legacy routes and Auditor UI now use the new workspace.')
