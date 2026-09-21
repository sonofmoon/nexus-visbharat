"""Measure an isolated SQLite copy at two sizes; never modify the demo database."""
import argparse
import concurrent.futures
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask
from visbharat.config import Config
from visbharat.db import close_db, get_db, ensure_database_indexes
from visbharat.services.repository import ReferenceDataRepository
from visbharat.blueprints.analyst import analyst_bp
from visbharat.security import hash_api_token


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rows',type=int,default=100000);parser.add_argument('--requests',type=int,default=20);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=root/'scratch'/'analyst-implementation'/'load';output.mkdir(parents=True,exist_ok=True)
    destination=output/('load-'+str(time.time_ns())+'.db')
    source=sqlite3.connect('file:'+Path(Config.DATABASE_PATH).resolve().as_posix()+'?mode=ro',uri=True)
    target=sqlite3.connect(destination);source.backup(target);source.close()
    app=Flask(__name__);app.config.from_object(Config);app.config.update(TESTING=True,DATABASE_URL='',DATABASE_PATH=str(destination))
    app.teardown_appcontext(close_db);app.register_blueprint(analyst_bp)
    app.extensions['reference_repo']=ReferenceDataRepository(str(root))
    token='isolated-load-test-token'
    with app.app_context():
        ensure_database_indexes()
        get_db().execute('INSERT INTO users(name,api_token,api_token_hash,role,created_at) VALUES(?,?,?,?,?)',('Isolated benchmark',token,hash_api_token(token),'analyst','2026-09-19'));get_db().commit()
    initial=target.execute('SELECT COUNT(*) FROM citizen_requests').fetchone()[0]
    columns=[r[1] for r in target.execute('PRAGMA table_info(citizen_requests)') if r[1]!='id']
    results=[]
    def run_request(i):
        started=time.perf_counter()
        with app.test_client() as client:
            response=client.get('/api/v2/analyst/snapshot',query_string={'limit':20,'state':'' if i%2 else 'Tamil Nadu'},headers={'Authorization':'Bearer '+token})
            assert response.status_code==200,response.get_json()
            d=response.get_json();assert sum(d['stats']['channels'].values())==d['stats']['total_complaints']
        return (time.perf_counter()-started)*1000
    for size in (initial,max(initial,args.rows)):
        current=target.execute('SELECT COUNT(*) FROM citizen_requests').fetchone()[0]
        if size>current:
            # Replicate only original rows. These are isolated stress fixtures, not new citizens.
            batch=0
            while current<size:
                take=min(initial,size-current)
                expressions=[f"'LOAD-{batch}-' || request_id" if c=='request_id' else c for c in columns]
                target.execute(f"INSERT INTO citizen_requests ({','.join(columns)}) SELECT {','.join(expressions)} FROM citizen_requests ORDER BY id LIMIT ?",(take,))
                current+=take;batch+=1
            target.commit()
        for concurrency in (1,4):
            run_request(1)  # warm-up excluded
            start=time.perf_counter()
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                timings=list(executor.map(run_request,range(args.requests)))
            elapsed=time.perf_counter()-start;ordered=sorted(timings)
            percentile=lambda q:round(ordered[max(0,math.ceil(q*len(ordered))-1)],2)
            result={'rows':size,'requests':len(timings),'concurrency':concurrency,'p50_ms':percentile(.5),'p95_ms':percentile(.95),'p99_ms':percentile(.99),'throughput_per_second':round(len(timings)/elapsed,2),'failure_rate':0,'assertion':'Full scoped channel totals reconcile'}
            results.append(result);print(json.dumps(result),flush=True)
    target.close()
    report={'status':'measured_local_sqlite','generated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'results':results,
            'environment':{'python':sys.version.split()[0],'platform':sys.platform,'database':'SQLite copy','http':'Flask test client; excludes network and model latency'},
            'limitations':['Synthetic replication, not a national deployment benchmark.','Small request sample; tail percentiles require a longer deployment soak test.','Queue age, external-provider throughput and cloud billing cost were not measured.'],
            'external_cost_per_request':None,'queue_age_seconds':None,'artifact':str(destination.relative_to(root))}
    path=root/'docs/evaluation/load.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2),encoding='utf-8')

if __name__=='__main__':main()
