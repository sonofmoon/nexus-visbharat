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

ops_bp = Blueprint('ops_bp', __name__)


@ops_bp.route('/api/v1/audit-logs', methods=['GET'])
@ops_bp.route('/api/v1/audit/logs', methods=['GET'])
@require_roles('admin', 'auditor')
def list_audit_logs_secure():
    from ..services import auditor_workbench as audit
    result=audit.event_page(audit.scope_from(request.args),request.args)
    records=result.pop('items')
    # Preserve one established legacy field without repeating every record three times.
    return jsonify(success=True,audit_logs=records,**result)


@ops_bp.route('/api/v1/audit/export', methods=['GET'])
@require_roles('admin', 'auditor')
def export_audit_logs_secure():
    from .auditor import event_export_payload, csv_cell
    from ..services import auditor_workbench as audit
    payload=event_export_payload(audit.scope_from(request.args),request.args)
    output=io.StringIO();writer=csv.writer(output)
    keys=['id','created_at','actor','action','resource_type','resource_id','event_version','chain_seq']
    writer.writerow(keys)
    for row in payload['records']: writer.writerow([csv_cell(row.get(k)) for k in keys])
    return current_app.response_class(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=nvb_audit_trail.csv','X-NVB-Manifest-SHA256':audit.digest(payload)})


@ops_bp.route('/api/v1/security/alerts', methods=['GET'])
@require_roles('admin', 'auditor')
def get_security_alerts_secure():
    from ..services import auditor_workbench as audit
    result=audit.security_feed(audit.scope_from(request.args),audit.page_limit(request.args.get('limit')))
    return jsonify(success=True,**result)


@ops_bp.route('/api/v1/users', methods=['GET'])
@ops_bp.route('/api/v1/admin/users', methods=['GET'])
@require_roles('admin')
def list_users():
    db = get_db()
    rows = db.execute('SELECT id, name, role, created_at FROM users ORDER BY id ASC').fetchall()
    return jsonify({'success': True, 'users': [dict(r) for r in rows]})


@ops_bp.route('/api/v1/users', methods=['POST'])
@require_roles('admin')
def create_user():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    role = (data.get('role') or '').strip()
    api_token = (data.get('api_token') or '').strip() or generate_api_token()

    if not name or not role:
        return jsonify({'success': False, 'error': 'name and role are required'}), 400

    if role not in current_app.config['ROLE_CHOICES']:
        return jsonify({'success': False, 'error': f"role must be one of {current_app.config['ROLE_CHOICES']}"}), 400

    db = get_db()
    try:
        db.execute(
            '''
            INSERT INTO users (name, api_token, api_token_hash, token_last4, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (name, api_token, hash_api_token(api_token), token_last4(api_token), role, datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))
        )
        db.commit()
    except Exception:
        return jsonify({'success': False, 'error': 'api_token already exists or invalid payload'}), 400

    write_audit_log(
        actor=g.current_user['name'],
        action='user_created',
        resource_type='user',
        details={'name': name, 'role': role, 'token_last4': token_last4(api_token)},
    )

    return jsonify({'success': True, 'message': 'User created', 'api_token': api_token})







@ops_bp.route('/api/v1/users/<int:user_id>/rotate-token', methods=['POST'])
@require_roles('admin')
def rotate_user_token(user_id):
    db = get_db()
    row = db.execute('SELECT id, name, role FROM users WHERE id = ?', (user_id,)).fetchone()
    if row is None:
        return jsonify({'success': False, 'error': 'user not found'}), 404

    new_token = generate_api_token()

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    db.execute(
        '''
        UPDATE users
        SET api_token = ?, api_token_hash = ?, token_last4 = ?, token_rotated_at = ?
        WHERE id = ?
        ''',
        (new_token, hash_api_token(new_token), token_last4(new_token), now, user_id)
    )
    db.commit()


    write_audit_log(
        actor=g.current_user['name'],
        action='rotate-user-token',
        resource_type='user',
        resource_id=str(user_id),
        details={'target_user': row['name'], 'target_role': row['role'], 'token_last4': token_last4(new_token)},
    )

    return jsonify(
        {
            'success': True,
            'message': 'Token rotated. Store this token now; it will not be shown again.',
            'user_id': user_id,
            'api_token': new_token,
            'token_last4': token_last4(new_token),
            'rotated_at': now,
        }
    )











@ops_bp.route('/api/v1/pipeline/jobs', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def list_pipeline_jobs_secure():
    limit = min(int(request.args.get('limit', 100)), 500)
    rows = list_pipeline_jobs(limit=limit)
    return jsonify({'success': True, 'jobs': rows})


@ops_bp.route('/api/v1/pipeline/jobs/<job_id>', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def get_pipeline_job_secure(job_id):
    row = get_pipeline_job(job_id)
    if row is None:
        return jsonify({'success': False, 'error': 'job not found'}), 404
    return jsonify({'success': True, 'job': row})


@ops_bp.route('/api/v1/pipeline/workers', methods=['GET'])
@require_roles('admin', 'auditor')
def list_pipeline_workers_secure():
    limit = min(int(request.args.get('limit', 100)), 500)
    rows = list_worker_heartbeats(limit=limit)
    return jsonify({'success': True, 'workers': rows})


@ops_bp.route('/api/v1/pipeline/jobs/<job_id>/retry', methods=['POST'])
@require_roles('admin')
def retry_pipeline_job_secure(job_id):
    result, err = retry_pipeline_job(job_id)
    if err:
        code = 404 if err == 'job not found' else 400
        return jsonify({'success': False, 'error': err}), code
    return jsonify({'success': True, 'job': result})


@ops_bp.route('/api/v1/pipeline/jobs/<job_id>/cancel', methods=['POST'])
@require_roles('admin')
def cancel_pipeline_job_secure(job_id):
    result, err = cancel_pipeline_job(job_id)
    if err:
        code = 404 if err == 'job not found' else 400
        return jsonify({'success': False, 'error': err}), code
    return jsonify({'success': True, 'job': result})


@ops_bp.route('/api/v1/pipeline/metrics', methods=['GET'])
@require_roles('admin', 'auditor')
def get_pipeline_metrics_secure():
    return jsonify({'success': True, 'metrics': get_pipeline_metrics()})




def _safe_cloud_ingestion_snapshot():
    db = get_db()
    pending_dead_letters = 0
    recent_dead_letters = []
    try:
        dead_letter_count_row = db.execute("SELECT count(*) as count FROM cloud_ingestion_dead_letters WHERE status = 'pending'").fetchone()
        pending_dead_letters = int(((dict(dead_letter_count_row) if dead_letter_count_row else {}).get('count')) or 0)
        rows = db.execute(
            "SELECT request_id, district, state, source_channel, error_message, status, created_at FROM cloud_ingestion_dead_letters ORDER BY id DESC LIMIT 10"
        ).fetchall()
        recent_dead_letters = [dict(r) for r in rows]
    except Exception:
        pending_dead_letters = 0
        recent_dead_letters = []
    return {'pending_dead_letters': pending_dead_letters, 'recent_dead_letters': recent_dead_letters}


def _safe_satellite_signal_snapshot():
    _ensure_satellite_signal_table()
    db = get_db()
    total_row = db.execute('SELECT COUNT(*) AS c FROM anti_capture_satellite_signals').fetchone()
    latest = db.execute(
        '''
        SELECT signal_id, project_id, signal_type, signal_value, signal_unit, observed_at,
               source_provider, source_uri, source_checksum_sha256, ingested_by, ingested_at
        FROM anti_capture_satellite_signals
        ORDER BY observed_at DESC, ingested_at DESC
        LIMIT 1
        '''
    ).fetchone()
    return {
        'total_signals': int(((dict(total_row) if total_row else {}).get('c')) or 0),
        'latest_signal': dict(latest) if latest else None,
    }


def _evaluate_ops_alerts_from_snapshot(snapshot: dict):
    thresholds = {
        'pipeline_dead_letters_warning': int(current_app.config.get('OPS_ALERT_PIPELINE_DEAD_LETTERS_WARNING', 1) or 1),
        'pipeline_dead_letters_critical': int(current_app.config.get('OPS_ALERT_PIPELINE_DEAD_LETTERS_CRITICAL', 5) or 5),
        'asr_failure_rate_warning_pct': float(current_app.config.get('OPS_ALERT_ASR_FAILURE_RATE_WARNING_PCT', 5.0) or 5.0),
        'asr_failure_rate_critical_pct': float(current_app.config.get('OPS_ALERT_ASR_FAILURE_RATE_CRITICAL_PCT', 15.0) or 15.0),
        'notification_failed_warning': int(current_app.config.get('OPS_ALERT_NOTIFICATION_FAILED_WARNING', 10) or 10),
        'notification_failed_critical': int(current_app.config.get('OPS_ALERT_NOTIFICATION_FAILED_CRITICAL', 30) or 30),
    }

    alerts = []
    observed = {}

    pipeline_pending = int((((snapshot or {}).get('components') or {}).get('cloud_ingestion') or {}).get('pending_dead_letters') or 0)
    observed['pipeline_pending_dead_letters'] = pipeline_pending
    if pipeline_pending >= thresholds['pipeline_dead_letters_critical']:
        alerts.append({'type': 'pipeline_dead_letters', 'severity': 'critical', 'threshold': thresholds['pipeline_dead_letters_critical'], 'observed': pipeline_pending})
    elif pipeline_pending >= thresholds['pipeline_dead_letters_warning']:
        alerts.append({'type': 'pipeline_dead_letters', 'severity': 'warning', 'threshold': thresholds['pipeline_dead_letters_warning'], 'observed': pipeline_pending})

    asr_summary = (((snapshot or {}).get('components') or {}).get('asr') or {})
    total_events = int(asr_summary.get('total_events') or 0)
    failed_events = 0
    for p in asr_summary.get('providers') or []:
        failed_events += int(p.get('failed') or 0)
    asr_fail_pct = (float(failed_events) / float(total_events) * 100.0) if total_events > 0 else 0.0
    observed['asr_failure_rate_pct'] = round(asr_fail_pct, 3)
    if asr_fail_pct >= thresholds['asr_failure_rate_critical_pct']:
        alerts.append({'type': 'asr_failure_rate', 'severity': 'critical', 'threshold': thresholds['asr_failure_rate_critical_pct'], 'observed': round(asr_fail_pct, 3)})
    elif asr_fail_pct >= thresholds['asr_failure_rate_warning_pct']:
        alerts.append({'type': 'asr_failure_rate', 'severity': 'warning', 'threshold': thresholds['asr_failure_rate_warning_pct'], 'observed': round(asr_fail_pct, 3)})

    notif_failed = int(((((snapshot or {}).get('components') or {}).get('notifications') or {}).get('metrics') or {}).get('failed', 0) or 0)
    observed['notification_failed'] = notif_failed
    if notif_failed >= thresholds['notification_failed_critical']:
        alerts.append({'type': 'notification_failed', 'severity': 'critical', 'threshold': thresholds['notification_failed_critical'], 'observed': notif_failed})
    elif notif_failed >= thresholds['notification_failed_warning']:
        alerts.append({'type': 'notification_failed', 'severity': 'warning', 'threshold': thresholds['notification_failed_warning'], 'observed': notif_failed})

    return {'alerts': alerts, 'thresholds': thresholds, 'observed': observed}


def _ensure_ops_alert_routing_table():
    db = get_db()
    db.execute("CREATE TABLE IF NOT EXISTS ops_alert_routing_events (id INTEGER PRIMARY KEY AUTOINCREMENT, route_key TEXT UNIQUE NOT NULL, alert_type TEXT NOT NULL, target_kind TEXT NOT NULL, target_ref TEXT, severity TEXT NOT NULL, severity_rank INTEGER NOT NULL, repeat_count INTEGER NOT NULL DEFAULT 1, escalated INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, channel TEXT NOT NULL, response_code INTEGER, response_body TEXT, payload_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    db.commit()


def _severity_rank(level: str) -> int:
    value = str(level or '').strip().lower()
    ranks = {'info': 1, 'warning': 2, 'critical': 3}
    return int(ranks.get(value, 1))


def _severity_label(rank: int) -> str:
    if rank >= 3:
        return 'critical'
    if rank == 2:
        return 'warning'
    return 'info'


def _bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _post_json_with_status(url: str, payload: dict, headers: dict | None = None, timeout: int = 8):
    data = json.dumps(payload or {}).encode('utf-8')
    req = urlrequest.Request(url=url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    for key, value in (headers or {}).items():
        req.add_header(str(key), str(value))
    with urlrequest.urlopen(req, timeout=timeout) as resp:  # nosec B310
        body = (resp.read() or b'').decode('utf-8', errors='ignore')
        return int(getattr(resp, 'status', 200) or 200), body[:1000]


def _build_ops_snapshot(limit: int = 100):
    safe_limit = min(max(int(limit or 100), 1), 500)
    snapshot = {
        'success': True,
        'as_of': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'components': {
            'pipeline': get_pipeline_metrics(),
            'asr': _get_asr_metrics_summary(limit=500),
            'maps': {
                'use_real_google_maps': bool(current_app.config.get('USE_REAL_GOOGLE_MAPS', False)),
                'api_key_configured': bool(str(current_app.config.get('GOOGLE_MAPS_API_KEY') or '').strip()),
                'client_initialized': bool(current_app.extensions.get('google_maps_client')),
            },
            'notifications': get_notification_ops_overlay(limit=min(safe_limit, 200)),
            'cloud_ingestion': _safe_cloud_ingestion_snapshot(),
            'satellite_signals': _safe_satellite_signal_snapshot(),
        },
    }
    snapshot['alerts'] = _evaluate_ops_alerts_from_snapshot(snapshot)
    return snapshot


def _ops_verify_timestamp_and_nonce(ts_raw: str, nonce: str, nonce_scope: str = 'ops-webhook-verify'):
    ts_clean = str(ts_raw or '').strip()
    nonce_clean = str(nonce or '').strip()
    if not ts_clean or not nonce_clean:
        return False, 'missing timestamp or nonce'

    try:
        ts_val = int(ts_clean)
    except Exception:
        return False, 'invalid timestamp format'

    window = int(current_app.config.get('OPS_WEBHOOK_VERIFY_WINDOW_SECONDS', current_app.config.get('WEBHOOK_REPLAY_WINDOW_SECONDS', 300)) or 300)
    now = int(time.time())
    if abs(now - ts_val) > window:
        return False, 'timestamp outside replay window'

    db = get_db()
    nonce_key = f'{nonce_scope}:{nonce_clean}:{ts_val}'
    db.execute('DELETE FROM webhook_nonces WHERE seen_at_epoch < ?', (now - window,))
    try:
        db.execute(
            'INSERT INTO webhook_nonces (nonce_key, seen_at_epoch, created_at) VALUES (?, ?, ?)',
            (nonce_key, now, datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')),
        )
        db.commit()
    except Exception:
        return False, 'replayed nonce'

    return True, ''


def _ops_verify_request_signature(raw_body: bytes, secret: str, ts_raw: str, nonce: str, provided_signature: str):
    if not secret:
        return False, 'missing verification secret'

    signature = str(provided_signature or '').strip()
    if not signature.startswith('sha256='):
        return False, 'invalid signature format'

    canonical = f"{str(ts_raw).strip()}:{str(nonce).strip()}:".encode('utf-8') + (raw_body or b'')
    expected_digest = hmac.new(secret.encode('utf-8'), canonical, hashlib.sha256).hexdigest()
    expected = f'sha256={expected_digest}'
    if not hmac.compare_digest(signature, expected):
        return False, 'signature mismatch'
    return True, ''


def _build_cloud_monitoring_incident_payload(payload: dict, alert_type: str, effective_rank: int):
    severity = _severity_label(effective_rank)
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    source = str(current_app.config.get('OPS_ALERT_GCP_SOURCE') or 'nexus-visbharath').strip() or 'nexus-visbharath'
    alert_obj = (payload or {}).get('alert') or {}
    observed = (payload or {}).get('observed') or {}

    return {
        'schema': 'google-cloud-monitoring-like/v1',
        'incident': {
            'incident_id': f"INC-{alert_type}-{int(time.time())}",
            'state': 'open',
            'started_at': now_iso,
            'severity': severity,
            'policy_name': f"NVB Ops {alert_type}",
            'summary': f"NVB Ops Alert: {alert_type} ({severity})",
            'resource': {
                'type': 'global',
                'labels': {
                    'project_id': str(current_app.config.get('GOOGLE_CLOUD_PROJECT') or '').strip(),
                    'service': source,
                },
            },
            'metric': {
                'type': f"custom.googleapis.com/nvb/ops/{alert_type}",
                'labels': {'source': source},
            },
            'documentation': {
                'content': 'Auto-generated by NVB ops alert router.',
                'mime_type': 'text/markdown',
            },
            'observed_value': alert_obj.get('observed'),
            'threshold_value': alert_obj.get('threshold'),
            'metadata': {
                'actor': (payload or {}).get('actor') or 'system',
                'as_of': (payload or {}).get('as_of') or now_iso,
                'alert': alert_obj,
                'observed': observed,
            },
        },
    }


def _publish_ops_alert_to_pubsub(alert_payload: dict):
    pubsub_client = current_app.extensions.get('google_ops_pubsub_client') or current_app.extensions.get('google_pubsub_client')
    if not pubsub_client:
        raise RuntimeError('google_pubsub_client unavailable')

    body = json.dumps(alert_payload, default=str).encode('utf-8')
    attrs = {'kind': 'ops_alert', 'source': 'nexus-visbharath'}
    future = pubsub_client.publisher.publish(pubsub_client.topic_path, body, **attrs)
    message_id = future.result(timeout=10)
    return str(message_id)


def _route_ops_alerts(evaluated: dict, actor: str = 'system', dry_run: bool = False):
    if not bool(current_app.config.get('OPS_ALERT_ROUTING_ENABLED', True)):
        return {'enabled': False, 'dry_run': bool(dry_run), 'routed': [], 'skipped': [], 'email': {'sent': 0, 'items': [], 'skipped': []}}

    alerts = list((evaluated or {}).get('alerts') or [])
    observed = (evaluated or {}).get('observed') or {}
    result = {'enabled': True, 'dry_run': bool(dry_run), 'routed': [], 'skipped': [], 'email': {'sent': 0, 'items': [], 'skipped': []}}
    if not alerts:
        return result

    db = get_db()
    _ensure_ops_alert_routing_table()

    dedupe_seconds = max(int(current_app.config.get('OPS_ALERT_ROUTE_DEDUPE_SECONDS', 900) or 900), 0)
    escalate_threshold = max(int(current_app.config.get('OPS_ALERT_ESCALATION_REPEAT_THRESHOLD', 3) or 3), 1)
    escalate_cooldown = max(int(current_app.config.get('OPS_ALERT_ESCALATION_COOLDOWN_SECONDS', 1800) or 1800), 0)

    pubsub_enabled = bool(current_app.config.get('OPS_ALERT_ROUTE_PAGER_ENABLED', True))
    pubsub_client = current_app.extensions.get('google_ops_pubsub_client') or current_app.extensions.get('google_pubsub_client')
    topic_ref = ''
    if pubsub_client:
        topic_ref = str(getattr(pubsub_client, 'topic_path', '') or '')[:240]

    now = datetime.now(timezone.utc)

    for alert in alerts:
        alert_type = str(alert.get('type') or '').strip() or 'ops_alert'
        base_rank = _severity_rank(str(alert.get('severity') or 'warning'))
        route_key = hashlib.sha256(f'google_pubsub::{alert_type}'.encode('utf-8')).hexdigest()

        prior = db.execute("SELECT severity_rank, repeat_count, updated_at FROM ops_alert_routing_events WHERE route_key = ? LIMIT 1", (route_key,)).fetchone()

        prior_rank = int(prior['severity_rank']) if prior else 0
        prior_repeat = int(prior['repeat_count']) if prior else 0
        last_ts = 0
        if prior and prior['updated_at']:
            try:
                last_ts = int(datetime.fromisoformat(str(prior['updated_at']).replace('Z', '+00:00')).timestamp())
            except Exception:
                last_ts = 0

        now_ts = int(now.timestamp())
        age = (now_ts - last_ts) if last_ts > 0 else 999999

        repeat_count = (prior_repeat + 1) if prior else 1
        escalation_due_to_repeat = repeat_count >= escalate_threshold and age >= escalate_cooldown
        escalation_due_to_rank = base_rank > prior_rank and prior_rank > 0
        escalated = bool(escalation_due_to_repeat or escalation_due_to_rank)

        effective_rank = base_rank
        if escalation_due_to_repeat and base_rank < 3:
            effective_rank = min(3, base_rank + 1)

        if dedupe_seconds > 0 and age < dedupe_seconds and effective_rank <= prior_rank:
            result['skipped'].append({'alert': alert, 'channel': 'google_pubsub', 'reason': 'dedupe_window', 'age_seconds': age})
            continue

        payload = {
            'source': 'nexus-visbharath',
            'actor': actor,
            'as_of': now.isoformat().replace('+00:00', 'Z'),
            'alert': {
                'type': alert_type,
                'severity': _severity_label(effective_rank),
                'severity_rank': effective_rank,
                'threshold': alert.get('threshold'),
                'observed': alert.get('observed'),
                'escalated': escalated,
                'repeat_count': repeat_count,
            },
            'observed': observed,
        }
        incident_payload = _build_cloud_monitoring_incident_payload(payload, alert_type=alert_type, effective_rank=effective_rank)

        status = 'dry_run' if dry_run else 'skipped'
        response_code = None
        response_body = ''

        if pubsub_enabled:
            if dry_run:
                status = 'dry_run'
            else:
                if not pubsub_client:
                    status = 'skipped'
                    response_body = 'google_pubsub_client unavailable'
                else:
                    try:
                        msg_id = _publish_ops_alert_to_pubsub(incident_payload)
                        response_body = f'pubsub_message_id={msg_id}'
                        response_code = 200
                        status = 'sent'
                    except Exception as err:
                        status = 'failed'
                        response_body = str(err)[:300]
        else:
            status = 'skipped'
            response_body = 'google pubsub routing disabled'

        if not dry_run and status in {'sent', 'failed', 'skipped'}:
            db.execute(
                "INSERT INTO ops_alert_routing_events (route_key, alert_type, target_kind, target_ref, severity, severity_rank, repeat_count, escalated, status, channel, response_code, response_body, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(route_key) DO UPDATE SET severity = excluded.severity, severity_rank = excluded.severity_rank, repeat_count = excluded.repeat_count, escalated = excluded.escalated, status = excluded.status, response_code = excluded.response_code, response_body = excluded.response_body, payload_json = excluded.payload_json, updated_at = excluded.updated_at",
                (route_key, alert_type, 'google_pubsub', topic_ref, _severity_label(effective_rank), int(effective_rank), int(repeat_count), 1 if escalated else 0, status, 'google_pubsub', response_code, response_body[:1000], json.dumps(incident_payload), now.isoformat().replace('+00:00', 'Z'), now.isoformat().replace('+00:00', 'Z')),
            )
            db.commit()

        result['routed'].append({'channel': 'google_pubsub', 'provider': 'google_pubsub', 'topic': topic_ref, 'status': status, 'response_code': response_code, 'alert': payload['alert']})

    if bool(current_app.config.get('OPS_ALERT_ROUTE_EMAIL_ENABLED', True)) and (not dry_run):
        email_dispatch = emit_notification_slo_alerts(evaluated, actor=actor)
        result['email'] = email_dispatch

    return result


def _serialize_ops_daily_csv(snapshot: dict) -> str:
    rows = [
        ('as_of', snapshot['as_of']),
        ('pipeline_total_jobs', (((snapshot.get('components') or {}).get('pipeline') or {}).get('total_jobs') or 0)),
        ('pipeline_pending_dead_letters', (((snapshot.get('components') or {}).get('cloud_ingestion') or {}).get('pending_dead_letters') or 0)),
        ('asr_total_events', (((snapshot.get('components') or {}).get('asr') or {}).get('total_events') or 0)),
        ('notification_failed', (((((snapshot.get('components') or {}).get('notifications') or {}).get('metrics') or {}).get('failed') or 0))),
        ('satellite_total_signals', ((((snapshot.get('components') or {}).get('satellite_signals') or {}).get('total_signals') or 0))),
        ('alerts_count', len((((snapshot.get('alerts') or {}).get('alerts')) or []))),
    ]
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(['metric', 'value'])
    for r in rows:
        writer.writerow([r[0], r[1]])
    return sio.getvalue()


def _sign_ops_artifact(artifact_text: str, content_type: str = 'application/json'):
    enabled = bool(current_app.config.get('OPS_DAILY_REPORT_SIGNING_ENABLED', True))
    if not enabled:
        return {'enabled': False}

    secret = str(current_app.config.get('OPS_DAILY_REPORT_SIGNING_SECRET') or current_app.config.get('SECRET_KEY') or '').strip()
    key_id = str(current_app.config.get('OPS_DAILY_REPORT_SIGNING_KEY_ID') or 'ops-hs256-v1').strip()
    if not secret:
        return {'enabled': False, 'error': 'missing_signing_secret'}

    ts = str(int(time.time()))
    body_hash = hashlib.sha256((artifact_text or '').encode('utf-8')).hexdigest()
    canonical = f'{ts}:{content_type}:{body_hash}'
    sig = hmac.new(secret.encode('utf-8'), canonical.encode('utf-8'), hashlib.sha256).hexdigest()
    return {'enabled': True, 'algorithm': 'HMAC-SHA256', 'key_id': key_id, 'timestamp': ts, 'content_type': content_type, 'body_sha256': body_hash, 'signature': sig, 'canonical': canonical}


@ops_bp.route('/api/v1/ops/reliability-dashboard', methods=['GET'])
@require_roles('admin', 'auditor', 'analyst')
def ops_reliability_dashboard_secure():
    limit = min(max(int(request.args.get('limit', 100) or 100), 1), 500)

    cloud_snapshot = _safe_cloud_ingestion_snapshot()
    satellite_snapshot = _safe_satellite_signal_snapshot()
    payload = {
        'success': True,
        'as_of': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'components': {
            'ai': {
                'mode': _get_ai_mode(),
                'stt_mode': _get_stt_mode(),
                'gemini_health': (current_app.extensions.get('google_ai_client').get_health() if current_app.extensions.get('google_ai_client') and hasattr(current_app.extensions.get('google_ai_client'), 'get_health') else None),
            },
            'pipeline': get_pipeline_metrics(),
            'asr': _get_asr_metrics_summary(limit=min(limit, 500)),
            'maps': {
                'use_real_google_maps': bool(current_app.config.get('USE_REAL_GOOGLE_MAPS', False)),
                'api_key_configured': bool(str(current_app.config.get('GOOGLE_MAPS_API_KEY') or '').strip()),
                'client_initialized': bool(current_app.extensions.get('google_maps_client')),
            },
            'notifications': get_notification_ops_overlay(limit=min(limit, 200)),
            'cloud_ingestion': cloud_snapshot,
            'satellite_signals': satellite_snapshot,
        },
    }
    payload['alerts'] = _evaluate_ops_alerts_from_snapshot(payload)

    write_audit_log(
        actor=g.current_user['name'],
        action='ops_reliability_dashboard_viewed',
        resource_type='ops',
        details={'limit': limit, 'alerts_count': len((payload.get('alerts') or {}).get('alerts') or [])},
    )
    return jsonify(payload)


@ops_bp.route('/api/v1/ops/alerts/evaluate', methods=['GET'])
@require_roles('admin', 'auditor')
def ops_alerts_evaluate_secure():
    snapshot = _build_ops_snapshot(limit=200)
    evaluated = snapshot.get('alerts') or {'alerts': [], 'thresholds': {}, 'observed': {}}

    route = _bool_arg('route', False)
    dry_run = _bool_arg('dry_run', False)
    routing = None
    if route:
        routing = _route_ops_alerts(evaluated, actor=g.current_user['name'], dry_run=dry_run)

    write_audit_log(
        actor=g.current_user['name'],
        action='ops_alerts_evaluated',
        resource_type='ops',
        details={'alerts_count': len(evaluated.get('alerts') or []), 'route': route, 'dry_run': dry_run, 'routed': len((routing or {}).get('routed') or [])},
    )
    payload = {'success': True, **evaluated}
    if routing is not None:
        payload['routing'] = routing
    return jsonify(payload)


@ops_bp.route('/api/v1/ops/alerts/route', methods=['POST'])
@require_roles('admin', 'auditor')
def ops_alerts_route_secure():
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get('dry_run', False))
    snapshot = _build_ops_snapshot(limit=200)
    evaluated = snapshot.get('alerts') or {'alerts': [], 'thresholds': {}, 'observed': {}}

    routing = _route_ops_alerts(evaluated, actor=g.current_user['name'], dry_run=dry_run)

    write_audit_log(
        actor=g.current_user['name'],
        action='ops_alerts_routed',
        resource_type='ops',
        details={'dry_run': dry_run, 'alerts_count': len(evaluated.get('alerts') or []), 'routed': len((routing.get('routed') or []))},
    )
    return jsonify({'success': True, 'dry_run': dry_run, **evaluated, 'routing': routing})


@ops_bp.route('/api/v1/ops/webhooks/verify-sample', methods=['POST'])
@require_roles('admin', 'auditor')
def ops_webhook_verify_sample_secure():
    secret = str(current_app.config.get('OPS_WEBHOOK_VERIFY_SECRET') or '').strip()
    if not secret:
        return jsonify({'success': False, 'error': 'OPS_WEBHOOK_VERIFY_SECRET not configured'}), 503

    ts_raw = (request.headers.get('X-Ops-Timestamp') or '').strip()
    nonce = (request.headers.get('X-Ops-Nonce') or '').strip()
    signature = (request.headers.get('X-Ops-Signature') or '').strip()
    raw_body = request.get_data() or b''

    ok_ts, ts_err = _ops_verify_timestamp_and_nonce(ts_raw, nonce, nonce_scope='ops-webhook-verify')
    if not ok_ts:
        return jsonify({'success': False, 'error': ts_err}), 401

    ok_sig, sig_err = _ops_verify_request_signature(raw_body, secret=secret, ts_raw=ts_raw, nonce=nonce, provided_signature=signature)
    if not ok_sig:
        return jsonify({'success': False, 'error': sig_err}), 401

    payload = request.get_json(silent=True)
    if payload is None:
        payload = {'raw': raw_body.decode('utf-8', errors='ignore')[:1000]}

    write_audit_log(
        actor=g.current_user['name'],
        action='ops_webhook_verify_sample_accepted',
        resource_type='ops',
        details={'nonce': nonce[:24], 'timestamp': ts_raw, 'bytes': len(raw_body)},
    )
    return jsonify({'success': True, 'verified': True, 'received': payload})


@ops_bp.route('/api/v1/ops/routing/status', methods=['GET'])
@require_roles('admin', 'auditor', 'analyst')
def ops_routing_status_secure():
    citizen_client = current_app.extensions.get('google_pubsub_client')
    ops_client = current_app.extensions.get('google_ops_pubsub_client')

    citizen_topic_id = str(current_app.config.get('PUBSUB_TOPIC_ID') or '').strip()
    ops_topic_id = str(current_app.config.get('OPS_ALERT_PUBSUB_TOPIC_ID') or '').strip()

    citizen_topic_path = str(getattr(citizen_client, 'topic_path', '') or '')
    ops_topic_path = str(getattr(ops_client, 'topic_path', '') or '')

    payload = {
        'success': True,
        'as_of': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'routing': {
            'citizen_ingestion': {
                'client_initialized': bool(citizen_client),
                'topic_id': citizen_topic_id,
                'topic_path': citizen_topic_path,
            },
            'ops_alerts': {
                'client_initialized': bool(ops_client),
                'topic_id': ops_topic_id,
                'topic_path': ops_topic_path,
            },
        },
        'isolation': {
            'separate_clients': bool(citizen_client) and bool(ops_client),
            'separate_topic_ids': bool(citizen_topic_id) and bool(ops_topic_id) and citizen_topic_id != ops_topic_id,
            'separate_topic_paths': bool(citizen_topic_path) and bool(ops_topic_path) and citizen_topic_path != ops_topic_path,
        },
    }

    write_audit_log(
        actor=g.current_user['name'],
        action='ops_routing_status_viewed',
        resource_type='ops',
        details={
            'citizen_topic_id': citizen_topic_id or None,
            'ops_topic_id': ops_topic_id or None,
            'separate_topic_ids': payload['isolation']['separate_topic_ids'],
            'separate_topic_paths': payload['isolation']['separate_topic_paths'],
        },
    )
    return jsonify(payload)


@ops_bp.route('/api/v1/ops/report/daily', methods=['GET'])
@require_roles('admin', 'auditor', 'analyst')
def ops_daily_report_secure():
    fmt = str(request.args.get('format') or 'json').strip().lower()
    signed = _bool_arg('signed', False)

    snapshot = _build_ops_snapshot(limit=200)

    if fmt == 'csv':
        csv_body = _serialize_ops_daily_csv(snapshot)
        sign_meta = _sign_ops_artifact(csv_body, content_type='text/csv') if signed else {'enabled': False}

        headers = {
            'Content-Type': 'text/csv; charset=utf-8',
            'Content-Disposition': 'attachment; filename="ops_daily_report.csv"',
            'Cache-Control': 'no-store',
        }
        if sign_meta.get('enabled'):
            headers['X-Ops-Artifact-Alg'] = str(sign_meta.get('algorithm'))
            headers['X-Ops-Artifact-Key-Id'] = str(sign_meta.get('key_id'))
            headers['X-Ops-Artifact-Timestamp'] = str(sign_meta.get('timestamp'))
            headers['X-Ops-Artifact-Body-Sha256'] = str(sign_meta.get('body_sha256'))
            headers['X-Ops-Artifact-Signature'] = str(sign_meta.get('signature'))

        write_audit_log(
            actor=g.current_user['name'],
            action='ops_daily_report_downloaded',
            resource_type='ops',
            details={'format': 'csv', 'signed': bool(sign_meta.get('enabled'))},
        )
        return (csv_body, 200, headers)

    body = dict(snapshot)
    if signed:
        canonical_json = json.dumps(snapshot, sort_keys=True, separators=(',', ':'))
        sign_meta = _sign_ops_artifact(canonical_json, content_type='application/json')
        body['artifact_signature'] = sign_meta

    write_audit_log(
        actor=g.current_user['name'],
        action='ops_daily_report_viewed',
        resource_type='ops',
        details={'format': 'json', 'signed': signed},
    )
    return jsonify(body)


@ops_bp.route('/api/v1/ops/cloud-ingestion-health', methods=['GET'])
def get_cloud_ingestion_health():
    try:
        db = get_db()
        dead_letter_count_row = db.execute("SELECT count(*) as count FROM cloud_ingestion_dead_letters WHERE status = 'pending'").fetchone()
        pending_dead_letters = dead_letter_count_row['count'] if dead_letter_count_row else 0
        
        recent_rows = db.execute("SELECT request_id, district, state, source_channel, error_message, status, created_at FROM cloud_ingestion_dead_letters ORDER BY id DESC LIMIT 10").fetchall()
        dead_letters_list = [dict(r) for r in recent_rows]

        bq_client = current_app.extensions.get('google_bigquery_client')
        ps_client = current_app.extensions.get('google_pubsub_client')

        status = 'healthy' if pending_dead_letters == 0 else 'action_required'

        return jsonify({
            'success': True,
            'status': status,
            'pending_dead_letters': pending_dead_letters,
            'recent_dead_letters': dead_letters_list,
            'bigquery_stream_configured': bool(bq_client),
            'pubsub_configured': bool(ps_client),
            'canonical_table': 'nexus-visbharat.visbharat_analytics.citizen_requests_canonical',
            'dedup_strategy': 'BigQuery MERGE daily key deduplication',
            'as_of': datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'error',
            'error': str(e)
        }), 500





































































