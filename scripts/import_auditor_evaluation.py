"""Recalculate recorded Analyst predictions; does not call any model provider."""
import os
import json
from pathlib import Path
import sys
os.environ['NVB_DISABLE_EXTERNAL_SERVICES']='1'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from flask import g
from visbharat import create_app
from visbharat.db import get_db
from visbharat.services import auditor_workbench as work, auditor_evaluation as evaluation

source=json.loads((ROOT/'docs/evaluation/quality.json').read_text(encoding='utf-8'))
data={'dataset_version':source['dataset']+':recorded-predictions','model_version':', '.join(sorted({r['model'] for r in source['predictions']})),
      'label_status':'provisional','source_artifact':'docs/evaluation/quality.json',
      'samples':[{'language':r['language'],'expected_category':r['expected_category'],'predicted_category':r['category'],
                  'expected_urgency':r['expected_urgency'],'predicted_urgency':r['urgency']} for r in source['predictions']]}
app=create_app()
with app.test_request_context():
    g.current_user=dict(get_db().execute("SELECT id,name,role FROM users WHERE role='auditor' ORDER BY id LIMIT 1").fetchone())
    existing=next((r for r in work.query('SELECT job_id,input_json FROM auditor_jobs') if json.loads(r['input_json']).get('dataset_version')==data['dataset_version']),None)
    job={'job_id':existing['job_id']} if existing else evaluation.enqueue(data,work.scope_from({}))
evaluation.run_pending(app)
with app.app_context():
    row=work.query('SELECT job_id,status,result_json FROM auditor_jobs WHERE job_id=?',(job['job_id'],))[0]
    row['result']=json.loads(row.pop('result_json'))
    (ROOT/'docs/evaluation/auditor-quality.json').write_text(json.dumps(row,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'status':row['status'],'samples':row['result']['overall']['n'],'category_macro_f1':row['result']['overall']['category_macro_f1'],'label_status':row['result']['label_status']}))
