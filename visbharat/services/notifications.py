import hashlib
import hmac
import json
import random
import secrets
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

from flask import current_app

from ..db import get_db


def _resolve_connector_secret(secret_name: str, fallback_value: str = ''):
    name = str(secret_name or '').strip()
    if not name:
        return str(fallback_value or '').strip()

    if not bool(current_app.config.get('NOTIFICATION_SECRET_PROVIDER_ENABLED', False)):
        return str(fallback_value or '').strip()

    provider = current_app.extensions.get('secret_provider')
    if provider is None:
        return str(fallback_value or '').strip()

    prefix = str(current_app.config.get('NOTIFICATION_SECRET_PROVIDER_PREFIX') or 'visbharat/connectors').strip().strip('/')
    full_name = f"{prefix}/{name}" if prefix else name
    try:
        if callable(provider):
            value = provider(full_name)
        elif hasattr(provider, 'get_secret'):
            value = provider.get_secret(full_name)
        else:
            value = None
        resolved = str(value or '').strip()
        return resolved if resolved else str(fallback_value or '').strip()
    except Exception:
        return str(fallback_value or '').strip()


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 5):
    data = json.dumps(payload).encode('utf-8')
    req = urlrequest.Request(url=url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    for key, value in (headers or {}).items():
        req.add_header(str(key), str(value))
    with urlrequest.urlopen(req, timeout=timeout) as resp:  # nosec B310
        body = (resp.read() or b'').decode('utf-8', errors='ignore')
        return int(getattr(resp, 'status', 200) or 200), body


def _connector_prefix(kind: str):
    mapping = {
        'email': 'EMAIL_CONNECTOR',
        'sms': 'SMS_CONNECTOR',
        'whatsapp': 'WHATSAPP_CONNECTOR',
    }
    return mapping.get(str(kind or '').strip().lower(), '')


def _connector_settings(kind: str):
    prefix = _connector_prefix(kind)
    if not prefix:
        raise RuntimeError(f'unknown connector kind: {kind}')

    endpoint = str(current_app.config.get(f'{prefix}_ENDPOINT') or '').strip()
    primary_key = _resolve_connector_secret(f"{str(kind).lower()}/api_key_primary", str(current_app.config.get(f'{prefix}_API_KEY') or '').strip())
    secondary_key = _resolve_connector_secret(f"{str(kind).lower()}/api_key_secondary", str(current_app.config.get(f'{prefix}_API_KEY_NEXT') or '').strip())
    configured_slot = str(current_app.config.get(f'{prefix}_ACTIVE_KEY_SLOT') or 'primary').strip().lower()
    if configured_slot not in {'primary', 'secondary'}:
        configured_slot = 'primary'

    default_timeout = max(int(current_app.config.get('NOTIFICATION_CONNECTOR_TIMEOUT_SECONDS', 8) or 8), 1)
    provider_timeout = max(int(current_app.config.get(f'{prefix}_TIMEOUT_SECONDS', default_timeout) or default_timeout), 1)

    signing_secret = _resolve_connector_secret(f"{str(kind).lower()}/signing_secret", str(current_app.config.get(f'{prefix}_SIGNING_SECRET') or '').strip())

    return {
        'prefix': prefix,
        'endpoint': endpoint,
        'primary_key': primary_key,
        'secondary_key': secondary_key,
        'configured_slot': configured_slot,
        'timeout_seconds': provider_timeout,
        'signing_secret': signing_secret,
    }


def _is_auth_error(status_code: int):
    return int(status_code or 0) in {401, 403}


def _classify_connector_error(status_code: int, body_text: str):
    code = int(status_code or 0)
    body = str(body_text or '').lower()
    if code in {401, 403}:
        return 'auth'
    if code == 429:
        return 'rate_limit'
    if code >= 500:
        return 'transient'
    if code >= 400:
        return 'permanent'
    if 'timeout' in body or 'temporar' in body:
        return 'transient'
    return 'unknown'


def _read_connector_health(kind: str):
    db = get_db()
    row = db.execute(
        '''
        SELECT active_key_slot, failure_count, open_until_epoch, last_error
        FROM notification_connector_health
        WHERE provider = ?
        LIMIT 1
        ''',
        (str(kind),),
    ).fetchone()
    if not row:
        return {'active_key_slot': '', 'failure_count': 0, 'open_until_epoch': 0, 'last_error': ''}
    return {
        'active_key_slot': str(row['active_key_slot'] or ''),
        'failure_count': int(row['failure_count'] or 0),
        'open_until_epoch': int(row['open_until_epoch'] or 0),
        'last_error': str(row['last_error'] or ''),
    }


def _write_connector_health(kind: str, active_key_slot: str, failure_count: int, open_until_epoch: int, last_error: str):
    db = get_db()
    now = _now_iso()
    exists = db.execute('SELECT provider FROM notification_connector_health WHERE provider = ? LIMIT 1', (str(kind),)).fetchone()
    if exists:
        db.execute(
            '''
            UPDATE notification_connector_health
            SET active_key_slot = ?, failure_count = ?, open_until_epoch = ?, last_error = ?, updated_at = ?
            WHERE provider = ?
            ''',
            (str(active_key_slot), int(failure_count), int(open_until_epoch), str(last_error or ''), now, str(kind)),
        )
    else:
        db.execute(
            '''
            INSERT INTO notification_connector_health (provider, active_key_slot, failure_count, open_until_epoch, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (str(kind), str(active_key_slot), int(failure_count), int(open_until_epoch), str(last_error or ''), now),
        )


def _current_active_slot(kind: str, settings: dict):
    health = _read_connector_health(kind)
    persisted = str(health.get('active_key_slot') or '').strip().lower()
    if persisted in {'primary', 'secondary'}:
        if persisted == 'secondary' and not settings.get('secondary_key'):
            return 'primary'
        return persisted
    return settings.get('configured_slot') or 'primary'


def _set_active_slot(kind: str, slot: str):
    if slot not in {'primary', 'secondary'}:
        return
    health = _read_connector_health(kind)
    _write_connector_health(kind, slot, int(health.get('failure_count') or 0), int(health.get('open_until_epoch') or 0), str(health.get('last_error') or ''))


def _connector_circuit_open(kind: str):
    if not bool(current_app.config.get('NOTIFICATION_CIRCUIT_BREAKER_ENABLED', True)):
        return False, ''
    circuit = _read_connector_health(kind)
    open_until = int(circuit.get('open_until_epoch') or 0)
    now_epoch = int(time.time())
    if open_until > now_epoch:
        return True, f'{kind} circuit open until {open_until}'
    return False, ''


def _record_connector_success(kind: str):
    if not bool(current_app.config.get('NOTIFICATION_CIRCUIT_BREAKER_ENABLED', True)):
        return
    health = _read_connector_health(kind)
    active = str(health.get('active_key_slot') or 'primary')
    _write_connector_health(kind, active, 0, 0, '')


def _record_connector_failure(kind: str, error_text: str):
    if not bool(current_app.config.get('NOTIFICATION_CIRCUIT_BREAKER_ENABLED', True)):
        return
    fail_threshold = max(int(current_app.config.get('NOTIFICATION_CIRCUIT_FAIL_THRESHOLD', 3) or 3), 1)
    open_seconds = max(int(current_app.config.get('NOTIFICATION_CIRCUIT_OPEN_SECONDS', 60) or 60), 1)

    health = _read_connector_health(kind)
    failure_count = int(health.get('failure_count') or 0) + 1
    open_until = int(health.get('open_until_epoch') or 0)
    if failure_count >= fail_threshold:
        open_until = int(time.time()) + open_seconds
    _write_connector_health(kind, str(health.get('active_key_slot') or 'primary'), failure_count, open_until, str(error_text or ''))




def _signed_outbound_headers(kind: str, payload: dict, settings: dict):
    if not bool(current_app.config.get('OUTBOUND_CONNECTOR_SIGNING_ENABLED', True)):
        return {}
    secret = str(settings.get('signing_secret') or '').strip()
    if not secret:
        return {}

    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(8)
    canonical = json.dumps(payload or {}, sort_keys=True, separators=(',', ':'))
    base = canonical
    if bool(current_app.config.get('OUTBOUND_CONNECTOR_SIGN_BIND_NONCE', True)):
        base = f"{timestamp}:{nonce}:{canonical}"

    digest = hmac.new(secret.encode('utf-8'), base.encode('utf-8'), hashlib.sha256).hexdigest()
    return {
        'X-Notification-Timestamp': timestamp,
        'X-Notification-Nonce': nonce,
        'X-Notification-Signature': f'sha256={digest}',
        'X-Notification-Provider': str(kind),
    }



def _twilio_client(account_sid: str, auth_token: str):
    try:
        from twilio.rest import Client  # type: ignore
    except Exception as err:
        raise RuntimeError('twilio SDK is not installed') from err
    return Client(account_sid, auth_token)



def _parse_provider_response_payload(raw_body: str):
    body = str(raw_body or '').strip()
    if not body:
        return {'external_id': '', 'status': 'sent', 'raw': ''}

    try:
        parsed = json.loads(body)
    except Exception:
        return {'external_id': body[:120], 'status': 'sent', 'raw': body[:500]}

    external_id = ''
    status = 'sent'
    if isinstance(parsed, dict):
        external_id = str(
            parsed.get('id')
            or parsed.get('message_id')
            or parsed.get('sid')
            or parsed.get('external_id')
            or parsed.get('request_id')
            or ''
        ).strip()
        status = str(parsed.get('status') or parsed.get('state') or parsed.get('message_status') or 'sent').strip().lower() or 'sent'
    return {'external_id': external_id[:120], 'status': status, 'raw': body[:500]}
def _attempt_http_adapter(kind: str, payload: dict, settings: dict, api_key: str):
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
    headers.update(_signed_outbound_headers(kind, payload, settings))
    status, body = _post_json(settings['endpoint'], payload, headers=headers, timeout=int(settings['timeout_seconds']))
    if status < 400:
        parsed = _parse_provider_response_payload(body)
        return {'provider': str(kind), 'status': 'sent' if parsed['status'] in {'sent', 'queued', 'accepted', 'ok'} else parsed['status'], 'external_id': parsed['external_id'] or body[:120], 'provider_response': parsed}
    classification = _classify_connector_error(status, body)
    raise RuntimeError(f'{kind} connector returned {status} [{classification}]')


def _attempt_email_sendgrid_adapter(payload: dict, settings: dict):
    endpoint = str(current_app.config.get('EMAIL_CONNECTOR_ENDPOINT') or '').strip() or 'https://api.sendgrid.com/v3/mail/send'
    api_key = _resolve_connector_secret('email/sendgrid_api_key', str(current_app.config.get('EMAIL_CONNECTOR_API_KEY') or '').strip())
    sender = _resolve_connector_secret('email/sendgrid_from', str(current_app.config.get('EMAIL_SMTP_FROM') or '').strip())
    recipient = str(((payload.get('target') or {}).get('email') or (payload.get('target') or {}).get('recipient_email') or '')).strip()
    message = str(((payload.get('template') or {}).get('message')) or '')
    if not api_key or not sender or not recipient:
        raise RuntimeError('sendgrid adapter requires api key, from and recipient email')

    body = {
        'personalizations': [{'to': [{'email': recipient}]}],
        'from': {'email': sender},
        'subject': f"VisBharat SLA {str(payload.get('stage') or '').strip()}",
        'content': [{'type': 'text/plain', 'value': message}],
    }
    headers = {'Authorization': f'Bearer {api_key}'}
    status, resp = _post_json(endpoint, body, headers=headers, timeout=int(settings.get('timeout_seconds') or 8))
    if status in {200, 201, 202}:
        parsed = _parse_provider_response_payload(resp)
        return {'provider': 'email', 'status': 'sent', 'external_id': parsed['external_id'] or (resp or '')[:120] or f'SENDGRID-{int(time.time())}', 'provider_response': parsed}
    classification = _classify_connector_error(status, resp)
    raise RuntimeError(f'email connector returned {status} [{classification}]')


def _attempt_email_smtp_adapter(payload: dict, settings: dict):
    host = _resolve_connector_secret('email/smtp_host', str(current_app.config.get('EMAIL_SMTP_HOST') or '').strip())
    port = int(current_app.config.get('EMAIL_SMTP_PORT', 587) or 587)
    username = _resolve_connector_secret('email/smtp_username', str(current_app.config.get('EMAIL_SMTP_USERNAME') or '').strip())
    password = _resolve_connector_secret('email/smtp_password', str(current_app.config.get('EMAIL_SMTP_PASSWORD') or '').strip())
    sender = _resolve_connector_secret('email/smtp_from', str(current_app.config.get('EMAIL_SMTP_FROM') or '').strip())
    recipient = str(((payload.get('target') or {}).get('email') or (payload.get('target') or {}).get('recipient_email') or '')).strip()
    if not host or not sender or not recipient:
        raise RuntimeError('smtp adapter requires host, from and recipient email')

    msg = EmailMessage()
    msg['Subject'] = f"VisBharat SLA {str(payload.get('stage') or '').strip()}"
    msg['From'] = sender
    msg['To'] = recipient
    msg.set_content(str(((payload.get('template') or {}).get('message')) or ''))

    with smtplib.SMTP(host, port, timeout=int(settings.get('timeout_seconds') or 8)) as server:
        try:
            server.starttls()
        except Exception:
            pass
        if username and password:
            server.login(username, password)
        server.send_message(msg)
    return {'provider': 'email', 'status': 'sent', 'external_id': f'SMTP-{int(time.time())}'}


def _attempt_sms_twilio_adapter(payload: dict):
    account_sid = _resolve_connector_secret('sms/twilio_account_sid', str(current_app.config.get('SMS_TWILIO_ACCOUNT_SID') or '').strip())
    auth_token = _resolve_connector_secret('sms/twilio_auth_token', str(current_app.config.get('SMS_TWILIO_AUTH_TOKEN') or '').strip())
    from_number = _resolve_connector_secret('sms/twilio_from_number', str(current_app.config.get('SMS_TWILIO_FROM_NUMBER') or '').strip())
    to_number = str(((payload.get('target') or {}).get('phone') or (payload.get('target') or {}).get('to_phone') or '')).strip()
    body = str(((payload.get('template') or {}).get('message')) or '')
    if not account_sid or not auth_token or not from_number or not to_number:
        raise RuntimeError('twilio sms adapter requires sid, token, from_number, to_number')
    client = _twilio_client(account_sid, auth_token)
    msg = client.messages.create(body=body, from_=from_number, to=to_number)
    return {'provider': 'sms', 'status': 'sent', 'external_id': str(getattr(msg, 'sid', '') or '')[:120]}


def _attempt_whatsapp_cloud_adapter(payload: dict, settings: dict):
    endpoint = str(current_app.config.get('WHATSAPP_CONNECTOR_ENDPOINT') or '').strip()
    token = _resolve_connector_secret('whatsapp/cloud_api_token', str(current_app.config.get('WHATSAPP_CONNECTOR_API_KEY') or '').strip())
    to_number = str(((payload.get('target') or {}).get('phone') or (payload.get('target') or {}).get('wa_id') or '')).strip()
    message = str(((payload.get('template') or {}).get('message')) or '')
    if not endpoint or not token or not to_number:
        raise RuntimeError('whatsapp cloud adapter requires endpoint, token and recipient')

    body = {
        'messaging_product': 'whatsapp',
        'to': to_number.replace('whatsapp:', ''),
        'type': 'text',
        'text': {'preview_url': False, 'body': message},
    }
    headers = {'Authorization': f'Bearer {token}'}
    status, resp = _post_json(endpoint, body, headers=headers, timeout=int(settings.get('timeout_seconds') or 8))
    if status in {200, 201, 202}:
        parsed = _parse_provider_response_payload(resp)
        return {'provider': 'whatsapp', 'status': 'sent', 'external_id': parsed['external_id'] or (resp or '')[:120] or f'WA-{int(time.time())}', 'provider_response': parsed}
    classification = _classify_connector_error(status, resp)
    raise RuntimeError(f'whatsapp connector returned {status} [{classification}]')


def _attempt_whatsapp_twilio_adapter(payload: dict):
    account_sid = _resolve_connector_secret('whatsapp/twilio_account_sid', str(current_app.config.get('WHATSAPP_TWILIO_ACCOUNT_SID') or '').strip())
    auth_token = _resolve_connector_secret('whatsapp/twilio_auth_token', str(current_app.config.get('WHATSAPP_TWILIO_AUTH_TOKEN') or '').strip())
    from_number = _resolve_connector_secret('whatsapp/twilio_from_number', str(current_app.config.get('WHATSAPP_TWILIO_FROM_NUMBER') or '').strip())
    to_number = str(((payload.get('target') or {}).get('phone') or (payload.get('target') or {}).get('wa_id') or '')).strip()
    body = str(((payload.get('template') or {}).get('message')) or '')
    if not account_sid or not auth_token or not from_number or not to_number:
        raise RuntimeError('twilio whatsapp adapter requires sid, token, from_number, to_number')
    client = _twilio_client(account_sid, auth_token)
    from_val = from_number if str(from_number).startswith('whatsapp:') else f'whatsapp:{from_number}'
    to_val = to_number if str(to_number).startswith('whatsapp:') else f'whatsapp:{to_number}'
    msg = client.messages.create(body=body, from_=from_val, to=to_val)
    return {'provider': 'whatsapp', 'status': 'sent', 'external_id': str(getattr(msg, 'sid', '') or '')[:120]}


def _attempt_connector(kind: str, payload: dict):
    enabled = bool(current_app.config.get('OUTBOUND_CONNECTORS_ENABLED', False))
    settings = _connector_settings(kind)

    if not enabled:
        return {'provider': str(kind), 'status': 'simulated', 'external_id': f"SIM-{str(kind).upper()}", 'auth_slot': 'none'}

    active_slot = _current_active_slot(kind, settings)
    slots_to_try = [active_slot]
    if settings.get('secondary_key'):
        other = 'secondary' if active_slot == 'primary' else 'primary'
        if other not in slots_to_try:
            slots_to_try.append(other)

    provider_key = str(current_app.config.get(f"{settings['prefix']}_PROVIDER") or 'http').strip().lower()
    last_error = ''

    for slot in slots_to_try:
        api_key = settings['secondary_key'] if slot == 'secondary' else settings['primary_key']
        try:
            if kind == 'email' and provider_key in {'smtp'}:
                result = _attempt_email_smtp_adapter(payload, settings)
            elif kind == 'email' and provider_key in {'sendgrid', 'sendgrid_sdk'}:
                result = _attempt_email_sendgrid_adapter(payload, settings)
            elif kind == 'sms' and provider_key in {'twilio', 'twilio_sdk'}:
                result = _attempt_sms_twilio_adapter(payload)
            elif kind == 'whatsapp' and provider_key in {'twilio', 'twilio_whatsapp', 'twilio_sdk'}:
                result = _attempt_whatsapp_twilio_adapter(payload)
            elif kind == 'whatsapp' and provider_key in {'meta', 'whatsapp_cloud', 'meta_cloud'}:
                result = _attempt_whatsapp_cloud_adapter(payload, settings)
            else:
                if not settings['endpoint']:
                    return {'provider': str(kind), 'status': 'simulated', 'external_id': f"SIM-{str(kind).upper()}", 'auth_slot': 'none'}
                result = _attempt_http_adapter(kind, payload, settings, api_key)

            _set_active_slot(kind, slot)
            result['auth_slot'] = slot
            return result
        except RuntimeError as err:
            last_error = str(err)
            # rotate keys only for auth errors from HTTP adapter path
            if ('[auth]' in last_error.lower() or '401' in last_error or '403' in last_error) and len(slots_to_try) > 1 and slot != slots_to_try[-1]:
                continue
            raise

    raise RuntimeError(last_error or f'{kind} connector failed')


def _render_message(stage: str, language: str = 'en', context=None):
    templates = current_app.config.get('NOTIFICATION_TEMPLATES', {})
    template = ((templates.get(language) or {}).get(stage) or (templates.get('en') or {}).get(stage) or 'Request {request_id}: SLA {stage} escalation.')
    payload = {'stage': stage}
    payload.update(context or {})
    try:
        message = str(template).format(**payload)
    except Exception:
        message = str(template)
    return {
        'version': str(current_app.config.get('NOTIFICATION_TEMPLATE_VERSION', 'v1')),
        'language': language,
        'message': message,
    }


def dispatch_sla_notification(request_id: str, stage: str, actor: str, source_channel: str, target: dict, sla_due_at: str, details=None):
    db = get_db()
    now = _now_iso()

    existing = db.execute(
        '''
        SELECT delivery_id, status, provider, attempt_count, max_retries, external_id, last_error
        FROM notification_deliveries
        WHERE request_id = ? AND stage = ? AND status IN ('sent', 'simulated', 'delivered')
        ORDER BY id DESC
        LIMIT 1
        ''',
        (request_id, stage),
    ).fetchone()
    if existing:
        return {
            'delivery_id': existing['delivery_id'],
            'status': existing['status'],
            'provider': existing['provider'],
            'attempt_count': int(existing['attempt_count'] or 0),
            'max_retries': int(existing['max_retries'] or 0),
            'external_id': existing['external_id'],
            'last_error': existing['last_error'],
            'idempotent_reused': True,
        }

    delivery_id = f"NOTIFY-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"

    preference = current_app.config.get('NOTIFICATION_CHANNEL_PREFERENCE', ['email', 'sms', 'whatsapp'])
    if not isinstance(preference, list) or not preference:
        preference = ['email', 'sms', 'whatsapp']

    max_retries = int(current_app.config.get('NOTIFICATION_MAX_RETRIES', 2) or 2)
    base_backoff = max(float(current_app.config.get('NOTIFICATION_RETRY_BACKOFF_SECONDS', 1) or 1), 0.0)
    backoff_multiplier = max(float(current_app.config.get('NOTIFICATION_RETRY_BACKOFF_MULTIPLIER', 2.0) or 2.0), 1.0)
    max_backoff = max(float(current_app.config.get('NOTIFICATION_RETRY_MAX_BACKOFF_SECONDS', 30) or 30), base_backoff)
    jitter = max(float(current_app.config.get('NOTIFICATION_RETRY_JITTER_SECONDS', 0.0) or 0.0), 0.0)

    target_lang = str((target or {}).get('language') or 'en').strip().lower()
    message_obj = _render_message(
        stage,
        language=target_lang,
        context={
            'request_id': request_id,
            'actor': actor,
            'source_channel': source_channel,
            'sla_due_at': sla_due_at,
        },
    )

    payload = {
        'request_id': request_id,
        'stage': stage,
        'source_channel': source_channel,
        'target': target or {},
        'sla_due_at': sla_due_at,
        'details': details or {},
        'template': message_obj,
    }

    status = 'failed'
    provider = ''
    external_id = None
    last_error = ''
    attempts = 0

    for kind in preference:
        provider = str(kind)

        circuit_open, circuit_error = _connector_circuit_open(provider)
        if circuit_open:
            last_error = circuit_error
            continue

        tries_for_kind = 0
        while tries_for_kind <= max_retries:
            tries_for_kind += 1
            attempts += 1
            try:
                result = _attempt_connector(str(kind), payload)
                provider = str(result.get('provider') or kind)
                status = 'sent' if result.get('status') == 'sent' else 'simulated'
                external_id = result.get('external_id')
                last_error = ''
                _record_connector_success(provider)
                break
            except (URLError, HTTPError, RuntimeError, ValueError) as err:
                last_error = str(err)
                _record_connector_failure(provider, last_error)
                if tries_for_kind <= max_retries:
                    backoff = min(base_backoff * (backoff_multiplier ** (tries_for_kind - 1)), max_backoff)
                    if jitter > 0:
                        backoff += random.uniform(0.0, jitter)
                    if backoff > 0:
                        time.sleep(backoff)
        if status in {'sent', 'simulated'}:
            break

    db.execute(
        '''
        INSERT INTO notification_deliveries (
            delivery_id, request_id, stage, provider, channel, target_json,
            payload_json, status, attempt_count, max_retries, external_id,
            last_error, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            delivery_id,
            request_id,
            stage,
            provider,
            source_channel,
            json.dumps(target or {}),
            json.dumps(payload),
            status,
            attempts,
            max_retries,
            external_id,
            last_error,
            now,
            now,
        ),
    )

    if status == 'failed':
        db.execute(
            '''
            INSERT INTO notification_dead_letters (
                delivery_id, request_id, stage, provider, payload_json, last_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (delivery_id, request_id, stage, provider, json.dumps(payload), last_error, now),
        )

    db.commit()

    return {
        'delivery_id': delivery_id,
        'status': status,
        'provider': provider,
        'attempt_count': attempts,
        'max_retries': max_retries,
        'external_id': external_id,
        'last_error': last_error,
        'template': message_obj,
    }




def _normalize_receipt_status(provider: str, status: str, raw_payload=None):
    norm_provider = str(provider or '').strip().lower()
    norm_status = str(status or '').strip().lower()

    base_map = {
        'delivered': 'delivered',
        'sent': 'sent',
        'queued': 'queued',
        'failed': 'failed',
        'undelivered': 'failed',
        'accepted': 'queued',
        'buffered': 'queued',
        'read': 'delivered',
    }

    provider_aliases = {
        'sms': {
            'delivrd': 'delivered',
            'delivery_failed': 'failed',
            'undelivered': 'failed',
            'received': 'sent',
            'queued': 'queued',
        },
        'whatsapp': {
            'sent': 'sent',
            'delivered': 'delivered',
            'read': 'delivered',
            'failed': 'failed',
            'failed_delivery': 'failed',
            'accepted': 'queued',
        },
        'email': {
            'processed': 'queued',
            'deferred': 'queued',
            'dropped': 'failed',
            'bounced': 'failed',
            'blocked': 'failed',
            'spamreport': 'failed',
            'delivered': 'delivered',
            'open': 'delivered',
            'opened': 'delivered',
            'click': 'delivered',
            'clicked': 'delivered',
        },
    }

    mapped = provider_aliases.get(norm_provider, {}).get(norm_status) or base_map.get(norm_status, 'unknown')
    payload = raw_payload or {}
    metadata = {
        'provider': norm_provider,
        'raw_status': norm_status,
        'mapped_status': mapped,
        'event_type': str((payload or {}).get('event_type') or (payload or {}).get('type') or '').strip().lower(),
        'message_class': str((payload or {}).get('message_class') or '').strip().lower(),
        'channel_meta': {
            'smtp_code': (payload or {}).get('smtp_code'),
            'carrier_status': (payload or {}).get('carrier_status'),
            'conversation_category': (payload or {}).get('conversation_category'),
            'provider_message_id': (payload or {}).get('message_id') or (payload or {}).get('sid') or (payload or {}).get('wamid'),
            'provider_error_code': (payload or {}).get('error_code') or (payload or {}).get('smtp_code') or (payload or {}).get('status_code'),
        },
    }
    return mapped, metadata

def register_notification_receipt_event(delivery_id: str, provider: str, status: str, external_id: str = '', raw_payload=None):
    payload_obj = raw_payload or {}
    canonical_payload = json.dumps(payload_obj, sort_keys=True, separators=(',', ':'))
    event_key = hashlib.sha256(
        f"{str(provider or '').strip().lower()}|{str(delivery_id)}|{str(status).strip().lower()}|{str(external_id)}|{canonical_payload}".encode('utf-8')
    ).hexdigest()

    db = get_db()
    existing = db.execute('SELECT event_key FROM notification_receipt_events WHERE event_key = ? LIMIT 1', (event_key,)).fetchone()
    if existing:
        return {'accepted': False, 'event_key': event_key}

    db.execute(
        '''
        INSERT INTO notification_receipt_events (event_key, delivery_id, provider, status, external_id, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (event_key, str(delivery_id), str(provider or ''), str(status or ''), str(external_id or ''), canonical_payload, _now_iso()),
    )
    db.commit()
    return {'accepted': True, 'event_key': event_key}


def reconcile_notification_receipt(delivery_id: str, provider: str, status: str, external_id: str = '', raw_payload=None):
    db = get_db()
    mapped, metadata = _normalize_receipt_status(provider, status, raw_payload=raw_payload)

    row = db.execute(
        'SELECT delivery_id, request_id, stage, provider, status, payload_json FROM notification_deliveries WHERE delivery_id = ? LIMIT 1',
        (delivery_id,),
    ).fetchone()
    if not row:
        return None

    merged_payload = {}
    try:
        merged_payload = json.loads(row['payload_json'] or '{}') if row.get('payload_json') is not None else {}
    except Exception:
        merged_payload = {}
    merged_payload['receipt'] = {
        'metadata': metadata,
        'raw_payload': raw_payload or {},
        'reconciled_at': _now_iso(),
    }

    db.execute(
        '''
        UPDATE notification_deliveries
        SET provider = ?, status = ?, external_id = ?, last_error = ?, payload_json = ?, updated_at = ?
        WHERE delivery_id = ?
        ''',
        (
            provider or row['provider'],
            mapped,
            external_id or '',
            '' if mapped != 'failed' else 'receipt marked failed',
            json.dumps(merged_payload),
            _now_iso(),
            delivery_id,
        ),
    )
    db.commit()

    return {
        'delivery_id': delivery_id,
        'request_id': row['request_id'],
        'stage': row['stage'],
        'provider': provider or row['provider'],
        'status': mapped,
        'metadata': metadata,
        'raw_payload': raw_payload or {},
    }


def get_notification_metrics(limit=1000):
    db = get_db()
    rows = db.execute(
        '''
        SELECT provider, status, COUNT(*) AS c
        FROM notification_deliveries
        GROUP BY provider, status
        ORDER BY c DESC
        LIMIT ?
        ''',
        (min(max(int(limit), 1), 5000),),
    ).fetchall()

    by_provider = {}
    by_status = {}
    total = 0
    for row in rows:
        provider = str(row['provider'] or 'unknown')
        status = str(row['status'] or 'unknown')
        count = int(row['c'] or 0)
        total += count

        if provider not in by_provider:
            by_provider[provider] = {'total': 0, 'by_status': {}}
        by_provider[provider]['total'] += count
        by_provider[provider]['by_status'][status] = by_provider[provider]['by_status'].get(status, 0) + count

        by_status[status] = by_status.get(status, 0) + count

    return {'total': total, 'by_provider': by_provider, 'by_status': by_status}


def get_notification_connector_health(limit=200):
    db = get_db()
    rows = db.execute(
        '''
        SELECT provider, active_key_slot, failure_count, open_until_epoch, last_error, updated_at
        FROM notification_connector_health
        ORDER BY provider ASC
        LIMIT ?
        ''',
        (min(max(int(limit), 1), 1000),),
    ).fetchall()
    now_epoch = int(time.time())
    items = []
    for row in rows:
        obj = dict(row)
        obj['circuit_open'] = int(obj.get('open_until_epoch') or 0) > now_epoch
        items.append(obj)
    return {'count': len(items), 'items': items}


def list_notification_receipt_events(limit=200, provider: str = '', delivery_id: str = ''):
    db = get_db()
    clauses = []
    params = []
    if provider:
        clauses.append('LOWER(provider) = ?')
        params.append(str(provider).strip().lower())
    if delivery_id:
        clauses.append('delivery_id = ?')
        params.append(str(delivery_id).strip())
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    rows = db.execute(
        f'''
        SELECT event_key, delivery_id, provider, status, external_id, payload_json, created_at
        FROM notification_receipt_events
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
        ''',
        tuple(params + [min(max(int(limit), 1), 1000)]),
    ).fetchall()

    items = []
    for row in rows:
        obj = dict(row)
        try:
            obj['payload'] = json.loads(obj.get('payload_json') or '{}')
        except Exception:
            obj['payload'] = {}
        obj.pop('payload_json', None)
        items.append(obj)
    return {'count': len(items), 'items': items}


def get_notification_slo_dashboard(limit=200):
    metrics = get_notification_metrics(limit=limit)
    health = get_notification_connector_health(limit=limit)

    total = int(metrics.get('total') or 0)
    failed = int((metrics.get('by_status') or {}).get('failed') or 0)
    failure_rate = (failed / float(total)) if total > 0 else 0.0

    min_deliveries = max(int(current_app.config.get('NOTIFICATION_SLO_MIN_DELIVERIES', 20) or 20), 1)
    max_failure_rate = float(current_app.config.get('NOTIFICATION_SLO_MAX_FAILURE_RATE', 0.1) or 0.1)
    max_open_circuits = max(int(current_app.config.get('NOTIFICATION_SLO_MAX_OPEN_CIRCUITS', 0) or 0), 0)

    open_circuits = [item for item in (health.get('items') or []) if bool(item.get('circuit_open'))]

    alerts = []
    if total >= min_deliveries and failure_rate > max_failure_rate:
        alerts.append({'type': 'failure_rate_breach', 'severity': 'high', 'failure_rate': round(failure_rate, 4), 'threshold': max_failure_rate, 'total': total})
    if len(open_circuits) > max_open_circuits:
        alerts.append({'type': 'open_circuit_breach', 'severity': 'medium', 'open_circuits': len(open_circuits), 'threshold': max_open_circuits})

    return {
        'slo': {
            'min_deliveries': min_deliveries,
            'max_failure_rate': max_failure_rate,
            'max_open_circuits': max_open_circuits,
        },
        'observed': {
            'total_deliveries': total,
            'failed_deliveries': failed,
            'failure_rate': round(failure_rate, 4),
            'open_circuits': len(open_circuits),
        },
        'alerts': alerts,
        'metrics': metrics,
        'connectors': health,
    }




def _slo_fanout_targets():
    configured = current_app.config.get('NOTIFICATION_SLO_ALERT_TARGETS') or {}
    targets = []
    if isinstance(configured, dict):
        raw = configured.get('targets')
        if isinstance(raw, list):
            targets = [item for item in raw if isinstance(item, dict)]

    if not targets:
        fallback = current_app.config.get('NOTIFICATION_SLO_ALERT_TARGET') or {}
        if isinstance(fallback, dict) and fallback:
            targets = [fallback]

    if not targets:
        targets = [{'email': str(current_app.config.get('EMAIL_SMTP_FROM') or ''), 'language': 'en'}]

    normalized = []
    for idx, target in enumerate(targets):
        obj = dict(target)
        obj['priority'] = int(obj.get('priority', 100 - idx) or 0)
        obj['weight'] = int(obj.get('weight', 1) or 1)
        obj['active'] = bool(obj.get('active', True))
        obj['_idx'] = idx
        normalized.append(obj)

    normalized = [item for item in normalized if item.get('active')]
    normalized.sort(key=lambda item: (-int(item.get('priority') or 0), -int(item.get('weight') or 0), int(item.get('_idx') or 0)))
    max_targets = max(int(current_app.config.get('NOTIFICATION_SLO_ALERT_MAX_TARGETS_PER_ALERT', 3) or 3), 1)
    return normalized[:max_targets]

def emit_notification_slo_alerts(dashboard: dict, actor: str = 'system'):
    alerts = list((dashboard or {}).get('alerts') or [])
    if not alerts:
        return {'sent': 0, 'items': [], 'skipped': []}

    targets = _slo_fanout_targets()

    dedupe_seconds = max(int(current_app.config.get('NOTIFICATION_SLO_ALERT_DEDUPE_SECONDS', 1800) or 1800), 0)
    cooldown_seconds = max(int(current_app.config.get('NOTIFICATION_SLO_ALERT_COOLDOWN_SECONDS', 900) or 900), 0)
    max_per_run = max(int(current_app.config.get('NOTIFICATION_SLO_ALERT_MAX_PER_RUN', 5) or 5), 1)

    now_epoch = int(time.time())
    db = get_db()

    items = []
    skipped = []
    for alert in alerts:
        observed = (dashboard or {}).get('observed') or {}
        alert_identity = {'type': str(alert.get('type') or ''), 'severity': str(alert.get('severity') or ''), 'threshold': alert.get('threshold')}

        for target in targets:
            if len(items) >= max_per_run:
                skipped.append({'alert': alert, 'target': target, 'reason': 'max_per_run'})
                continue

            target_fp = hashlib.sha256(json.dumps(target, sort_keys=True).encode('utf-8')).hexdigest()
            alert_key = hashlib.sha256(json.dumps({'identity': alert_identity, 'target_fp': target_fp}, sort_keys=True).encode('utf-8')).hexdigest()

            existing = db.execute(
                'SELECT created_at FROM notification_slo_alert_events WHERE alert_key = ? ORDER BY id DESC LIMIT 1',
                (alert_key,),
            ).fetchone()
            if existing:
                try:
                    ts = datetime.fromisoformat(str(existing['created_at']).replace('Z', '+00:00')).timestamp()
                except Exception:
                    ts = 0
                age = now_epoch - int(ts)
                if dedupe_seconds > 0 and age < dedupe_seconds:
                    skipped.append({'alert': alert, 'target': target, 'reason': 'dedupe_window'})
                    continue
                if cooldown_seconds > 0 and age < cooldown_seconds:
                    skipped.append({'alert': alert, 'target': target, 'reason': 'cooldown_window'})
                    continue

            stage = f"slo_{str(alert.get('type') or 'alert')}_{target_fp[:6]}"[:80]
            payload = dispatch_sla_notification(
                request_id=f"OPS-NOTIFICATION-SLO-{target_fp[:8]}",
                stage=stage,
                actor=actor,
                source_channel='ops-slo-dashboard',
                target=target,
                sla_due_at=_now_iso(),
                details={'slo_alert': alert, 'observed': observed, 'target_priority': target.get('priority'), 'target_weight': target.get('weight')},
            )

            if bool(payload.get('idempotent_reused')):
                skipped.append({'alert': alert, 'target': target, 'reason': 'idempotent_reused'})
                continue

            db.execute(
                '''
                INSERT INTO notification_slo_alert_events (alert_key, alert_type, severity, observed_json, notification_delivery_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (
                    alert_key,
                    str(alert.get('type') or ''),
                    str(alert.get('severity') or ''),
                    json.dumps({'observed': observed, 'target': target}),
                    str(payload.get('delivery_id') or ''),
                    _now_iso(),
                ),
            )
            db.commit()

            items.append({'alert': alert, 'target': target, 'notification': payload})

    return {'sent': len(items), 'items': items, 'skipped': skipped}




def get_notification_ops_overlay(limit=100):
    safe_limit = min(max(int(limit), 1), 500)
    metrics = get_notification_metrics(limit=safe_limit)
    connectors = get_notification_connector_health(limit=safe_limit)
    slo = get_notification_slo_dashboard(limit=safe_limit)

    db = get_db()
    recent_deliveries = db.execute(
        '''
        SELECT delivery_id, request_id, stage, provider, status, attempt_count, created_at, updated_at
        FROM notification_deliveries
        ORDER BY id DESC
        LIMIT ?
        ''',
        (safe_limit,),
    ).fetchall()

    receipt_events = db.execute(
        '''
        SELECT event_key, delivery_id, provider, status, external_id, created_at
        FROM notification_receipt_events
        ORDER BY id DESC
        LIMIT ?
        ''',
        (safe_limit,),
    ).fetchall()

    return {
        'metrics': metrics,
        'connectors': connectors,
        'slo': slo,
        'recent_deliveries': [dict(r) for r in recent_deliveries],
        'recent_receipt_events': [dict(r) for r in receipt_events],
        'meta': {'limit': safe_limit},
    }

