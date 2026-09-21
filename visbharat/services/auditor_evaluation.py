"""Bounded offline evaluation jobs. Measurements come from labelled observations."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time
from datetime import timedelta
from flask import g
from ..db import get_db
from ..audit import write_audit_log
from . import auditor_workbench as work
from .auditor_actions import identifier

LOCK=threading.RLock()


def ratio(n,d): return round(n/d,6) if d else None


def wilson(n,d):
    if not d: return None
    z=1.96;p=n/d;den=1+z*z/d
    center=(p+z*z/(2*d))/den
    radius=z*math.sqrt((p*(1-p)+z*z/(4*d))/d)/den
    return [round(center-radius,6),round(center+radius,6)]


def classification(rows):
    labels=sorted({r['expected_category'] for r in rows}|{r['predicted_category'] for r in rows})
    f1=[]
    for label in labels:
        tp=sum(r['expected_category']==label and r['predicted_category']==label for r in rows)
        fp=sum(r['expected_category']!=label and r['predicted_category']==label for r in rows)
        fn=sum(r['expected_category']==label and r['predicted_category']!=label for r in rows)
        f1.append(2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0)
    emergencies=[r for r in rows if r.get('expected_urgency')=='Emergency']
    detected=sum(r.get('predicted_urgency')=='Emergency' for r in emergencies)
    correct=sum(r['expected_category']==r['predicted_category'] for r in rows)
    return {'n':len(rows),'category_macro_f1':round(sum(f1)/len(f1),6) if f1 else None,
            'accuracy':ratio(correct,len(rows)),'accuracy_ci95':wilson(correct,len(rows)),
            'emergency_n':len(emergencies),'emergency_recall':ratio(detected,len(emergencies)),
            'emergency_recall_ci95':wilson(detected,len(emergencies))}


def edit_distance(a,b):
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1): current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=current
    return previous[-1]


def calculate(data):
    began=time.monotonic()
    rows=data['samples'];result={'overall':classification(rows),'groups':{},'model_version':data['model_version'],
        'dataset_version':data['dataset_version'],'dataset_sha256':work.digest(rows),
        'label_status':data['label_status'],'review_reference':data.get('review_reference'),
        'independently_verified':False,'notice':'Metrics describe the submitted evaluation set; reviewer provenance and representativeness still require independent checking.'}
    for dimension in ('language','state','channel'):
        groups={k:classification([r for r in rows if r.get(dimension,'Unknown')==k]) for k in sorted({r.get(dimension,'Unknown') for r in rows})}
        values=[v['accuracy'] for v in groups.values() if v['accuracy'] is not None]
        result['groups'][dimension]={'items':groups,'accuracy_gap':max(values)-min(values) if len(values)>1 else None}
    audio=[r for r in rows if r.get('reference_transcript')]
    words=sum(len(r['reference_transcript'].split()) for r in audio)
    chars=sum(len(r['reference_transcript']) for r in audio)
    word_errors=char_errors=0
    for r in audio:
        if time.monotonic()-began>20: raise TimeoutError('Evaluation exceeded its computation budget')
        word_errors+=edit_distance(r['reference_transcript'].split(),r.get('predicted_transcript','').split())
        char_errors+=edit_distance(r['reference_transcript'],r.get('predicted_transcript',''))
    result['speech']={'n':len(audio),'wer':ratio(word_errors,words),'cer':ratio(char_errors,chars)}
    for field in ('translation_correct','location_correct'):
        labelled=[r for r in rows if isinstance(r.get(field),bool)]
        n=sum(r[field] for r in labelled)
        result[field]={'n':len(labelled),'accuracy':ratio(n,len(labelled)),'ci95':wilson(n,len(labelled))}
    pairs=[r for r in rows if isinstance(r.get('expected_duplicate'),bool) and isinstance(r.get('predicted_duplicate'),bool)]
    tp=sum(r['expected_duplicate'] and r['predicted_duplicate'] for r in pairs)
    result['duplicates']={'n':len(pairs),'precision':ratio(tp,sum(r['predicted_duplicate'] for r in pairs)),
                          'recall':ratio(tp,sum(r['expected_duplicate'] for r in pairs))}
    baseline=data.get('baseline_language_counts')
    if baseline:
        current=Counter(r.get('language','Unknown') for r in rows);labels=set(current)|set(baseline)
        n=sum(baseline.values());m=len(rows)
        # Jensen-Shannon divergence in bits; distribution change is not prediction bias.
        js=0.0
        for key in labels:
            p=baseline.get(key,0)/n;q=current.get(key,0)/m;mid=(p+q)/2
            if p: js+=.5*p*math.log2(p/mid)
            if q: js+=.5*q*math.log2(q/mid)
        result['distribution_drift']={'jensen_shannon_bits':round(js,6),'baseline_n':n,'current_n':m,
                                     'meaning':'Language composition change on the evaluation samples; not model bias or quality drift.'}
    else: result['distribution_drift']={'status':'baseline_required'}
    return result


def enqueue(data,scope):
    samples=data.get('samples')
    if not isinstance(samples,list) or not 1<=len(samples)<=500: raise ValueError('Supply 1–500 labelled evaluation samples')
    for row in samples:
        if not isinstance(row,dict): raise ValueError('Each sample must be an object')
        for k in ('expected_category','predicted_category'): work.text(row.get(k),k,1,80)
        for k in ('reference_transcript','predicted_transcript'):
            if not isinstance(row.get(k,''),str) or len(row.get(k,''))>500: raise ValueError('Audio review text must be a string of at most 500 characters')
    for k in ('dataset_version','model_version'): data[k]=work.text(data.get(k),k,1,150)
    if sum(len(r.get('reference_transcript',''))+len(r.get('predicted_transcript','')) for r in samples)>20000:
        raise ValueError('Split audio evaluations into batches of at most 20,000 transcript characters')
    if data.get('label_status') not in ('provisional','human_reviewed'): raise ValueError('Declare provisional or human_reviewed labels')
    if data['label_status']=='human_reviewed': data['review_reference']=work.safe_uri(data.get('review_reference'))
    if data.get('baseline_language_counts'):
        if not isinstance(data['baseline_language_counts'],dict): raise ValueError('Baseline must contain language counts')
        for k,v in data['baseline_language_counts'].items():
            work.text(k,'language',1,80)
            if not isinstance(v,int) or isinstance(v,bool): raise ValueError('Baseline counts must be integers')
            work.number(v,'baseline count',0,1e9)
        if sum(data['baseline_language_counts'].values())<=0: raise ValueError('Baseline is empty')
    pending=work.query("SELECT COUNT(*) AS n FROM auditor_jobs WHERE status IN ('queued','running')")[0]['n']
    if pending>=10: raise ValueError('Evaluation queue is full; wait for a job to finish')
    jid=identifier('NVB-EVAL');stamp=work.now().isoformat();db=get_db()
    db.execute('''INSERT INTO auditor_jobs(job_id,kind,status,scope_json,input_json,created_by,created_at) VALUES(?,'labelled_evaluation','queued',?,?,?,?)''',
               (jid,json.dumps(scope),json.dumps(data),g.current_user['name'],stamp))
    write_audit_log(g.current_user['name'],'evaluation_queued','evaluation',jid,{'dataset_version':data['dataset_version'],'samples':len(samples)},commit=False)
    db.commit();return {'job_id':jid,'status':'queued'}


def run_pending(app):
    with app.app_context():
        db=get_db()
        # Recover abandoned work after a process restart; failed jobs do not retry forever.
        stale=(work.now()-timedelta(minutes=5)).isoformat()
        db.execute("UPDATE auditor_jobs SET status='failed',error='Worker recovery limit reached',completed_at=? WHERE status='running' AND started_at<? AND attempts>=2",(work.now().isoformat(),stale))
        db.execute("UPDATE auditor_jobs SET status='queued' WHERE status='running' AND started_at<? AND attempts<2",(stale,));db.commit()
        while True:
            db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
            rows=work.query("SELECT * FROM auditor_jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
            if not rows: db.commit();break
            job=rows[0]
            db.execute("UPDATE auditor_jobs SET status='running',started_at=?,attempts=attempts+1 WHERE job_id=?",(work.now().isoformat(),job['job_id']));db.commit()
            try:
                result=calculate(json.loads(job['input_json']))
                db.execute("UPDATE auditor_jobs SET status='completed',result_json=?,completed_at=? WHERE job_id=?",
                           (json.dumps(result),work.now().isoformat(),job['job_id']))
                write_audit_log('evaluation_worker','evaluation_completed','evaluation',job['job_id'],{'dataset_sha256':result['dataset_sha256']},commit=False)
                db.commit()
            except Exception:
                db.rollback()
                db.execute("UPDATE auditor_jobs SET status='failed',error='Evaluation failed; inspect server diagnostics',completed_at=? WHERE job_id=?",(work.now().isoformat(),job['job_id']))
                db.commit();app.logger.exception('Auditor evaluation job failed')


def start_worker(app):
    with LOCK:
        executor=app.extensions.setdefault('auditor_executor',ThreadPoolExecutor(max_workers=1,thread_name_prefix='auditor-eval'))
        future=app.extensions.get('auditor_worker_future')
        if not future or future.done():
            future=executor.submit(run_pending,app)
            app.extensions['auditor_worker_future']=future
            def drain_remaining(_):
                with app.app_context():
                    if work.query("SELECT job_id FROM auditor_jobs WHERE status='queued' LIMIT 1"):
                        start_worker(app)
            future.add_done_callback(drain_remaining)


def results(scope):
    # Evaluations are global test datasets, not citizen records. Show exact dataset
    # scope and disclose that intake filters do not rewrite an evaluation result.
    rows=work.query('SELECT job_id,status,scope_json,result_json,created_by,created_at,started_at,completed_at,error FROM auditor_jobs ORDER BY created_at DESC LIMIT 20')
    if scope.get('pilot_id'):
        rows=[r for r in rows if json.loads(r['scope_json']).get('pilot_id')==scope['pilot_id'] and all(not scope.get(k) or scope[k]==json.loads(r['scope_json']).get(k) for k in ('state','district'))]
    allowed=scope.get('_allowed_states') or []
    if allowed:
        rows=[r for r in rows if json.loads(r['scope_json']).get('state') in allowed]
    for r in rows:
        r['evaluation_scope']=json.loads(r.pop('scope_json'));r['result']=json.loads(r.pop('result_json') or 'null')
    return {'items':rows,'notice':'Results retain their recorded evaluation-dataset scope. Citizen intake filters do not recalculate these results.'}
