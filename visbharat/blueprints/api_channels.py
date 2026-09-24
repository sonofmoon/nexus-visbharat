import base64
import csv
import io
import json
import random
import hmac
import hashlib
import time
import ipaddress
from datetime import datetime, timezone, timedelta
import secrets
import os
from urllib import request as urlrequest

from flask import Blueprint, current_app, jsonify, request, g

from ..auth import require_auth, require_roles
from ..audit import write_audit_log
from ..db import get_db
from ..security import generate_api_token, hash_api_token, token_last4
from ..services.keyword_sms import parse_keyword_sms, keyword_help_message
from ..services.ivr_orchestrator import (
    orchestrate_missed_call_callback, get_callback_status, process_due_callbacks,
    get_callback_metrics, reconcile_provider_callback, parse_provider_callback_payload,
    get_callback_alerts, emit_callback_alerts, list_callback_alert_history
)
from ..services.demand_analytics import compute_demand_velocity, compute_silence_map, compute_latent_demand_surface
from ..services.intelligence import (
    compute_gap_analysis, compute_hotspot_predictions, compute_priority_rankings_with_budget,
    compute_impact_metrics_and_auto_brief, compute_demand_decay_impact, compute_bigquery_analytics,
    compute_vertex_predictions
)
from ..services.pipeline_queue import (
    enqueue_ingestion_job, get_pipeline_job, list_pipeline_jobs, list_worker_heartbeats,
    find_job_by_idempotency_key, retry_pipeline_job, cancel_pipeline_job, get_pipeline_metrics,
    process_ingestion_payload, get_outbox_replication_metrics, replicate_pending_outbox,
    fast_path_emergency_triage
)
from ..services.routing import resolve_ward_department_route
from ..services.request_queue import list_execution_requests
from ..services.sla import resolve_sla_policy
from ..services.scoring import (
    get_scoring_weights, upsert_scoring_weights, validate_scoring_weights, list_scoring_profiles,
    get_active_scoring_profile, activate_scoring_profile, rollback_scoring_profile
)
from ..services.notifications import (
    dispatch_sla_notification, reconcile_notification_receipt, get_notification_metrics,
    register_notification_receipt_event, get_notification_connector_health,
    list_notification_receipt_events, get_notification_slo_dashboard,
    emit_notification_slo_alerts, get_notification_ops_overlay
)
from ..services.geo_ward import resolve_ward_from_map, resolve_geo_ward_context
from ..services.clustering import (
    recompute_demand_clusters, list_demand_clusters, assign_request_to_cluster, merge_demand_clusters,
    split_demand_cluster, list_low_confidence_cluster_members, rescore_low_confidence_cluster_members,
    set_cluster_member_override, list_cluster_member_override_history, sweep_stale_cluster_member_overrides,
    export_cluster_member_override_history, get_cluster_member_override_metrics,
    get_cluster_member_override_alerts, get_cluster_member_override_backlog_summary
)
from ..services.governance import (
    record_consent_event, list_consent_events, privatize_count, should_apply_dp, dp_metadata,
    get_dpdp_controls, get_dpdp_evidence, get_dpdp_readiness, get_dpg_status
)
from ..services.code_mix import normalize_code_mix
from ..services.policy_outputs import compute_demand_investment_alignment_map, generate_rag_policy_brief
from ..services.layer4_fusion import layer4_source_status, compute_layer4_fusion
from ..services.pii_scrubber import scrub_text, scrub_payload_fields
from ..services.ai_simulation import (
    simulate_gemini_intent_classification, simulate_speech_to_text,
    simulate_translation, simulate_dialogflow_cx_turn
)
from .api import *

channels_bp = Blueprint('channels_bp', __name__)


def _require_webhook_token():
    expected = (current_app.config.get('WEBHOOK_SHARED_TOKEN') or '').strip()
    if not expected:
        return True
    provided = (request.headers.get('X-Webhook-Token') or '').strip()
    return hmac.compare_digest(provided, expected)


def _verify_meta_signature():
    app_secret = (current_app.config.get('META_APP_SECRET') or '').strip()
    if not app_secret:
        return True

    provided = (request.headers.get('X-Hub-Signature-256') or '').strip()
    if not provided.startswith('sha256='):
        return False

    payload = request.get_data() or b''
    bind_replay = bool(current_app.config.get('WEBHOOK_BIND_REPLAY_IN_SIGNATURE', False))
    if bind_replay:
        ts = (request.headers.get('X-Webhook-Timestamp') or '').strip()
        nonce = (request.headers.get('X-Webhook-Nonce') or '').strip()
        signature_payload = (ts + ':' + nonce + ':').encode('utf-8') + payload
    else:
        signature_payload = payload

    digest = hmac.new(app_secret.encode('utf-8'), signature_payload, hashlib.sha256).hexdigest()
    expected = f'sha256={digest}'
    return hmac.compare_digest(provided, expected)


def _verify_telegram_secret():
    secret = (current_app.config.get('TELEGRAM_WEBHOOK_SECRET') or '').strip()
    if not secret:
        return True
    provided = (request.headers.get('X-Telegram-Bot-Api-Secret-Token') or '').strip()
    return hmac.compare_digest(provided, secret)


def _verify_twilio_signature(data):
    auth_token = (current_app.config.get('TWILIO_AUTH_TOKEN') or '').strip()
    if not auth_token:
        return True

    provided = (request.headers.get('X-Twilio-Signature') or '').strip()
    if not provided:
        return False

    url = request.url
    normalized = data or {}
    if hasattr(normalized, 'items'):
        items = sorted((str(k), str(v)) for k, v in normalized.items())
    else:
        items = []

    to_sign = url + ''.join(k + v for k, v in items)

    if bool(current_app.config.get('WEBHOOK_BIND_REPLAY_IN_SIGNATURE', False)):
        ts = (request.headers.get('X-Webhook-Timestamp') or '').strip()
        nonce = (request.headers.get('X-Webhook-Nonce') or '').strip()
        to_sign = to_sign + ts + nonce
    digest = hmac.new(auth_token.encode('utf-8'), to_sign.encode('utf-8'), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode('utf-8')
    return hmac.compare_digest(provided, expected)



def _verify_exotel_signature(raw_payload: bytes):
    secret = (current_app.config.get('EXOTEL_WEBHOOK_SECRET') or '').strip()
    if not secret:
        return True

    provided = (request.headers.get('X-Exotel-Signature') or '').strip()
    if not provided:
        return False

    payload = raw_payload or b''
    if bool(current_app.config.get('WEBHOOK_BIND_REPLAY_IN_SIGNATURE', False)):
        ts = (request.headers.get('X-Webhook-Timestamp') or '').strip()
        nonce = (request.headers.get('X-Webhook-Nonce') or '').strip()
        payload = (ts + ':' + nonce + ':').encode('utf-8') + payload

    digest = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
    expected = f'sha256={digest}'
    return hmac.compare_digest(provided, expected)

def _notification_receipt_secret(provider: str):
    normalized = str(provider or '').strip().lower()
    if normalized == 'email':
        return (current_app.config.get('NOTIFICATION_RECEIPT_EMAIL_SECRET') or '').strip()
    if normalized == 'sms':
        return (current_app.config.get('NOTIFICATION_RECEIPT_SMS_SECRET') or '').strip()
    if normalized == 'whatsapp':
        return (current_app.config.get('NOTIFICATION_RECEIPT_WHATSAPP_SECRET') or '').strip()
    return (current_app.config.get('NOTIFICATION_RECEIPT_HMAC_SECRET') or '').strip()


def _verify_notification_receipt_signature(provider: str, raw_payload: bytes):
    secret = _notification_receipt_secret(provider)
    if not secret:
        return True

    provided = (request.headers.get('X-Notification-Signature') or '').strip()
    if not provided.startswith('sha256='):
        return False

    payload = raw_payload or b''
    if bool(current_app.config.get('NOTIFICATION_RECEIPT_BIND_REPLAY_IN_SIGNATURE', True)):
        ts = (request.headers.get('X-Notification-Timestamp') or '').strip()
        nonce = (request.headers.get('X-Notification-Nonce') or '').strip()
        payload = (ts + ':' + nonce + ':').encode('utf-8') + payload

    digest = hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()
    expected = f'sha256={digest}'
    return hmac.compare_digest(provided, expected)





def _validate_notification_receipt_payload(provider: str, data: dict):
    normalized = str(provider or '').strip().lower()
    if normalized not in {'email', 'sms', 'whatsapp'}:
        return False, 'provider must be one of email, sms, whatsapp'

    obj = data or {}
    status = str(obj.get('status') or '').strip().lower()
    allowed_status = {'queued', 'sent', 'delivered', 'failed', 'undelivered'}
    if status not in allowed_status:
        return False, f'status must be one of {sorted(list(allowed_status))}'

    delivery_id = str(obj.get('delivery_id') or '').strip()
    external_id = str(obj.get('external_id') or '').strip()
    if not delivery_id:
        return False, f'delivery_id is required for provider {normalized}'
    if not external_id:
        return False, f'external_id is required for provider {normalized}'

    if len(delivery_id) > 120:
        return False, 'delivery_id exceeds 120 chars'
    if len(external_id) > 160:
        return False, 'external_id exceeds 160 chars'

    callback_type = str(obj.get('callback_type') or 'delivery_status').strip().lower()
    if callback_type not in {'delivery_status', 'engagement'}:
        return False, 'callback_type must be delivery_status or engagement'

    if normalized == 'email':
        recipient = str(obj.get('recipient_email') or obj.get('recipient') or '').strip()
        if recipient and ('@' not in recipient or len(recipient) > 254):
            return False, 'invalid recipient_email for email provider'
        smtp_code = str(obj.get('smtp_code') or '').strip()
        if smtp_code and not smtp_code.isdigit():
            return False, 'smtp_code must be numeric when provided'

    if normalized == 'sms':
        phone = str(obj.get('to_phone') or obj.get('recipient') or '').strip()
        if phone and (not phone.startswith('+') or len(phone) < 8 or len(phone) > 20):
            return False, 'invalid to_phone format for sms provider'
        carrier = str(obj.get('carrier_status') or '').strip().lower()
        if carrier and carrier not in {'accepted', 'delivered', 'failed', 'unknown'}:
            return False, 'invalid carrier_status for sms provider'

    if normalized == 'whatsapp':
        wa_id = str(obj.get('wa_id') or obj.get('recipient') or '').strip()
        if wa_id and (not wa_id.isdigit() or len(wa_id) < 8 or len(wa_id) > 20):
            return False, 'invalid wa_id for whatsapp provider'
        category = str(obj.get('conversation_category') or '').strip().lower()
        if category and category not in {'service', 'utility', 'marketing', 'authentication'}:
            return False, 'invalid conversation_category for whatsapp provider'

    return True, ''
def _extract_client_ip():
    xff = (request.headers.get('X-Forwarded-For') or '').strip()
    if xff:
        return xff.split(',')[0].strip()
    return (request.remote_addr or '').strip()


def _verify_source_ip():
    allowed = (current_app.config.get('WEBHOOK_ALLOWED_IPS') or '').strip()
    if not allowed:
        return True

    client_ip = _extract_client_ip()
    if not client_ip:
        return False

    try:
        addr = ipaddress.ip_address(client_ip)
    except Exception:
        return False

    entries = [item.strip() for item in allowed.split(',') if item.strip()]
    if not entries:
        return True

    for entry in entries:
        try:
            if '/' in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                if addr == ipaddress.ip_address(entry):
                    return True
        except Exception:
            continue
    return False


def _verify_replay_protection():
    enforce = bool(current_app.config.get('WEBHOOK_REQUIRE_REPLAY_PROTECTION', True))
    if not enforce:
        return True

    ts_raw = (request.headers.get('X-Webhook-Timestamp') or '').strip()
    nonce = (request.headers.get('X-Webhook-Nonce') or '').strip()
    if not ts_raw or not nonce:
        return False

    try:
        ts_val = int(ts_raw)
    except Exception:
        return False

    now = int(time.time())
    window = int(current_app.config.get('WEBHOOK_REPLAY_WINDOW_SECONDS', 300))
    if abs(now - ts_val) > window:
        return False

    nonce_key = f'{nonce}:{ts_val}'
    store = (current_app.config.get('WEBHOOK_REPLAY_STORE') or 'db').strip().lower()

    if store == 'memory':
        cache = current_app.extensions.setdefault('webhook_nonce_cache', {})
        max_entries = int(current_app.config.get('WEBHOOK_NONCE_CACHE_MAX', 10000))

        expired_keys = [k for k, seen_at in cache.items() if (now - int(seen_at)) > window]
        for k in expired_keys:
            cache.pop(k, None)

        if nonce_key in cache:
            return False

        cache[nonce_key] = now

        if len(cache) > max_entries:
            oldest = sorted(cache.items(), key=lambda item: item[1])[: len(cache) - max_entries]
            for k, _ in oldest:
                cache.pop(k, None)

        return True

    db = get_db()
    db.execute('DELETE FROM webhook_nonces WHERE seen_at_epoch < ?', (now - window,))

    created_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    try:
        db.execute(
            'INSERT INTO webhook_nonces (nonce_key, seen_at_epoch, created_at) VALUES (?, ?, ?)',
            (nonce_key, now, created_at),
        )
        db.commit()
        return True
    except Exception:
        return False
def _ingest_text_request(channel, text, language, district, sender='anonymous', endpoint='', idempotency_key=None):
    raw_text = str(text or '').strip()
    language = (language or 'en').strip().lower() or 'en'
    district = (district or '').strip()
    idem_key = ((request.headers.get('X-Idempotency-Key') if idempotency_key is None else idempotency_key) or '').strip()

    if idem_key and endpoint:
        existing = _get_existing_idempotent_request(endpoint, idem_key)
        if existing:
            return {
                'success': True,
                'request_id': existing['request_id'],
                'channel': channel,
                'classification': {
                    'category': existing.get('category'),
                    'urgency': existing.get('urgency'),
                    'sentiment': existing.get('sentiment'),
                },
                'routing': {
                    'ward': existing.get('ward') or '',
                    'service': existing.get('service_type') or '',
                    'department': existing.get('routed_department') or '',
                },
                'sla': {
                    'due_at': existing.get('sla_due_at'),
                    'breached_at': existing.get('sla_breached_at'),
                    'escalation_level': int(existing.get('sla_escalation_level') or 0),
                },
                'idempotent_reused': True,
            }, None

    if not raw_text:
        return None, ('text is required', 400)

    clean_sender = str(sender or 'anonymous').strip() or 'anonymous'

    payload = {
        'channel': channel,
        'text': raw_text,
        'language': language,
        'district': district,
        'sender': clean_sender,
        'ward': (request.get_json(silent=True) or request.form.to_dict() or {}).get('ward', ''),
        'pipeline': {'ingested_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')},
    }

    if bool(current_app.config.get('ASYNC_PIPELINE_ENABLED', False)):
        idem_key = idem_key
        if idem_key:
            existing = find_job_by_idempotency_key(idem_key)
            if existing:
                return {'success': True, 'job_id': existing['job_id'], 'status': existing['status'], 'channel': channel, 'idempotent_reused': True}, None
        job_id = enqueue_ingestion_job(payload, idempotency_key=idem_key)
        write_audit_log(
            actor=sender,
            action='channel_request_enqueued',
            resource_type='processing_job',
            resource_id=job_id,
            details={'channel': channel, 'district': district, 'language': language},
        )
        return {
            'success': True,
            'job_id': job_id,
            'status': 'queued',
            'channel': channel,
        }, None

    result = process_ingestion_payload(payload)
    _save_idempotency_key(endpoint or f'/api/channels/{channel.lower()}/webhook', idem_key, result['request_id'])
    _append_lifecycle_event(
        result['request_id'],
        event_type='submitted',
        actor=sender,
        channel=channel,
        from_status=None,
        to_status='New',
        details={'language': language, 'district': result.get('district')},
    )
    write_audit_log(
        actor=sender,
        action='channel_request_ingested',
        resource_type='citizen_request',
        resource_id=result['request_id'],
        details={
            'channel': channel,
            'district': result.get('district'),
            'language': language,
            'model_trace': result.get('model_trace') or {},
        },
    )

    return {
        'success': True,
        'request_id': result['request_id'],
        'channel': channel,
        'classification': result['classification'],
        'model_trace': result.get('model_trace') or {},
        'routing': result.get('routing', {}),
        'sla': result.get('sla', {}),
    }, None


@channels_bp.route('/api/channels/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        verify_token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge', '')
        if mode == 'subscribe' and verify_token == current_app.config.get('WHATSAPP_VERIFY_TOKEN'):
            return challenge, 200
        return jsonify({'success': False, 'error': 'verification failed'}), 403

    if not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401
    if not _verify_source_ip():
        return jsonify({'success': False, 'error': 'source ip not allowed'}), 403
    if not _verify_replay_protection():
        return jsonify({'success': False, 'error': 'invalid or replayed webhook request'}), 401
    if not _verify_meta_signature():
        return jsonify({'success': False, 'error': 'invalid meta signature'}), 401

    data = request.get_json(silent=True) or {}
    text = data.get('text') or data.get('message')
    if not text and 'entry' in data:
        try:
            msg = data['entry'][0]['changes'][0]['value']['messages'][0]
            text = (msg.get('text') or {}).get('body')
        except Exception:
            pass
    payload, error = _ingest_text_request(
        channel='WhatsApp',
        text=text,
        language=data.get('language', 'en'),
        district=data.get('district'),
        sender=(data.get('from') or 'whatsapp-user'),
        endpoint='/api/channels/whatsapp/webhook',
    )
    if error:
        return jsonify({'success': False, 'error': error[0]}), error[1]
    return jsonify(payload)


@channels_bp.route('/api/channels/telegram/webhook', methods=['POST'])
def telegram_webhook():
    if not _verify_telegram_secret():
        return jsonify({'success': False, 'error': 'invalid telegram secret'}), 401
    has_custom_secret = bool((request.headers.get('X-Telegram-Bot-Api-Secret-Token') or '').strip())
    if not has_custom_secret and not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401
    data = request.get_json(silent=True) or {}
    from ..services.telegram_gateway import dispatch_outbox, handle_update

    try:
        payload = handle_update(data, _ingest_text_request)
        delivery = dispatch_outbox()
        payload['delivery'] = delivery
        return jsonify(payload)
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    except Exception:
        current_app.logger.exception('Telegram webhook processing failed')
        return jsonify({'success': False, 'error': 'Telegram update could not be processed'}), 500


@channels_bp.route('/api/channels/telegram/health', methods=['GET'])
def telegram_health():
    """Operational probe; secrets are represented only as booleans."""
    from ..services.telegram_gateway import health
    return jsonify(success=True, channel='Telegram', **health())


@channels_bp.route('/api/channels/<channel>/session/<session_key>', methods=['GET'])
def get_channel_session_endpoint(channel, session_key):
    from ..db import get_channel_session
    session = get_channel_session(channel, session_key)
    return jsonify({'success': True, 'session': session or {}})


@channels_bp.route('/api/channels/<channel>/session/<session_key>', methods=['POST', 'PUT'])
def save_channel_session_endpoint(channel, session_key):
    from ..db import save_channel_session
    data = request.get_json(silent=True) or {}
    save_channel_session(channel, session_key, data)
    return jsonify({'success': True})


@channels_bp.route('/api/channels/<channel>/session/<session_key>', methods=['DELETE'])
def delete_channel_session_endpoint(channel, session_key):
    from ..db import delete_channel_session
    delete_channel_session(channel, session_key)
    return jsonify({'success': True})





@channels_bp.route('/api/channels/sms/keyword', methods=['POST'])
def sms_keyword_workflow():
    if not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401
    if not _verify_source_ip():
        return jsonify({'success': False, 'error': 'source ip not allowed'}), 403
    if not _verify_replay_protection():
        return jsonify({'success': False, 'error': 'invalid or replayed webhook request'}), 401

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    text = str(data.get('text') or data.get('body') or '').strip()
    sender = str(scrub_text(data.get('from') or data.get('sender') or 'sms-user').get('scrubbed') or 'sms-user').strip() or 'sms-user'
    district = str(data.get('district') or '').strip()
    language = str(data.get('language') or 'en').strip() or 'en'

    cmd = parse_keyword_sms(text)

    if cmd.command == 'new':
        issue_text = str(cmd.argument or '').strip()
        if not issue_text:
            return jsonify({'success': False, 'error': 'NV NEW requires issue text', 'help': keyword_help_message()}), 400
        payload, error = _ingest_text_request(
            channel='SMS',
            text=issue_text,
            language=language,
            district=district,
            sender=sender,
            endpoint='/api/channels/sms/keyword',
        )
        if error:
            return jsonify({'success': False, 'error': error[0], 'help': keyword_help_message()}), error[1]
        payload['keyword_action'] = 'new'
        return jsonify(payload)

    if cmd.command == 'status':
        request_id = str(cmd.argument or '').strip()
        if not request_id:
            return jsonify({'success': False, 'error': 'NV STATUS requires request id', 'help': keyword_help_message()}), 400
        request_row, timeline = _fetch_request_timeline(request_id)
        if not request_row:
            return jsonify({'success': False, 'error': 'request not found'}), 404
        return jsonify({'success': True, 'keyword_action': 'status', 'request': request_row, 'timeline': timeline})

    if cmd.command == 'cosign':
        token = str(cmd.argument or '').strip()
        if not token:
            return jsonify({'success': False, 'error': 'NV COSIGN requires token', 'help': keyword_help_message()}), 400
        with current_app.test_request_context(
            f'/api/v1/requests/{token}/cosign',
            method='POST',
            json={'token': token, 'supporter_ref': sender, 'channel': 'sms-keyword'},
        ):
            pass
        db = get_db()
        row = db.execute(
            '''
            SELECT request_id FROM request_tokens
            WHERE token = ?
            ORDER BY issued_at DESC
            LIMIT 1
            ''',
            (token,),
        ).fetchone()
        if not row:
            req_row = db.execute('SELECT request_id FROM citizen_requests WHERE request_id = ? LIMIT 1', (token,)).fetchone()
            if req_row:
                request_id = str(req_row['request_id'])
                row2 = db.execute('SELECT token FROM request_tokens WHERE request_id = ? ORDER BY id DESC LIMIT 1', (request_id,)).fetchone()
                if row2:
                    token = str(row2['token'])
                    row = {'request_id': request_id}
                else:
                    issued_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                    token = f'CS-{secrets.token_hex(8)}'
                    db.execute('INSERT INTO request_tokens (request_id, token, token_status, issued_at, expires_at, used_at, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?)', (request_id, token, 'active', issued_at, None, None, json.dumps({'auto_generated': True, 'source': 'sms_keyword'})))
                    row = {'request_id': request_id}
            if not row:
                return jsonify({'success': False, 'error': 'invalid or expired cosign token'}), 404
        request_id = str(row['request_id'])
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        exists = db.execute(
            'SELECT id FROM request_cosigns WHERE request_id = ? AND supporter_ref = ? LIMIT 1',
            (request_id, sender),
        ).fetchone()
        created = False
        if not exists:
            db.execute(
                'INSERT INTO request_cosigns (request_id, token, supporter_ref, supporter_name, channel, verified, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (request_id, token, sender, sender, 'sms-keyword', 1, now),
            )
            created = True
        db.execute('UPDATE request_tokens SET used_at = ?, token_status = ? WHERE request_id = ? AND token = ?', (now, 'used', request_id, token))
        support_row = db.execute('SELECT COUNT(*) AS c FROM request_cosigns WHERE request_id = ? AND verified = 1', (request_id,)).fetchone()
        db.commit()
        return jsonify({'success': True, 'keyword_action': 'cosign', 'request_id': request_id, 'created': created, 'verified_support_count': int(support_row['c'] or 0)})

    return jsonify({'success': False, 'error': 'unsupported keyword command', 'help': keyword_help_message()}), 400


@channels_bp.route('/api/channels/ivr/missed-call', methods=['POST'])
def ivr_missed_call_webhook():
    if not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401
    if not _verify_source_ip():
        return jsonify({'success': False, 'error': 'source ip not allowed'}), 403
    if not _verify_replay_protection():
        return jsonify({'success': False, 'error': 'invalid or replayed webhook request'}), 401

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    phone = str(data.get('phone') or data.get('from') or data.get('caller') or '').strip()
    district = str(data.get('district') or '').strip()
    language = str(data.get('language') or 'en').strip() or 'en'

    try:
        result = orchestrate_missed_call_callback(phone=phone, district=district, language=language)
    except ValueError as err:
        return jsonify({'success': False, 'error': str(err)}), 400

    write_audit_log(
        actor=phone or 'ivr-caller',
        action='ivr_missed_call_scheduled',
        resource_type='processing_job',
        resource_id=result.get('callback_job_id'),
        details={'district': district, 'language': language},
    )
    return jsonify({'success': True, **result})
@channels_bp.route('/api/channels/sms/webhook', methods=['POST'])
def sms_webhook():
    if not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401
    if not _verify_source_ip():
        return jsonify({'success': False, 'error': 'source ip not allowed'}), 403
    if not _verify_replay_protection():
        return jsonify({'success': False, 'error': 'invalid or replayed webhook request'}), 401

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    if not _verify_twilio_signature(data):
        return jsonify({'success': False, 'error': 'invalid twilio signature'}), 401
    text = data.get('text') or data.get('body')
    payload, error = _ingest_text_request(
        channel='SMS',
        text=text,
        language=(data.get('language') or 'en'),
        district=data.get('district'),
        sender=(data.get('from') or data.get('sender') or 'sms-user'),
        endpoint='/api/channels/sms/webhook',
    )
    if error:
        return jsonify({'success': False, 'error': error[0]}), error[1]
    return jsonify(payload)


@channels_bp.route('/api/channels/email/webhook', methods=['POST'])
def email_webhook():
    if not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401
    if not _verify_source_ip():
        return jsonify({'success': False, 'error': 'source ip not allowed'}), 403
    if not _verify_replay_protection():
        return jsonify({'success': False, 'error': 'invalid or replayed webhook request'}), 401

    data = request.get_json(silent=True) or {}
    payload, error = _ingest_text_request(
        channel='Email',
        text=(data.get('body') or data.get('text') or data.get('subject')),
        language=(data.get('language') or 'en'),
        district=(data.get('district')),
        sender=(data.get('from') or data.get('sender') or 'email-user'),
        endpoint='/api/channels/email/webhook',
    )
    if error:
        return jsonify({'success': False, 'error': error[0]}), error[1]
    return jsonify(payload)


@channels_bp.route('/api/channels/ivr/webhook', methods=['POST'])
def ivr_webhook():
    if not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401
    if not _verify_source_ip():
        return jsonify({'success': False, 'error': 'source ip not allowed'}), 403
    if not _verify_replay_protection():
        return jsonify({'success': False, 'error': 'invalid or replayed webhook request'}), 401

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    if not _verify_twilio_signature(data):
        return jsonify({'success': False, 'error': 'invalid twilio signature'}), 401
    transcript = data.get('transcript') or data.get('text')
    payload, error = _ingest_text_request(
        channel='Voice IVR',
        text=transcript,
        language=(data.get('language') or 'en'),
        district=data.get('district'),
        sender=(data.get('caller') or data.get('from') or 'ivr-caller'),
        endpoint='/api/channels/ivr/webhook',
    )
    if error:
        return jsonify({'success': False, 'error': error[0]}), error[1]
    return jsonify(payload)


@channels_bp.route('/api/channels/ivr/callbacks/<callback_id>', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def ivr_callback_status(callback_id):
    item = get_callback_status(callback_id)
    if not item:
        return jsonify({'success': False, 'error': 'callback not found'}), 404
    return jsonify({'success': True, 'callback': item})


@channels_bp.route('/api/channels/ivr/callbacks/process', methods=['POST'])
@require_roles('admin', 'analyst')
def ivr_callback_process():
    data = request.get_json(silent=True) or {}
    limit = int(data.get('limit', 20))
    force_fail = bool(data.get('force_fail', False))
    result = process_due_callbacks(limit=limit, force_fail=force_fail)
    write_audit_log(
        actor=g.current_user['name'],
        action='ivr_callbacks_processed',
        resource_type='processing_job',
        details={'count': result.get('count', 0), 'force_fail': force_fail, 'role': g.current_user['role']},
    )
    return jsonify({'success': True, **result})

@channels_bp.route('/api/channels/ivr/callbacks/metrics', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def ivr_callback_metrics():
    payload = get_callback_metrics()
    write_audit_log(
        actor=g.current_user['name'],
        action='ivr_callback_metrics_viewed',
        resource_type='processing_job',
        details={'role': g.current_user['role'], 'total_callbacks': payload.get('total_callbacks', 0)},
    )
    return jsonify({'success': True, 'metrics': payload})

@channels_bp.route('/api/channels/ivr/callbacks/webhook', methods=['POST'])
def ivr_callback_webhook():
    if not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401
    if not _verify_source_ip():
        return jsonify({'success': False, 'error': 'source ip not allowed'}), 403
    if not _verify_replay_protection():
        return jsonify({'success': False, 'error': 'invalid or replayed webhook request'}), 401

    raw_payload = request.get_data(cache=True) or b''
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    provider = str(data.get('provider') or 'twilio').strip().lower()
    if provider == 'twilio' and not _verify_twilio_signature(data):
        return jsonify({'success': False, 'error': 'invalid twilio signature'}), 401
    if provider == 'exotel' and not _verify_exotel_signature(raw_payload):
        return jsonify({'success': False, 'error': 'invalid exotel signature'}), 401

    mapped = parse_provider_callback_payload(provider=provider, data=data)
    if not mapped.get('success'):
        return jsonify(mapped), 400

    provider = str(mapped.get('provider') or provider)
    callback_id = str(mapped.get('callback_id') or '')
    status = str(mapped.get('status') or '')
    external_event_id = str(mapped.get('external_event_id') or '')

    result = reconcile_provider_callback(
        provider=provider,
        callback_id=callback_id,
        status=status,
        external_event_id=external_event_id,
        raw_payload=data,
    )
    if not result.get('success'):
        code = 404 if result.get('error') == 'callback not found' else 400
        return jsonify(result), code

    write_audit_log(
        actor='ivr-callback-webhook',
        action='ivr_callback_webhook_reconciled',
        resource_type='processing_job',
        resource_id=callback_id,
        details={'provider': provider, 'status': result.get('status'), 'idempotent_reused': bool(result.get('idempotent_reused', False))},
    )
    return jsonify(result)


@channels_bp.route('/api/channels/ivr/callbacks/alerts', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def ivr_callback_alerts():
    notify = str(request.args.get('notify', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}
    payload = get_callback_alerts()
    sent = []
    skipped = []
    if notify and (payload.get('alerts') or []):
        emitted = emit_callback_alerts(actor=g.current_user['name'])
        sent = emitted.get('sent', [])
        skipped = emitted.get('skipped', [])

    alert_state = {
        'active_alerts': len(payload.get('alerts') or []),
        'notifications_sent': len(sent),
        'suppressed': len(skipped),
        'dedupe_seconds': int(current_app.config.get('IVR_CALLBACK_ALERT_DEDUPE_SECONDS', 1800) or 1800),
        'cooldown_seconds': int(current_app.config.get('IVR_CALLBACK_ALERT_COOLDOWN_SECONDS', 900) or 900),
    }

    write_audit_log(
        actor=g.current_user['name'],
        action='ivr_callback_alerts_viewed',
        resource_type='processing_job',
        details={'role': g.current_user['role'], 'alerts': alert_state['active_alerts'], 'notify': notify, 'notifications': alert_state['notifications_sent'], 'suppressed': alert_state['suppressed']},
    )
    return jsonify({'success': True, 'metrics': payload.get('metrics', {}), 'alerts': payload.get('alerts', []), 'notifications': sent, 'skipped': skipped, 'alert_state': alert_state, 'notify': notify})

@channels_bp.route('/api/channels/ivr/callbacks/alerts/history', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def ivr_callback_alert_history():
    try:
        limit = min(max(int(request.args.get('limit', 100) or 100), 1), 5000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    alert_type = str(request.args.get('alert_type') or '').strip() or None
    severity = str(request.args.get('severity') or '').strip() or None
    date_from = str(request.args.get('date_from') or '').strip() or None
    date_to = str(request.args.get('date_to') or '').strip() or None
    delivery_status = str(request.args.get('delivery_status') or '').strip() or None

    result = list_callback_alert_history(
        limit=limit,
        alert_type=alert_type,
        severity=severity,
        date_from=date_from,
        date_to=date_to,
        delivery_status=delivery_status,
    )
    items = result.get('items') or []

    write_audit_log(
        actor=g.current_user['name'],
        action='ivr_callback_alert_history_viewed',
        resource_type='processing_job',
        details={'role': g.current_user['role'], 'limit': limit, 'alert_type': alert_type, 'severity': severity, 'date_from': date_from, 'date_to': date_to, 'delivery_status': delivery_status, 'count': len(items)},
    )

    return jsonify({'success': True, 'items': items, 'meta': {'limit': limit, 'count': len(items)}})


@channels_bp.route('/api/channels/ivr/callbacks/alerts/history/export', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def export_ivr_callback_alert_history():
    try:
        limit = min(max(int(request.args.get('limit', 500) or 500), 1), 5000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    alert_type = str(request.args.get('alert_type') or '').strip() or None
    severity = str(request.args.get('severity') or '').strip() or None
    date_from = str(request.args.get('date_from') or '').strip() or None
    date_to = str(request.args.get('date_to') or '').strip() or None
    delivery_status = str(request.args.get('delivery_status') or '').strip() or None
    export_format = str(request.args.get('format') or 'json').strip().lower()

    result = list_callback_alert_history(
        limit=limit,
        alert_type=alert_type,
        severity=severity,
        date_from=date_from,
        date_to=date_to,
        delivery_status=delivery_status,
    )
    items = result.get('items') or []

    enriched_items = []
    for item in items:
        observed = item.get('observed') if isinstance(item.get('observed'), dict) else {}
        alert_obj = observed.get('alert') if isinstance(observed.get('alert'), dict) else {}
        metrics_obj = observed.get('metrics') if isinstance(observed.get('metrics'), dict) else {}

        enriched = dict(item)
        enriched['observed_value'] = alert_obj.get('observed')
        enriched['threshold_value'] = alert_obj.get('threshold')
        enriched['failed_callbacks'] = metrics_obj.get('failed')
        enriched['dead_letter_count'] = metrics_obj.get('dead_letter_count')
        enriched['connected_callbacks'] = metrics_obj.get('connected')
        enriched['retry_pending'] = metrics_obj.get('retry_pending')
        enriched['total_callbacks'] = metrics_obj.get('total_callbacks')
        enriched_items.append(enriched)

    write_audit_log(
        actor=g.current_user['name'],
        action='ivr_callback_alert_history_exported',
        resource_type='processing_job',
        details={'role': g.current_user['role'], 'limit': limit, 'alert_type': alert_type, 'severity': severity, 'date_from': date_from, 'date_to': date_to, 'delivery_status': delivery_status, 'format': export_format, 'count': len(enriched_items)},
    )

    if export_format == 'csv':
        import csv
        import io

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                'id',
                'alert_key',
                'alert_type',
                'severity',
                'notification_delivery_id',
                'delivery_status',
                'observed_value',
                'threshold_value',
                'failed_callbacks',
                'dead_letter_count',
                'connected_callbacks',
                'retry_pending',
                'total_callbacks',
                'created_at',
            ],
        )
        writer.writeheader()
        for item in enriched_items:
            writer.writerow(
                {
                    'id': item.get('id'),
                    'alert_key': item.get('alert_key'),
                    'alert_type': item.get('alert_type'),
                    'severity': item.get('severity'),
                    'notification_delivery_id': item.get('notification_delivery_id'),
                    'delivery_status': item.get('delivery_status'),
                    'observed_value': item.get('observed_value'),
                    'threshold_value': item.get('threshold_value'),
                    'failed_callbacks': item.get('failed_callbacks'),
                    'dead_letter_count': item.get('dead_letter_count'),
                    'connected_callbacks': item.get('connected_callbacks'),
                    'retry_pending': item.get('retry_pending'),
                    'total_callbacks': item.get('total_callbacks'),
                    'created_at': item.get('created_at'),
                }
            )

        return current_app.response_class(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=ivr_callback_alert_history.csv'},
        )

    return jsonify({'success': True, 'items': enriched_items, 'meta': {'limit': limit, 'count': len(enriched_items), 'format': 'json'}})


@channels_bp.route('/api/complaints', methods=['GET'])
def list_complaints_public():
    district = (request.args.get('district') or '').strip()
    state = (request.args.get('state') or '').strip()
    category = (request.args.get('category') or '').strip()
    urgency = (request.args.get('urgency') or '').strip()
    pilot_id = (request.args.get('pilot_id') or '').strip()
    limit = min(int(request.args.get('limit', 200)), 500)

    if not pilot_id:
        bq_rows = _bq_complaints(limit=limit, district=district, state=state, category=category, urgency=urgency)
        if bq_rows is not None:
            return jsonify({'success': True, 'count': len(bq_rows), 'complaints': bq_rows, 'source': 'bigquery'})

    db = get_db()
    sql = '''
        SELECT request_id, district, state, input_language, original_text, translated_text,
               category, urgency, sentiment, status, source_channel, ward, routed_department, created_at
        FROM citizen_requests
        WHERE 1=1
    '''
    params = []
    if pilot_id:
        sql += " AND EXISTS (SELECT 1 FROM pilot_requests pr WHERE pr.request_id = citizen_requests.request_id AND pr.pilot_id = ?)"
        params.append(pilot_id)
    if district:
        sql += " AND district = ?"
        params.append(district)
    if state:
        sql += " AND state = ?"
        params.append(state)
    if category:
        sql += " AND category = ?"
        params.append(category)
    if urgency:
        sql += " AND urgency = ?"
        params.append(urgency)

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = db.execute(sql, params).fetchall()
    complaints = []
    for row in rows:
        item = dict(row)
        item['source'] = item.get('source_channel')
        item['language'] = item.get('input_language')
        if not item.get('ward'):
            item['ward'] = f"Ward {((hash(str(item.get('request_id', ''))) % 25) + 1):02d}"
        complaints.append(item)

    complaints.sort(key=lambda x: str(x.get('created_at', '') or x.get('request_id', '')), reverse=True)
    complaints = complaints[:limit]
    return jsonify({'success': True, 'count': len(complaints), 'complaints': complaints, 'source': 'db'})


@channels_bp.route('/api/stats', methods=['GET'])
def stats():
    from ..services.analyst_workbench import stats as scoped_stats, scope_from
    data=scoped_stats(scope_from(request.args))
    return jsonify(success=True,stats=data,categories=data['categories'],daily_trend=data['daily_trend'],source='operational_database')


@channels_bp.route('/api/districts', methods=['GET'])
def districts():
    pilot_param = request.args.get('pilot_id', '').strip()
    if current_app.config.get('PILOT_ONLY') or pilot_param:
        from ..services.pilot import scope,rows
        s=scope(request.args)
        pid = s.get('pilot_id') or pilot_param
        locs=rows('SELECT DISTINCT state,district FROM pilot_locations WHERE pilot_id=?',(pid,))
        return jsonify(success=True,districts=sorted({r['district'] for r in locs if all(not s.get(k) or s[k]==r[k] for k in ('state','district'))}))
    state = (request.args.get('state') or '').strip()
    allowed_map = current_app.config.get('ALLOWED_STATE_TO_DISTRICTS') or {}
    
    if state and state in allowed_map:
        cfg_dists = allowed_map[state]
    elif not state:
        all_dists = set()
        for dists_list in allowed_map.values():
            all_dists.update(dists_list)
        cfg_dists = list(all_dists)
    else:
        cfg_dists = []
        
    try:
        repo_dists = current_app.extensions['reference_repo'].list_districts(state=state)
    except Exception:
        repo_dists = []
        
    combined = sorted(list(set(cfg_dists) | set(repo_dists)))
    return jsonify({'success': True, 'districts': combined})


@channels_bp.route('/api/states', methods=['GET'])
def states():
    pilot_param = request.args.get('pilot_id', '').strip()
    if current_app.config.get('PILOT_ONLY') or pilot_param:
        from ..services.pilot import scope,rows
        s=scope(request.args)
        pid = s.get('pilot_id') or pilot_param
        locs=rows('SELECT DISTINCT state,district FROM pilot_locations WHERE pilot_id=?',(pid,))
        return jsonify(success=True,states=sorted({r['state'] for r in locs if all(not s.get(k) or s[k]==r[k] for k in ('state','district'))}))
    allowed_map = current_app.config.get('ALLOWED_STATE_TO_DISTRICTS') or {}
    cfg_states = list(allowed_map.keys())
    try:
        repo_states = current_app.extensions['reference_repo'].list_states()
    except Exception:
        repo_states = []
        
    combined = sorted(list(set(cfg_states) | set(repo_states)))
    return jsonify({'success': True, 'states': combined})


@channels_bp.route('/api/translate', methods=['POST'])
def translate_text():
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    source = data.get('source_lang', 'en')
    target = data.get('target_lang', 'en')
    return jsonify({'success': True, 'result': _run_translation(text, source, target), 'ai_mode': _get_ai_mode()})



@channels_bp.route('/api/v1/notifications/slo-dashboard', methods=['GET'])
@require_roles('admin', 'auditor')
def notification_slo_dashboard_secure():
    limit = int(request.args.get('limit', 200) or 200)
    notify = str(request.args.get('notify') or '').strip().lower() in {'1', 'true', 'yes'}
    payload = get_notification_slo_dashboard(limit=limit)
    notifications = []
    if notify and (payload.get('alerts') or []):
        emitted = emit_notification_slo_alerts(payload, actor=g.current_user['name'])
        notifications = emitted.get('items') or []
    write_audit_log(
        actor=g.current_user['name'],
        action='notification_slo_dashboard_viewed',
        resource_type='notification_delivery',
        details={'limit': limit, 'alerts': len(payload.get('alerts') or []), 'notify': notify, 'notifications': len(notifications)},
    )
    return jsonify({'success': True, 'dashboard': payload, 'notify': notify, 'notifications': notifications})



@channels_bp.route('/api/v1/notifications/ops-overlay', methods=['GET'])
@require_roles('admin', 'auditor')
def notification_ops_overlay_secure():
    limit = int(request.args.get('limit', 100) or 100)
    payload = get_notification_ops_overlay(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='notification_ops_overlay_viewed',
        resource_type='notification_delivery',
        details={
            'limit': limit,
            'deliveries': len(payload.get('recent_deliveries') or []),
            'receipt_events': len(payload.get('recent_receipt_events') or []),
            'alerts': len((payload.get('slo') or {}).get('alerts') or []),
        },
    )
    return jsonify({'success': True, 'overlay': payload})

@channels_bp.route('/api/v1/notifications/receipts', methods=['POST'])
@require_roles('admin')
def notification_receipt_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    delivery_id = str(data.get('delivery_id') or '').strip()
    provider = str(data.get('provider') or '').strip()
    status = str(data.get('status') or '').strip()
    external_id = str(data.get('external_id') or '').strip()

    if not delivery_id or not status:
        return jsonify({'success': False, 'error': 'delivery_id and status are required'}), 400

    result = reconcile_notification_receipt(delivery_id, provider, status, external_id=external_id, raw_payload=data)
    if result is None:
        return jsonify({'success': False, 'error': 'delivery not found'}), 404

    write_audit_log(
        actor=g.current_user['name'],
        action='notification_receipt_reconciled',
        resource_type='notification_delivery',
        resource_id=delivery_id,
        details={'status': result['status'], 'provider': result['provider']},
    )
    return jsonify({'success': True, 'receipt': result})


@channels_bp.route('/api/notifications/receipts/webhook', methods=['POST'])
def notification_receipt_webhook():
    token = (current_app.config.get('NOTIFICATION_RECEIPT_TOKEN') or '').strip()
    provided = (request.headers.get('X-Notification-Token') or '').strip()
    if token and provided != token:
        return jsonify({'success': False, 'error': 'invalid notification receipt token'}), 401

    raw_payload = request.get_data() or b''
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    provider = str((request.headers.get('X-Notification-Provider') or data.get('provider') or '')).strip().lower()

    ok, err = _validate_notification_receipt_payload(provider, data)
    if not ok:
        return jsonify({'success': False, 'error': err}), 400

    if not _verify_notification_receipt_signature(provider, raw_payload):
        return jsonify({'success': False, 'error': 'invalid notification receipt signature'}), 401

    delivery_id = str(data.get('delivery_id') or '').strip()
    status = str(data.get('status') or '').strip()
    external_id = str(data.get('external_id') or '').strip()

    evt = register_notification_receipt_event(delivery_id, provider, status, external_id=external_id, raw_payload=data)
    if not evt.get('accepted'):
        return jsonify({'success': True, 'duplicate': True, 'event_key': evt.get('event_key')}), 200

    result = reconcile_notification_receipt(delivery_id, provider, status, external_id=external_id, raw_payload=data)
    if result is None:
        return jsonify({'success': False, 'error': 'delivery not found'}), 404

    write_audit_log(
        actor='notification-webhook',
        action='notification_receipt_webhook_reconciled',
        resource_type='notification_delivery',
        resource_id=delivery_id,
        details={'status': result['status'], 'provider': result['provider']},
    )
    return jsonify({'success': True, 'receipt': result})
