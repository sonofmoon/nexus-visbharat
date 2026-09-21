"""Isolated SQLite benchmark: 100k reports, one million historical audit events.

This measures in-process authenticated reads, not network or cloud throughput.
Historical events are deliberately unsigned and are never presented as verified.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.test_auditor_workbench import AuditorWorkbenchTest
from visbharat.db import get_db

test=AuditorWorkbenchTest();test.setUp();app=test.app
report={'observed_at':datetime.now(timezone.utc).isoformat(),'request_rows':100000,'historical_audit_rows':1000000,
        'environment':'SQLite / Flask test client / local Windows host','network_included':False,
        'model_calls':0,'runs':[],'integrity_workload':'Unsigned synthetic historical events; separate tests check protected-event integrity.'}
try:
    with app.app_context():
        db=get_db();db.execute('DELETE FROM citizen_requests');db.execute('DELETE FROM cluster_members');db.commit()
        sql='''INSERT INTO citizen_requests(request_id,source_channel,input_language,district,state,ward,lat,lng,original_text,translated_text,category,urgency,sentiment,status,ai_metadata_json,created_at)
            VALUES(?,?,?,?,?,?,11,78,'Synthetic benchmark report','Synthetic benchmark report','Water Supply','Routine','Neutral','Pending','{"is_synthetic":true}','2026-01-01T00:00:00Z')'''
        for batch in range(0,100000,5000):
            for i in range(batch,batch+5000):
                state,district,language=[('Tamil Nadu','Karur','ta'),('Telangana','Hyderabad','te'),('Andhra Pradesh','Tirupati','te')][i%3]
                db.execute(sql,(f'BENCH-{i}',f'Channel {i%7}',language,district,state,f'Ward {i%20}'))
            db.commit()
        print('Prepared 100,000 synthetic reports',flush=True)
        for batch in range(0,1000000,10000):
            # Use the driver bulk operation only for isolated benchmark fixtures.
            db._conn.executemany("INSERT INTO audit_logs(actor,action,resource_type,resource_id,details_json,created_at) VALUES('benchmark','benchmark_event','citizen_request',?,'{}','2026-01-01T00:00:00Z')",[(f'BENCH-{i%100000}',) for i in range(batch,batch+10000)])
            db.commit()
            if batch%250000==0: print(f'Prepared {batch+10000:,} historical events',flush=True)
        last=db.execute('SELECT MAX(id) AS n FROM audit_logs').fetchone()['n']
        db.execute('UPDATE auditor_chain_state SET legacy_end_id=?,head_id=? WHERE id=1',(last,last));db.commit()
    def invoke(path):
        started=time.perf_counter()
        with app.test_client() as client:
            response=client.get(path,headers=test.h)
            data=response.get_json()
        if response.status_code!=200: raise AssertionError(data)
        if path.endswith('/snapshot'): assert data['counts']['total']==100000 and sum(r['value'] for r in data['distributions']['channels'])==100000
        return (time.perf_counter()-started)*1000,len(response.data),data.get('cache_hit')
    def measure(label,path,concurrency,requests):
        began=time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool: results=list(pool.map(lambda _:invoke(path),range(requests)))
        elapsed=time.perf_counter()-began;values=sorted(r[0] for r in results)
        def percentile(p): return round(values[min(len(values)-1,int((len(values)-1)*p))],2)
        item={'label':label,'concurrency':concurrency,'requests':requests,'p50_ms':percentile(.5),'p95_ms':percentile(.95),'p99_ms':percentile(.99),
              'requests_per_second':round(requests/elapsed,2),'max_payload_bytes':max(r[1] for r in results),'failures':0,'cache_hits':sum(bool(r[2]) for r in results)}
        report['runs'].append(item);print(json.dumps(item),flush=True)
    measure('initial_snapshot','/api/v2/auditor/snapshot',1,1)
    measure('warm_snapshot','/api/v2/auditor/snapshot',20,100)
    measure('paged_history','/api/v2/auditor/events?action=benchmark_event&limit=100',20,100)
    out=ROOT/'docs/evaluation/auditor-load.json';out.write_text(json.dumps(report,indent=2))
finally:
    test.doCleanups()
