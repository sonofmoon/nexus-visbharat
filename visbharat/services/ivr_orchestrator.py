from datetime import datetime, timedelta, timezone
import json
import hashlib
import hmac

from flask import current_app

from ..db import get_db
from .pipeline_queue import enqueue_ingestion_job
from .notifications import dispatch_sla_notification


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _parse_iso(value: str):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except Exception:
        return None


def _next_retry_iso(attempt: int):
    base = max(float(current_app.config.get('IVR_CALLBACK_RETRY_BASE_SECONDS', 60) or 60), 1.0)
    multiplier = max(float(current_app.config.get('IVR_CALLBACK_RETRY_BACKOFF_MULTIPLIER', 2.0) or 2.0), 1.0)
    max_delay = max(float(current_app.config.get('IVR_CALLBACK_RETRY_MAX_SECONDS', 1800) or 1800), base)
    delay = min(base * (multiplier ** max(int(attempt) - 1, 0)), max_delay)
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat().replace('+00:00', 'Z')


def orchestrate_missed_call_callback(phone: str, district: str = '', language: str = 'en'):
    normalized_phone = str(phone or '').strip()
    if not normalized_phone:
        raise ValueError('phone is required')

    db = get_db()
    callback_id = f"IVR-CB-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    now = _now_iso()
    max_retries = int(current_app.config.get('IVR_CALLBACK_MAX_RETRIES', 2) or 2)

    payload = {
        'channel': 'Voice IVR',
        'text': 'Missed-call callback requested',
        'language': str(language or 'en').strip() or 'en',
        'district': str(district or '').strip(),
        'sender': normalized_phone,
        'callback': {
            'callback_id': callback_id,
            'status': 'scheduled',
            'requested_at': now,
            'phone': normalized_phone,
        },
    }

    job_id = enqueue_ingestion_job(payload, idempotency_key=f'ivr-missed-{normalized_phone}')
    db.execute(
        '''
        INSERT INTO ivr_callback_jobs (
            callback_id, phone, district, language, status, attempt_count, max_retries,
            next_retry_at, ingestion_job_id, last_error, payload_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            callback_id,
            normalized_phone,
            str(district or '').strip(),
            str(language or 'en').strip() or 'en',
            'scheduled',
            0,
            max_retries,
            now,
            job_id,
            '',
            str(payload),
            now,
            now,
        ),
    )
    db.commit()

    return {
        'status': 'scheduled',
        'callback_id': callback_id,
        'callback_job_id': job_id,
        'phone': normalized_phone,
    }


def get_callback_status(callback_id: str):
    db = get_db()
    row = db.execute(
        '''
        SELECT callback_id, phone, district, language, status, attempt_count, max_retries,
               next_retry_at, ingestion_job_id, last_error, created_at, updated_at
        FROM ivr_callback_jobs
        WHERE callback_id = ?
        LIMIT 1
        ''',
        (str(callback_id or '').strip(),),
    ).fetchone()
    return dict(row) if row else None


def process_due_callbacks(limit: int = 20, force_fail: bool = False):
    db = get_db()
    now = _now_iso()
    safe_limit = min(max(int(limit or 20), 1), 200)

    rows = db.execute(
        '''
        SELECT callback_id, status, attempt_count, max_retries, phone, district, language, ingestion_job_id, next_retry_at
        FROM ivr_callback_jobs
        WHERE status IN ('scheduled', 'dialing', 'retry_pending')
          AND (next_retry_at IS NULL OR next_retry_at <= ?)
        ORDER BY created_at ASC
        LIMIT ?
        ''',
        (now, safe_limit),
    ).fetchall()

    processed = []
    for row in rows:
        callback_id = str(row['callback_id'])
        attempts = int(row['attempt_count'] or 0) + 1
        max_retries = int(row['max_retries'] or 0)

        if force_fail:
            if attempts <= max_retries:
                next_retry_at = _next_retry_iso(attempts)
                db.execute(
                    'UPDATE ivr_callback_jobs SET status = ?, attempt_count = ?, next_retry_at = ?, last_error = ?, updated_at = ? WHERE callback_id = ?',
                    ('retry_pending', attempts, next_retry_at, 'simulated callback failure', now, callback_id),
                )
                processed.append({'callback_id': callback_id, 'status': 'retry_pending', 'attempt_count': attempts, 'next_retry_at': next_retry_at})
            else:
                db.execute(
                    'UPDATE ivr_callback_jobs SET status = ?, attempt_count = ?, next_retry_at = NULL, last_error = ?, updated_at = ? WHERE callback_id = ?',
                    ('failed', attempts, 'max retries reached', now, callback_id),
                )
                db.execute(
                    'INSERT INTO ivr_callback_dead_letters (callback_id, phone, district, language, attempt_count, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (callback_id, str(row['phone'] or ''), str(row['district'] or ''), str(row['language'] or 'en'), attempts, 'max retries reached', now),
                )
                processed.append({'callback_id': callback_id, 'status': 'failed', 'attempt_count': attempts, 'dead_lettered': True})
            continue

        db.execute(
            'UPDATE ivr_callback_jobs SET status = ?, attempt_count = ?, next_retry_at = NULL, last_error = ?, updated_at = ? WHERE callback_id = ?',
            ('connected', attempts, '', now, callback_id),
        )
        processed.append({'callback_id': callback_id, 'status': 'connected', 'attempt_count': attempts})

    db.commit()
    return {'processed': processed, 'count': len(processed)}


def get_callback_metrics():
    db = get_db()

    totals = db.execute(
        '''
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END) AS scheduled,
          SUM(CASE WHEN status = 'dialing' THEN 1 ELSE 0 END) AS dialing,
          SUM(CASE WHEN status = 'retry_pending' THEN 1 ELSE 0 END) AS retry_pending,
          SUM(CASE WHEN status = 'connected' THEN 1 ELSE 0 END) AS connected,
          SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
          AVG(attempt_count) AS avg_attempts
        FROM ivr_callback_jobs
        '''
    ).fetchone()

    dead_letters = db.execute('SELECT COUNT(*) AS c FROM ivr_callback_dead_letters').fetchone()
    reasons = db.execute(
        '''
        SELECT reason, COUNT(*) AS c
        FROM ivr_callback_dead_letters
        GROUP BY reason
        ORDER BY c DESC
        LIMIT 10
        '''
    ).fetchall()

    return {
        'total_callbacks': int(totals['total'] or 0),
        'scheduled': int(totals['scheduled'] or 0),
        'dialing': int(totals['dialing'] or 0),
        'retry_pending': int(totals['retry_pending'] or 0),
        'connected': int(totals['connected'] or 0),
        'failed': int(totals['failed'] or 0),
        'avg_attempts': round(float(totals['avg_attempts'] or 0.0), 3),
        'dead_letter_count': int(dead_letters['c'] or 0),
        'dead_letter_reasons': [{'reason': str(r['reason'] or ''), 'count': int(r['c'] or 0)} for r in reasons],
    }




def _deep_get(obj, *path, default=''):
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return default
    return cur


def parse_provider_callback_payload(provider: str, data: dict):
    payload = data or {}
    norm_provider = str(provider or payload.get('provider') or 'twilio').strip().lower()

    if norm_provider == 'twilio':
        callback_id = str(
            payload.get('callback_id')
            or payload.get('CallbackId')
            or payload.get('custom_field')
            or payload.get('CustomField')
            or ''
        ).strip()
        status = str(
            payload.get('status')
            or payload.get('StatusCallbackEvent')
            or payload.get('CallStatus')
            or payload.get('call_status')
            or ''
        ).strip()
        external_event_id = str(payload.get('event_id') or payload.get('EventSid') or payload.get('CallSid') or '').strip()
    elif norm_provider == 'exotel':
        callback_id = str(
            payload.get('callback_id')
            or payload.get('custom_field')
            or payload.get('CustomField')
            or _deep_get(payload, 'Call', 'CustomField', default='')
            or _deep_get(payload, 'call', 'custom_field', default='')
            or ''
        ).strip()
        status = str(
            payload.get('status')
            or payload.get('call_status')
            or payload.get('event')
            or _deep_get(payload, 'Call', 'Status', default='')
            or _deep_get(payload, 'call', 'status', default='')
            or ''
        ).strip()
        external_event_id = str(
            payload.get('event_id')
            or payload.get('sid')
            or _deep_get(payload, 'Call', 'Sid', default='')
            or _deep_get(payload, 'call', 'sid', default='')
            or ''
        ).strip()
    else:
        callback_id = str(payload.get('callback_id') or payload.get('CallbackId') or '').strip()
        status = str(payload.get('status') or payload.get('CallStatus') or '').strip()
        external_event_id = str(payload.get('event_id') or payload.get('EventSid') or '').strip()

    if not callback_id or not status:
        return {'success': False, 'error': 'callback_id and status are required after provider mapping'}

    return {
        'success': True,
        'provider': norm_provider,
        'callback_id': callback_id,
        'status': status,
        'external_event_id': external_event_id,
    }

def _normalize_provider_status(status: str):
    val = str(status or '').strip().lower()
    aliases = {
        'queued': 'dialing',
        'ringing': 'dialing',
        'in-progress': 'dialing',
        'in_progress': 'dialing',
        'dialing': 'dialing',
        'answered': 'connected',
        'completed': 'connected',
        'connected': 'connected',
        'failed': 'failed',
        'busy': 'failed',
        'no-answer': 'failed',
        'no_answer': 'failed',
        'canceled': 'failed',
    }
    return aliases.get(val, val or 'failed')


def reconcile_provider_callback(provider: str, callback_id: str, status: str, external_event_id: str = '', raw_payload=None):
    db = get_db()
    now = _now_iso()
    normalized_status = _normalize_provider_status(status)
    safe_provider = str(provider or '').strip().lower() or 'unknown'
    safe_callback_id = str(callback_id or '').strip()
    safe_external_id = str(external_event_id or '').strip()
    if not safe_callback_id:
        return {'success': False, 'error': 'callback_id is required'}

    row = db.execute(
        'SELECT callback_id, status, attempt_count, max_retries, phone, district, language FROM ivr_callback_jobs WHERE callback_id = ? LIMIT 1',
        (safe_callback_id,),
    ).fetchone()
    if not row:
        return {'success': False, 'error': 'callback not found'}

    payload_text = json.dumps(raw_payload or {}, sort_keys=True)
    event_material = f"{safe_provider}|{safe_callback_id}|{safe_external_id}|{normalized_status}|{payload_text}"
    event_key = hashlib.sha256(event_material.encode('utf-8')).hexdigest()

    existing_event = db.execute('SELECT id FROM ivr_callback_events WHERE event_key = ? LIMIT 1', (event_key,)).fetchone()
    if existing_event:
        return {'success': True, 'idempotent_reused': True, 'callback_id': safe_callback_id, 'status': str(row['status'] or '')}

    db.execute(
        'INSERT INTO ivr_callback_events (event_key, callback_id, provider, external_event_id, status, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (event_key, safe_callback_id, safe_provider, safe_external_id, normalized_status, payload_text, now),
    )

    attempts = int(row['attempt_count'] or 0)
    max_retries = int(row['max_retries'] or 0)

    if normalized_status in {'connected'}:
        db.execute(
            'UPDATE ivr_callback_jobs SET status = ?, next_retry_at = NULL, last_error = ?, updated_at = ? WHERE callback_id = ?',
            ('connected', '', now, safe_callback_id),
        )
    elif normalized_status in {'dialing'}:
        db.execute(
            'UPDATE ivr_callback_jobs SET status = ?, updated_at = ? WHERE callback_id = ?',
            ('dialing', now, safe_callback_id),
        )
    else:
        attempts = attempts + 1
        if attempts <= max_retries:
            next_retry = _next_retry_iso(attempts)
            db.execute(
                'UPDATE ivr_callback_jobs SET status = ?, attempt_count = ?, next_retry_at = ?, last_error = ?, updated_at = ? WHERE callback_id = ?',
                ('retry_pending', attempts, next_retry, f'provider failure: {normalized_status}', now, safe_callback_id),
            )
        else:
            db.execute(
                'UPDATE ivr_callback_jobs SET status = ?, attempt_count = ?, next_retry_at = NULL, last_error = ?, updated_at = ? WHERE callback_id = ?',
                ('failed', attempts, 'provider terminal failure', now, safe_callback_id),
            )
            db.execute(
                'INSERT INTO ivr_callback_dead_letters (callback_id, phone, district, language, attempt_count, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (safe_callback_id, str(row['phone'] or ''), str(row['district'] or ''), str(row['language'] or 'en'), attempts, f'provider:{safe_provider}:{normalized_status}', now),
            )

    db.commit()
    latest = get_callback_status(safe_callback_id) or {}
    return {'success': True, 'idempotent_reused': False, 'callback_id': safe_callback_id, 'status': latest.get('status', normalized_status)}



def get_callback_alerts():
    metrics = get_callback_metrics()
    alerts = []

    fail_threshold = max(int(current_app.config.get('IVR_CALLBACK_ALERT_FAILURE_COUNT_THRESHOLD', 5) or 5), 0)
    dl_threshold = max(int(current_app.config.get('IVR_CALLBACK_ALERT_DEAD_LETTER_COUNT_THRESHOLD', 3) or 3), 0)

    failed = int(metrics.get('failed') or 0)
    dead_letters = int(metrics.get('dead_letter_count') or 0)

    if failed >= fail_threshold and fail_threshold > 0:
        alerts.append({'type': 'ivr_callback_failed_spike', 'severity': 'high', 'observed': failed, 'threshold': fail_threshold})
    if dead_letters >= dl_threshold and dl_threshold > 0:
        alerts.append({'type': 'ivr_callback_dead_letter_spike', 'severity': 'high', 'observed': dead_letters, 'threshold': dl_threshold})

    return {'metrics': metrics, 'alerts': alerts}


def emit_callback_alerts(actor: str = 'system'):
    result = get_callback_alerts()
    alerts = list(result.get('alerts') or [])
    if not alerts:
        return {'sent': [], 'alerts': [], 'metrics': result.get('metrics', {}), 'skipped': []}

    db = get_db()
    dedupe_seconds = max(int(current_app.config.get('IVR_CALLBACK_ALERT_DEDUPE_SECONDS', 1800) or 1800), 0)
    cooldown_seconds = max(int(current_app.config.get('IVR_CALLBACK_ALERT_COOLDOWN_SECONDS', 900) or 900), 0)
    now_dt = datetime.now(timezone.utc)

    target = current_app.config.get('NOTIFICATION_SLO_ALERT_TARGET') or {}
    notifications = []
    skipped = []
    for alert in alerts:
        identity = {
            'type': str(alert.get('type') or ''),
            'severity': str(alert.get('severity') or ''),
            'threshold': alert.get('threshold'),
        }
        alert_key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode('utf-8')).hexdigest()

        existing = db.execute(
            'SELECT created_at FROM ivr_callback_alert_events WHERE alert_key = ? ORDER BY id DESC LIMIT 1',
            (alert_key,),
        ).fetchone()
        if existing:
            prev_dt = _parse_iso(str(existing['created_at'] or ''))
            if prev_dt:
                elapsed = (now_dt - prev_dt).total_seconds()
                if dedupe_seconds and elapsed < dedupe_seconds:
                    skipped.append({'alert': alert, 'reason': 'dedupe_window'})
                    continue
                if cooldown_seconds and elapsed < cooldown_seconds:
                    skipped.append({'alert': alert, 'reason': 'cooldown_window'})
                    continue

        stage = f"ivr_{str(alert.get('type') or 'alert')}"[:80]
        payload = dispatch_sla_notification(
            request_id=f"IVR-ALERT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}-{str(alert.get('type') or 'alert')[:12]}",
            stage=stage,
            actor=actor,
            source_channel='ops-ivr-callback',
            target=target,
            sla_due_at=_now_iso(),
            details={'ivr_alert': alert, 'metrics': result.get('metrics', {})},
        )

        if payload.get('idempotent_reused'):
            skipped.append({'alert': alert, 'reason': 'idempotent_reused'})
            continue

        created_at = _now_iso()
        db.execute(
            'INSERT INTO ivr_callback_alert_events (alert_key, alert_type, severity, observed_json, notification_delivery_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (
                alert_key,
                str(alert.get('type') or ''),
                str(alert.get('severity') or ''),
                json.dumps({'alert': alert, 'metrics': result.get('metrics', {})}),
                str(payload.get('delivery_id') or ''),
                created_at,
            ),
        )
        notifications.append({'alert': alert, 'notification': payload})

    db.commit()
    return {'sent': notifications, 'alerts': alerts, 'metrics': result.get('metrics', {}), 'skipped': skipped}


def list_callback_alert_history(
    limit: int = 100,
    alert_type: str | None = None,
    severity: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    delivery_status: str | None = None,
):
    db = get_db()
    safe_limit = min(max(int(limit or 100), 1), 5000)

    clauses = []
    params = []
    if alert_type:
        clauses.append('e.alert_type = ?')
        params.append(str(alert_type).strip())
    if severity:
        clauses.append('e.severity = ?')
        params.append(str(severity).strip())
    if date_from:
        clauses.append('e.created_at >= ?')
        params.append(str(date_from).strip())
    if date_to:
        clauses.append('e.created_at <= ?')
        params.append(str(date_to).strip())
    if delivery_status:
        clauses.append("COALESCE(d.status, '') = ?")
        params.append(str(delivery_status).strip())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    rows = db.execute(
        f'''
        SELECT e.id, e.alert_key, e.alert_type, e.severity, e.observed_json, e.notification_delivery_id, e.created_at,
               d.status AS delivery_status
        FROM ivr_callback_alert_events e
        LEFT JOIN notification_deliveries d ON d.delivery_id = e.notification_delivery_id
        {where_sql}
        ORDER BY e.id DESC
        LIMIT ?
        ''',
        tuple(params + [safe_limit]),
    ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        try:
            item['observed'] = json.loads(str(item.get('observed_json') or '{}'))
        except Exception:
            item['observed'] = {}
        item.pop('observed_json', None)
        items.append(item)

    return {'items': items}


