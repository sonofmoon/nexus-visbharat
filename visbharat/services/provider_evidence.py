"""Persist invocation evidence without storing prompts, audio, credentials or errors."""
from datetime import datetime, timezone
from functools import wraps
import time
from uuid import uuid4

from flask import current_app
from ..db import _open_connection, get_db


CLIENTS = {
    'google_ai': ('classify_request', 'translate_text', 'generate_policy_brief', 'transcribe_audio_bytes'),
    'google_stt': ('transcribe_bytes',),
    'google_tts': ('synthesize',),
    'google_dialogflow': ('detect_intent',),
    'google_translation': ('translate_text',),
    'google_vertex': ('predict_stress',),
    'google_bigquery': ('aggregate_requests', 'insert_request', 'update_request_progress'),
    'google_maps': ('geocode_address',),
}


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS provider_invocations (
        trace_id TEXT PRIMARY KEY, provider TEXT NOT NULL, operation TEXT NOT NULL,
        status TEXT NOT NULL, model TEXT, checked_at TEXT NOT NULL,
        latency_ms REAL NOT NULL, fallback_used INTEGER NOT NULL,
        transport TEXT, error_type TEXT
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_provider_invocations_time ON provider_invocations(checked_at)')
    db.commit()


def instrument(client, provider):
    """Instrument public operations after their response/validation completes."""
    for operation in CLIENTS.get(provider, ()):
        original = getattr(client, operation, None)
        if not callable(original) or getattr(original, '_nvb_evidence', False):
            continue

        def decorate(fn, name):
            @wraps(fn)
            def call(*args, **kwargs):
                start = time.monotonic()
                event = dict(trace_id=uuid4().hex, provider=provider, operation=name,
                             status='unavailable', model=None, fallback_used=False,
                             transport=None, error_type=None)
                result = None
                try:
                    result = fn(*args, **kwargs)
                    data = result if isinstance(result, dict) else {}
                    mode = str(data.get('provider_mode') or '')
                    fallback = bool(data.get('fallback_used')) or any(s in mode for s in ('fallback', 'simulation', 'proxy'))
                    event.update(status='degraded' if fallback else 'verified',
                                 model=data.get('model'), fallback_used=fallback,
                                 transport=mode or None)
                    required = {'transcribe_bytes':'transcript','transcribe_audio_bytes':'transcript','translate_text':'translated_text'}.get(name)
                    if required and not str(data.get(required) or '').strip():
                        event['status'] = 'unavailable'
                    if result is False or result is None:
                        event['status'] = 'unavailable'
                    return result
                except Exception as error:
                    event['error_type'] = type(error).__name__
                    raise
                finally:
                    event['checked_at'] = datetime.now(timezone.utc).isoformat()
                    event['latency_ms'] = round((time.monotonic() - start) * 1000, 2)
                    recorded = False
                    db = None
                    try:
                        # Separate transaction: telemetry must never commit caller work.
                        db = _open_connection()
                        db.execute('''INSERT INTO provider_invocations
                            (trace_id,provider,operation,status,model,checked_at,latency_ms,fallback_used,transport,error_type)
                            VALUES (?,?,?,?,?,?,?,?,?,?)''',
                            tuple(event[k] for k in ('trace_id','provider','operation','status','model','checked_at','latency_ms'))
                            + (int(event['fallback_used']), event['transport'], event['error_type']))
                        db.commit()
                        recorded = True
                    except Exception:
                        current_app.logger.warning('Provider evidence could not be persisted: %s/%s', provider, name)
                    finally:
                        if db is not None:
                            db.close()
                    if isinstance(result, dict):
                        result['provider_evidence'] = {**event, 'persisted': recorded}
            call._nvb_evidence = True
            return call
        setattr(client, operation, decorate(original, operation))
    return client


def install(app):
    for provider in CLIENTS:
        client = app.extensions.get(provider + '_client')
        if client is not None:
            instrument(client, provider)


def status():
    """Recent successful calls are evidence of execution, not model quality."""
    ttl = int(current_app.config.get('PROVIDER_VERIFICATION_TTL_SECONDS', 900))
    try:
        rows = get_db().execute('''SELECT * FROM provider_invocations
            ORDER BY checked_at DESC, trace_id DESC LIMIT 500''').fetchall()
    except Exception:
        rows = []
    latest = {}
    for row in rows:
        latest.setdefault((row['provider'], row['operation']), dict(row))
    now = datetime.now(timezone.utc)
    services = {}
    for provider, operations in CLIENTS.items():
        configured = current_app.extensions.get(provider + '_client') is not None
        details = {}
        for operation in operations:
            event = latest.get((provider, operation))
            state = 'configured' if configured else 'unavailable'
            if configured and event:
                age = (now - datetime.fromisoformat(event['checked_at'])).total_seconds()
                state = event['status'] if 0 <= age <= ttl else 'configured'
                event = {**event, 'stale': age > ttl, 'fallback_used': bool(event['fallback_used'])}
            details[operation] = {'status': state, 'last_invocation': event}
        states = [v['status'] for v in details.values()]
        # A verified operation must not hide a failure in another operation.
        attempted = [v['status'] for v in details.values() if v['last_invocation'] and not v['last_invocation'].get('stale')]
        overall = ('degraded' if any(s in ('degraded','unavailable') for s in attempted)
                   else 'verified' if 'verified' in states else 'configured' if configured else 'unavailable')
        services[provider] = {'configured': configured, 'status': overall, 'operations': details}
    return {'services': services, 'verification_ttl_seconds': ttl,
            'meaning': 'Verified means a recent successful provider operation, not independently validated AI quality.'}
