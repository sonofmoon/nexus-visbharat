"""Run three fictional district journeys in a new isolated DB and export evidence.

No provider calls, real messages, government approvals or source-DB changes.
Use --serve to inspect this exact rehearsal locally after generating the bundle.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from visbharat import create_app
from visbharat.db import get_db
from visbharat.security import hash_api_token, token_last4
from visbharat.services import pilot
from visbharat.services.pilot_examples import EXAMPLES


def run(output):
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    if (output/'manifest.json').exists():
        raise ValueError('Use a new output folder to preserve the previous evidence bundle.')
    workspace=ROOT/'scratch'/('judging-'+secrets.token_hex(6))
    workspace.mkdir(parents=True)
    credentials={role:secrets.token_urlsafe(32) for role in ('admin','analyst','auditor')}
    app=create_app({'TESTING':True,'DATABASE_URL':'','DATABASE_PATH':str(workspace/'rehearsal.db'),
        'DEMO_MODE':True,'SEED_DEMO_DATA':False,'AUTO_MIGRATE':True,'DISABLE_EXTERNAL_SERVICES':True,
        'SECRET_KEY':secrets.token_urlsafe(32),'PILOT_ID':pilot.PILOT_ID,'PILOT_ONLY':True,
        'PILOT_MODEL_CALLS':False,'PILOT_ANALYTICS_SYNC':False,'LOCAL_EVALUATION_WORKER':False,
        **{role.upper()+'_API_TOKEN':value for role,value in credentials.items()}})
    headers={role:{'Authorization':'Bearer '+value} for role,value in credentials.items()}
    with app.app_context():
        pilot.create_default();db=get_db()
        for user in pilot.rows('SELECT id FROM users'):
            db.execute('INSERT INTO pilot_memberships VALUES(?,?,?,?,1)',(pilot.PILOT_ID,user['id'],'',''))
        for example in EXAMPLES:
            token=secrets.token_urlsafe(32);name=example['district']+' demo officer'
            db.execute("INSERT INTO users(name,api_token,api_token_hash,token_last4,role,created_at) VALUES(?,?,?,?,'analyst',?)",
                       (name,token,hash_api_token(token),token_last4(token),pilot.now()))
            uid=pilot.rows('SELECT id FROM users WHERE name=?',(name,))[0]['id']
            db.execute('INSERT INTO pilot_memberships VALUES(?,?,?,?,1)',(pilot.PILOT_ID,uid,example['state'],example['district']))
            headers[example['district']]={'Authorization':'Bearer '+token}
        db.commit()
    client=app.test_client()

    def call(method,path,role='analyst',payload=None,expected=200):
        response=getattr(client,method)(path,headers=headers[role],**({'json':payload} if payload is not None else {}))
        if response.status_code!=expected:
            raise RuntimeError(f'{method} {path}: expected {expected}, got {response.status_code}: {response.get_json()}')
        return response.get_json()

    results=[];files=[]
    for example in EXAMPLES:
        received=client.post('/api/v2/pilot/intake',headers={'Idempotency-Key':'judging-'+example['id']},json={
            'location_id':example['location_id'],'language':example['language'],'text':example['text'],
            'category':example['category'],'consent_granted':True})
        if received.status_code!=202:raise RuntimeError('Prepared intake failed')
        rid=received.json['request_id']
        other='Tirupati' if example['district']=='Vellore' else 'Vellore'
        call('post','/api/v2/pilot/requests/'+rid+'/review',other,{'version':1},404)
        call('post','/api/v2/pilot/requests/'+rid+'/review',example['district'],{
            'version':1,'original_text':example['text'],'translated_text':example['translation'],
            'category':example['category'],'urgency':'Routine','status':'Acknowledged',
            'reason':'Automated role rehearsal of a prepared fictional request. No field validation or government approval.'})
        snapshot=call('get','/api/v2/analyst/snapshot?budget_lakh=500&capacity=6')
        project=next(p['project_id'] for p in snapshot['scenario']['items'] if p['district']==example['district'] and p['category']==example['category'])
        draft=call('post','/api/v2/analyst/projects/'+project+'/draft',payload={
            'budget_lakh':500,'capacity':6,'notes':'Synthetic judging rehearsal; costs and beneficiaries are fictional.'})
        did=draft['decision']['decision_id']
        call('post','/api/v2/analyst/decisions/'+did+'/review','admin',{
            'engineering_cost_lakh':20,'beneficiary_count':100,'evidence_reference':'urn:nvb:demo:fictional-survey'})
        call('post','/api/v1/policy/decisions/'+did+'/approve','admin',{})
        # Existing commitments must be recognised in subsequent planning.
        after=call('get','/api/v2/analyst/snapshot?budget_lakh=500&capacity=6')
        committed=next(p for p in after['scenario']['items'] if p['project_id']==project)
        if not committed['committed']:raise RuntimeError('Approved commitment was not recognised')
        export=call('post','/api/v2/auditor/exports','auditor',{'kind':'project','project_id':project})
        dossier=call('get','/api/v2/auditor/exports/'+export['snapshot_id'],'auditor')
        encoded=json.dumps(dossier,ensure_ascii=False,indent=2)
        if received.json['tracking_secret'] in encoded or example['text'] in encoded:
            raise RuntimeError('Dossier failed private receipt / verbatim request redaction check')
        filename=example['id']+'-redacted-dossier.json'
        (output/filename).write_text(encoded,encoding='utf-8')
        files.append({'file':filename,'sha256':hashlib.sha256(encoded.encode('utf-8')).hexdigest()})
        results.append({'example':example['id'],'district':example['district'],'category':example['category'],
            'request_id':rid,'project_id':project,'decision_id':did,'cross_district_review_denied':True,
            'existing_commitment_recognised':True,'private_receipt_and_verbatim_request_redacted':True,
            'dashboard_route':'/pilot/dashboard?pilot_id='+pilot.PILOT_ID+'&demo_ticket='+rid})
    manifest={'generated_at':datetime.now(timezone.utc).isoformat(),'data_mode':'synthetic',
        'execution':'local Flask API rehearsal in a newly created isolated SQLite database',
        'source_database_modified':False,'database':str(workspace/'rehearsal.db'),
        'google_provider_calls':0,'real_messages_sent':0,'voice_recording':'pending team-recorded Tamil or Telugu fictional request',
        'government_participation':False,'deployment_verified':False,
        'scenarios':results,'artifacts':files,
        'limitations':['Automated role actions are not independent human or government review.',
                       'Commitment recognition demonstrates internal overlap only, not a live government investment-plan join.',
                       'Costs, beneficiaries, locations and requests are illustrative.',
                       'Google AI and real messaging execution need separate provider evidence.']}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    guide=['# NVB local judging rehearsal','',
           'All three scenarios are fictional. This run proves local workflow execution only.','',
           'Start at `/pilot`, open a prepared example, review the prefilled text, give consent and submit.',
           'Use the district demonstration role to review it, then Development & Policy for the proposal.',
           'Use Admin for synthetic decision approval and Auditor for the redacted dossier.',
           'Switch to another district officer to demonstrate denied access.','',
           'The completed tickets in this bundle are available only in its isolated rehearsal database.','']
    guide.extend('- '+r['district']+': `'+r['dashboard_route']+'`' for r in results)
    guide.extend(['','Record a fresh team-recorded Tamil or Telugu fictional request separately.',
                  'Messaging is a local connector rehearsal until a real gateway receipt is independently signed.',
                  'Do not present this bundle as Google AI execution, field impact, deployment or government approval.'])
    (output/'ENTRY_GUIDE.md').write_text('\n'.join(guide)+'\n',encoding='utf-8')
    print(json.dumps({'bundle':str(output),'scenarios_passed':len(results),'source_database_modified':False}))
    return app


def serve_db(db_path, port=5002):
    import sqlite3
    db_path = Path(db_path).resolve()
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    users = con.cursor().execute("SELECT name, role, api_token FROM users").fetchall()
    con.close()
    credentials = {u['role']: u['api_token'] for u in users if u['role'] in ('admin', 'analyst', 'auditor')}
    app = create_app({
        'TESTING': False,
        'TEMPLATES_AUTO_RELOAD': True,
        'DATABASE_URL': '',
        'DATABASE_PATH': str(db_path),
        'DEMO_MODE': True,
        'SEED_DEMO_DATA': False,
        'AUTO_MIGRATE': False,
        'DISABLE_EXTERNAL_SERVICES': True,
        'SECRET_KEY': secrets.token_urlsafe(32),
        'PILOT_ID': pilot.PILOT_ID,
        'PILOT_ONLY': True,
        'PILOT_MODEL_CALLS': False,
        'PILOT_ANALYTICS_SYNC': False,
        'LOCAL_EVALUATION_WORKER': False,
        **{role.upper() + '_API_TOKEN': val for role, val in credentials.items()}
    })
    app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='scratch/pilot-judging-package')
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int,default=5002)
    parser.add_argument('--db',default=None,help='Serve existing rehearsal database')
    args=parser.parse_args()
    if args.db:
        serve_db(args.db, port=args.port)
    else:
        app=run(args.output)
        if args.serve:
            app.config['TESTING']=False
            app.config['TEMPLATES_AUTO_RELOAD']=True
            app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False)
