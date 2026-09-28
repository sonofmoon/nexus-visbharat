"""Durable, leased Telegram audio/intake work. No background threads in web requests."""
import hashlib
import json
import time
from uuid import uuid4

from flask import current_app

from ..db import get_db


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS telegram_jobs (
        job_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, draft_nonce TEXT NOT NULL,
        kind TEXT NOT NULL, payload_json TEXT NOT NULL, request_id TEXT UNIQUE,
        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        available_at INTEGER NOT NULL, lease_owner TEXT, lease_until INTEGER,
        error_type TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_telegram_jobs_due ON telegram_jobs(status,available_at)')
    db.execute('CREATE TABLE IF NOT EXISTS telegram_worker_heartbeats (role TEXT PRIMARY KEY, last_seen_epoch INTEGER NOT NULL)')


def heartbeat(role):
    db = get_db()
    db.execute('''INSERT INTO telegram_worker_heartbeats(role,last_seen_epoch) VALUES (?,?)
        ON CONFLICT(role) DO UPDATE SET last_seen_epoch=excluded.last_seen_epoch''', (role, int(time.time())))
    db.commit()


def enqueue(chat_id, session, kind):
    from . import telegram_gateway as bot
    key = f"{chat_id}:{session['nonce']}:{kind}"
    if kind == 'voice':
        key += ':' + session['voice_file_id'] + ':' + str(session.get('voice_revision', 0))
    job_id = hashlib.sha256(key.encode()).hexdigest()
    db = get_db()
    existing = db.execute('SELECT * FROM telegram_jobs WHERE job_id=?', (job_id,)).fetchone()
    if existing:
        if kind == 'voice' and existing['status'] in {'failed', 'cancelled'}:
            db.execute("UPDATE telegram_jobs SET status='queued',available_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?", (int(time.time()), job_id))
        return dict(existing)
    request_id = None
    if kind == 'intake':
        from .pipeline_queue import _generate_request_id
        while True:
            request_id = _generate_request_id()
            if not db.execute('SELECT job_id FROM telegram_jobs WHERE request_id=?', (request_id,)).fetchone():
                break
    db.execute('''INSERT INTO telegram_jobs
        (job_id,chat_id,draft_nonce,kind,payload_json,request_id,status,available_at,created_at,updated_at)
        VALUES (?,?,?,?,?,?,'queued',?,?,?)''',
        (job_id, str(chat_id), session['nonce'], kind, json.dumps(session), request_id,
         int(time.time()), bot._now(), bot._now()))
    # Caller saves the session and outbox in the same short transaction.
    return {'job_id': job_id, 'request_id': request_id, 'status': 'queued'}


def _claim():
    db = get_db()
    now = int(time.time())
    candidate = db.execute("""SELECT job_id FROM telegram_jobs
        WHERE (status='queued' AND available_at<=?) OR (status='processing' AND lease_until<?)
        ORDER BY created_at,job_id LIMIT 1""", (now, now)).fetchone()
    if not candidate:
        db.commit()
        return None
    owner = uuid4().hex
    lease = max(300, int(current_app.config.get('TELEGRAM_JOB_LEASE_SECONDS', 900)))
    row = db.execute("""UPDATE telegram_jobs SET status='processing',lease_owner=?,lease_until=?,
        attempts=attempts+1 WHERE job_id=? AND
        ((status='queued' AND available_at<=?) OR (status='processing' AND lease_until<?))
        RETURNING *""", (owner, now + lease, candidate['job_id'], now, now)).fetchone()
    db.commit()
    return dict(row) if row else None


def process_one(ingest_text=None):
    """Claim one job; retain a fixed ticket ID across retries and process restarts."""
    from . import telegram_gateway as bot
    from ..blueprints.api_channels import _ingest_text_request
    ingest_text = ingest_text or _ingest_text_request
    job = _claim()
    if not job:
        return False
    chat_id = job['chat_id']
    snapshot = json.loads(job['payload_json'])
    current = bot._session(chat_id)
    if not _matches(job, current):
        _finish(job, 'cancelled')
        return True
    error = None
    result = None
    try:
        if job['kind'] == 'voice':
            result = bot._voice_text(snapshot['voice_file_id'], bot._lang(snapshot))
        else:
            # A previous worker may have saved the ticket before crashing.
            existing = get_db().execute('SELECT request_id FROM citizen_requests WHERE request_id=?', (job['request_id'],)).fetchone()
            if existing:
                result = {'success': True, 'request_id': existing['request_id']}
            else:
                with current_app.test_request_context('/internal/telegram-worker', method='POST', json={}):
                    result, failure = ingest_text(
                        channel='Telegram', text=snapshot['issue'], language=bot._lang(snapshot),
                        district=snapshot['district'], state=snapshot['state'], ward=snapshot.get('ward', ''),
                        sender=chat_id, endpoint='/api/channels/telegram/conversation',
                        idempotency_key=f"telegram:{chat_id}:{job['draft_nonce']}",
                        reserved_request_id=job['request_id'],
                    )
                if failure or not result or not result.get('request_id'):
                    raise ValueError('Intake did not return a saved ticket')
    except Exception as exc:
        get_db().rollback()
        error = type(exc).__name__  # Never persist provider URLs, credentials or audio.

    with bot._chat_transaction(chat_id):
        db = get_db()
        owned = db.execute('SELECT lease_owner,status FROM telegram_jobs WHERE job_id=?', (job['job_id'],)).fetchone()
        if not owned or owned['lease_owner'] != job['lease_owner'] or owned['status'] != 'processing':
            return True
        session = bot._session(chat_id)
        if not _matches(job, session):
            _finish(job, 'cancelled', commit=False)
            return True
        if error:
            if job['kind'] == 'voice':
                bot._request_voice_retry(chat_id, session)
                _finish(job, 'failed', error, commit=False)
            elif job['attempts'] < 3:
                db.execute("UPDATE telegram_jobs SET status='queued',available_at=?,lease_owner=NULL,lease_until=NULL,error_type=? WHERE job_id=?",
                           (int(time.time()) + 2 ** job['attempts'], error, job['job_id']))
            else:
                _finish(job, 'failed', error, commit=False)
                bot._text(chat_id, bot._flow(bot._lang(session), 'needs_attention').format(ticket=job['request_id']),
                          key=f"job:{job['job_id']}:failed")
        elif job['kind'] == 'voice':
            session.update(issue=result, consent_granted=False)
            bot._after_issue(chat_id, session)
            _finish(job, 'done', commit=False)
        else:
            session.update(stage='complete', request_id=result['request_id'])
            bot._save(chat_id, session)
            bot._announce_saved(chat_id, session)
            _finish(job, 'done', commit=False)
    return True


def _matches(job, session):
    expected_stage = 'transcribing' if job['kind'] == 'voice' else 'processing'
    return (session.get('nonce') == job['draft_nonce'] and session.get('job_id') == job['job_id']
            and session.get('stage') == expected_stage)


def _finish(job, status, error=None, commit=True):
    from .telegram_gateway import _now
    db = get_db()
    db.execute('''UPDATE telegram_jobs SET status=?,error_type=?,updated_at=?,lease_owner=NULL,lease_until=NULL
        WHERE job_id=? AND lease_owner=?''', (status, error, _now(), job['job_id'], job['lease_owner']))
    if commit:
        db.commit()
