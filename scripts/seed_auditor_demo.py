"""Attach labelled, replay-safe demo evidence to existing synthetic requests.

No demand replacement, policy approval, financial instruction or external message.
"""
import json
import os
from pathlib import Path
import sys

os.environ['NVB_DISABLE_EXTERNAL_SERVICES']='1'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from flask import g
from visbharat import create_app
from visbharat.db import get_db
from visbharat.services import auditor_workbench as work, auditor_actions as actions

app=create_app();results=[]
with app.test_request_context():
    if not app.config.get('DEMO_MODE'): raise RuntimeError('Demo mode is required')
    users={r['role']:dict(r) for r in get_db().execute("SELECT id,name,role FROM users WHERE role IN ('admin','auditor') ORDER BY id DESC").fetchall()}
    for state,district in [('Tamil Nadu','Karur'),('Andhra Pradesh','Tirupati'),('Telangana','Nalgonda')]:
        g.current_user=users['admin'];scope=work.scope_from({'state':state,'district':district,'category':'Water Supply'})
        candidates=work.projects(scope)['items']
        if not candidates: continue
        project=candidates[0];pid=project['project_id']
        pscope={**scope,'project_id':pid};cw,params=work.citizen_where(pscope)
        ticket=work.query(f'SELECT c.request_id,c.ai_metadata_json FROM citizen_requests c WHERE {cw} ORDER BY c.request_id LIMIT 1',params)[0]
        if not json.loads(ticket['ai_metadata_json']).get('is_synthetic'): raise RuntimeError('Only existing synthetic tickets may receive demo evidence')
        rid=ticket['request_id'];saved=[]
        examples=[('milestone','Milestone claim — synthetic example',{'milestone':'M1','period':'2026-08','sanctioned_lakh':100,'released_lakh':60,'spent_lakh':45,'certified_progress_pct':80}),
                  ('site','Site observation — synthetic example',{'milestone':'M1','period':'2026-08','observed_progress_pct':30}),
                  ('outcome','Baseline water service — synthetic example',{'measure':'Hours of reliable water supply','unit':'hours/day','catchment':project['ward'],'phase':'before','value':2,'sample_size':25})]
        if state=='Tamil Nadu':
            examples.append(('outcome','Follow-up water service — synthetic example',{'measure':'Hours of reliable water supply','unit':'hours/day','catchment':project['ward'],'phase':'after','value':6,'sample_size':25}))
        for idx,(kind,title,metadata) in enumerate(examples):
            uri=f'urn:nvb:synthetic-auditor-demo:{pid}:{idx}'
            prior=work.query('SELECT evidence_id FROM auditor_evidence WHERE source_uri=?',(uri,))
            if prior: saved.append(prior[0]['evidence_id']);continue
            g.current_user=users['admin']
            e=actions.add_evidence(pid,{'kind':kind,'title':title,'source_uri':uri,'observed_at':'2026-08-01' if metadata.get('phase')=='before' else '2026-09-01',
                'data_mode':'synthetic','metadata':metadata},pscope)
            g.current_user=users['auditor']
            actions.review_evidence(e['evidence_id'],{'status':'human_reviewed','version':1,
                'notes':'Synthetic demo role simulation: reviewed for internal consistency only; no site visit or independent real-world verification occurred.'},pscope)
            saved.append(e['evidence_id'])
        g.current_user=users['admin']
        case=actions.create_case({'title':'Demo: reconcile milestone and site evidence','notes':'Synthetic claim and site readings differ. Request engineering evidence before any conclusion.',
            'kind':'discrepancy','project_id':pid,'request_id':rid,'owner':users['auditor']['name'],'data_mode':'synthetic',
            'idempotency_key':'auditor-demo-v1-'+pid},pscope)
        if not work.query('SELECT event_id FROM consent_ledger WHERE request_id=?',(rid,)):
            actions.consent_action({'request_id':rid,'purpose':'request_processing','event_state':'granted','legal_basis':'consent','notice_version':'demo-v1',
                'receipt_reference':'urn:nvb:synthetic-demo:receipt:'+rid,
                'reason':'Synthetic demo receipt created during setup; this is not a real citizen consent record.'},pscope)
            if state=='Andhra Pradesh':
                actions.consent_action({'request_id':rid,'purpose':'request_processing','event_state':'withdrawn','legal_basis':'consent','notice_version':'demo-v1',
                    'receipt_reference':'urn:nvb:synthetic-demo:withdrawal:'+rid,
                    'reason':'Synthetic demo withdrawal showing a local purpose restriction; no real citizen action is claimed.'},pscope)
                actions.create_case({'title':'Demo: verify processing restriction after withdrawal','notes':'Synthetic withdrawal needs confirmation of downstream restrictions and a documented retention review.',
                    'kind':'rights','project_id':pid,'request_id':rid,'owner':users['auditor']['name'],'data_mode':'synthetic','idempotency_key':'auditor-rights-demo-v1-'+pid},pscope)
        results.append({'state':state,'district':district,'project_id':pid,'request_id':rid,'case_id':case['case_id'],'evidence_ids':saved})
output=ROOT/'docs/evaluation/auditor-demo.json';output.write_text(json.dumps({'data_mode':'synthetic','notice':'Role simulation only; no financial actions or real-world verification','projects':results},indent=2))
print(json.dumps({'projects_prepared':len(results),'artifact':str(output.relative_to(ROOT))}))
