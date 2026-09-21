"""Isolated local UI preview; no external services or existing application data."""
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from visbharat import create_app
from visbharat.services import pilot

if __name__=='__main__':
    folder=root/'scratch'/'portal-preview'
    folder.mkdir(parents=True,exist_ok=True)
    app=create_app({'DATABASE_URL':'','DATABASE_PATH':str(folder/'preview.db'),'DEMO_MODE':True,
        'SEED_DEMO_DATA':False,'AUTO_MIGRATE':True,'DISABLE_EXTERNAL_SERVICES':True,
        'SECRET_KEY':'local-portal-preview-only','PILOT_ID':pilot.PILOT_ID,'PILOT_ONLY':True,
        'PILOT_MODEL_CALLS':False,'ADMIN_API_TOKEN':'preview-admin','ANALYST_API_TOKEN':'preview-analyst',
        'AUDITOR_API_TOKEN':'preview-auditor'})
    with app.app_context():pilot.create_default()
    app.run(host='127.0.0.1',port=5082,debug=False)
