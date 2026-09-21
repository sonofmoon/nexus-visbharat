"""Authenticated managed-task dispatch; SQL outbox is the recovery source."""
import json
from flask import current_app,request
from . import pilot


def require_worker_identity():
    from google.oauth2 import id_token
    from google.auth.transport.requests import Request
    audience=current_app.config.get('PILOT_WORKER_AUDIENCE')
    expected=current_app.config.get('PILOT_WORKER_SERVICE_ACCOUNT')
    value=request.headers.get('Authorization','')
    if not audience or not expected or not value.startswith('Bearer '):raise PermissionError('Worker identity is required')
    try:claims=id_token.verify_oauth2_token(value[7:],Request(),audience=audience)
    except Exception:raise PermissionError('Worker identity could not be verified')
    if claims.get('email')!=expected or claims.get('email_verified') is not True:raise PermissionError('Unassigned worker identity')


def dispatch():
    from google.cloud import tasks_v2
    from google.api_core.exceptions import AlreadyExists
    queue=current_app.config.get('PILOT_TASK_QUEUE');url=current_app.config.get('PILOT_WORKER_URL')
    if not queue or not url or not url.startswith('https://'):raise ValueError('Configure the approved task queue and worker URL')
    client=tasks_v2.CloudTasksClient();sent=0
    jobs=pilot.rows("SELECT job_id,updated_at FROM pilot_outbox WHERE (status='queued' AND next_attempt_at<=?) OR (status='running' AND lease_until<?) ORDER BY created_at LIMIT 50",(pilot.now(),pilot.now()))
    from datetime import datetime,timedelta,timezone
    stale=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
    evaluations=pilot.rows("SELECT job_id,COALESCE(started_at,created_at) updated_at FROM auditor_jobs WHERE status='queued' OR (status='running' AND started_at<?) ORDER BY created_at LIMIT 1",(stale,))
    jobs.extend({**r,'kind':'evaluation'} for r in evaluations)
    for job in jobs:
        name=pilot.digest([job['job_id'],job['updated_at']])
        task={'name':queue+'/tasks/'+name,'http_request':{'http_method':tasks_v2.HttpMethod.POST,
            'url':url.rstrip('/')+'/api/v2/pilot/internal/work','headers':{'Content-Type':'application/json'},
            'body':json.dumps({'job_id':job['job_id'],'kind':job.get('kind','pilot')}).encode(),
            'oidc_token':{'service_account_email':current_app.config['PILOT_WORKER_SERVICE_ACCOUNT'],
                          'audience':current_app.config['PILOT_WORKER_AUDIENCE']}}}
        try:client.create_task(request={'parent':queue,'task':task},timeout=15);sent+=1
        except AlreadyExists:pass
    return {'dispatched':sent,'selected':len(jobs)}
