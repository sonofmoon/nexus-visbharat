"""Prepare an isolated synthetic rehearsal; never modify the source demonstration DB."""
import json
from pathlib import Path
import secrets
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from visbharat import create_app
from visbharat.config import Config
from visbharat.db import get_db
from visbharat.services import pilot
from visbharat.security import hash_api_token,token_last4

DEST=ROOT/'scratch/pilot-implementation/rehearsal.db'


def configuration():
    return {'DATABASE_URL':'','DATABASE_PATH':str(DEST),'DISABLE_EXTERNAL_SERVICES':True,
        'DEMO_MODE':True,'SEED_DEMO_DATA':False,'AUTO_MIGRATE':True,'PILOT_ONLY':True,
        'PILOT_ID':pilot.PILOT_ID,'PILOT_MODEL_CALLS':False,'PILOT_ANALYTICS_SYNC':False,
        'LOCAL_EVALUATION_WORKER':False,'JURY_REQUIRE_LIVE_MODELS':False,
        'GOOGLE_MAPS_API_KEY':'','USE_REAL_GOOGLE_MAPS':False}


def prepare():
    DEST.parent.mkdir(parents=True,exist_ok=True)
    source=Path(Config.DATABASE_PATH).resolve()
    if source==DEST.resolve():raise RuntimeError('Source and rehearsal database must be different')
    connection=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);connection.row_factory=sqlite3.Row
    corpus=[dict(r) for r in connection.execute("SELECT * FROM citizen_requests WHERE (state='Tamil Nadu' AND district='Vellore' OR state='Andhra Pradesh' AND district='Tirupati') AND category='Water Supply' ORDER BY id")]
    connection.close()
    if not corpus or any(not json.loads(r['ai_metadata_json']).get('is_synthetic') for r in corpus):
        raise RuntimeError('Only an explicitly synthetic source corpus can be copied into this rehearsal')
    app=create_app(configuration())
    with app.app_context():
        pilot.create_default();db=get_db()
        for district,state in [('Vellore','Tamil Nadu'),('Tirupati','Andhra Pradesh')]:
            name=district+' demo officer'
            if not pilot.rows('SELECT id FROM users WHERE name=?',(name,)):
                credential=secrets.token_urlsafe(32)
                db.execute('''INSERT INTO users(name,api_token,api_token_hash,token_last4,role,created_at)
                    VALUES(?,?,?,?,'analyst',?)''',(name,credential,hash_api_token(credential),token_last4(credential),pilot.now()))
        for u in pilot.rows('SELECT id,name FROM users'):
            district=next((d for d in ('Vellore','Tirupati') if u['name']==d+' demo officer'),'')
            state={'Vellore':'Tamil Nadu','Tirupati':'Andhra Pradesh'}.get(district,'')
            db.execute('INSERT INTO pilot_memberships VALUES(?,?,?,?,1) ON CONFLICT(pilot_id,user_id) DO NOTHING',(pilot.PILOT_ID,u['id'],state,district))
        db.commit();counts={};added=0
        for r in corpus:
            district=r['district'];i=counts.get(district,0);counts[district]=i+1
            loc=district.lower()+'-'+str(i%3+1)
            language=r['input_language'] if r['input_language'] in ('ta','te','en') else 'en'
            data={'location_id':loc,'language':language,'text':r['original_text'],
                'consent_granted':True,'source_id':'synthetic-rehearsal:'+r['request_id']}
            result=pilot.intake(pilot.PILOT_ID,data,'seed:'+pilot.digest(data['source_id']))
            if result['reused']:continue
            rid=result['request_id'];added+=1
            metadata={'is_synthetic':True,'source_demo_ticket':r['request_id'],'pilot_id':pilot.PILOT_ID,
                'processing_status':'human_reviewed','provider_mode':'recorded_synthetic_fixture',
                'notice':'Copied fictional report; seeded review is a role simulation, not human validation or a new AI call.',
                'geolocation_status':'not_observed','location_status':'proposed'}
            db.execute("UPDATE citizen_requests SET translated_text=?,category='Water Supply',urgency=?,ai_metadata_json=? WHERE request_id=?",
                (r['translated_text'],r['urgency'],json.dumps(metadata),rid))
            db.execute("UPDATE pilot_requests SET processing_status='human_reviewed' WHERE request_id=?",(rid,))
            db.execute("UPDATE pilot_outbox SET status='completed' WHERE request_id=?",(rid,));db.commit()
        count=pilot.rows('SELECT COUNT(*) n FROM pilot_requests')[0]['n']
        manifest={'data_mode':'synthetic','source_records_unchanged':True,'pilot_id':pilot.PILOT_ID,
            'reports':count,'new_reports':added,'source_distribution':counts,'google_model_calls':0,
            'locations':'Six proposed demonstration catchments; official communities have not been selected.'}
        (ROOT/'docs/evaluation/ministry-pilot-demo.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print(json.dumps(manifest,indent=2))
    return app


if __name__=='__main__':prepare()
