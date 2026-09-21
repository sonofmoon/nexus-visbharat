"""Record artifact integrity without reading or exporting any credentials."""
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path

root=Path(__file__).resolve().parents[1]
paths=[
    'docs/evaluation/quality.json','docs/evaluation/baseline-quality.json','docs/evaluation/review-pack.json',
    'docs/evaluation/load.json','docs/evaluation/interoperability.json',
    'docs/release/analyst-decision.schema.json','docs/ANALYST_IMPLEMENTATION_STATUS.md',
    'scratch/analyst-implementation/browser/results.json','scratch/analyst-implementation/regression-final.log',
    'scratch/jury-demo-v2/live-verification.json','visbharat/services/analyst_workbench.py',
    'visbharat/services/analyst_compat.py','visbharat/blueprints/analyst.py',
    'templates/analyst_workbench.html','static/js/analyst-workbench.js','static/css/analyst-workbench.css',
    'tests/test_analyst_workbench.py','tests/test_dashboard_deployment.py',
]
files=[]
for path in paths:
    p = root / path
    if p.exists() and p.is_file():
        data = p.read_bytes()
        files.append({'path':path,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
report={'recorded_at':datetime.now(timezone.utc).isoformat(),'algorithm':'sha256','files':files,
        'meaning':'Local artifact integrity record; not a signature, external authentication or human validation.'}
(root/'docs/evaluation/evidence-manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'files_recorded':len(files),'manifest':'docs/evaluation/evidence-manifest.json'}))
