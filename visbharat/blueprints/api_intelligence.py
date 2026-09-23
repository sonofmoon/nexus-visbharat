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

intelligence_bp = Blueprint('intelligence_bp', __name__)


@intelligence_bp.route('/api/v1/intelligence/demand-velocity', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def intelligence_demand_velocity_secure():
    days = int(request.args.get('days', 30))
    limit = int(request.args.get('limit', 50))
    payload = compute_demand_velocity(days=days, limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='intelligence_demand_velocity_viewed',
        resource_type='intelligence',
        details={'role': g.current_user['role'], 'days': payload.get('days'), 'limit': limit},
    )
    return jsonify({'success': True, **payload})


@intelligence_bp.route('/api/v1/intelligence/silence-map', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def intelligence_silence_map_secure():
    limit = int(request.args.get('limit', 50))
    payload = compute_silence_map(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='intelligence_silence_map_viewed',
        resource_type='intelligence',
        details={'role': g.current_user['role'], 'limit': limit},
    )
    return jsonify({'success': True, **payload})


@intelligence_bp.route('/api/v1/layer4/status', methods=['GET'])
@intelligence_bp.route('/api/v1/layer4/fusion/sources', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def layer4_fusion_sources_secure():
    payload = layer4_source_status()
    write_audit_log(
        actor=g.current_user['name'],
        action='layer4_fusion_sources_viewed',
        resource_type='layer4_fusion',
        details={'role': g.current_user['role'], 'configured_sources': payload.get('configured_sources'), 'ready_sources': payload.get('ready_sources')},
    )
    return jsonify({'success': True, **payload})


@intelligence_bp.route('/api/v1/layer4/fusion', methods=['GET'])
@intelligence_bp.route('/api/v1/policy/fusion-join', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def layer4_fusion_secure():
    try:
        limit = min(max(int(request.args.get('limit', 100) or 100), 1), 500)
    except ValueError:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    state = str(request.args.get('state') or '').strip()
    payload = compute_layer4_fusion(limit=limit, state=state)
    write_audit_log(
        actor=g.current_user['name'],
        action='layer4_fusion_viewed',
        resource_type='layer4_fusion',
        details={'role': g.current_user['role'], 'limit': limit, 'state': state or None, 'districts': int((payload.get('meta') or {}).get('total_districts') or 0)},
    )
    return jsonify({'success': True, **payload})


@intelligence_bp.route('/api/v1/intelligence/gap-analysis', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def intelligence_gap_analysis_secure():
    limit = min(int(request.args.get('limit', 50)), 500)
    rows = compute_gap_analysis(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='intelligence_gap_analysis_viewed',
        resource_type='intelligence',
        details={'limit': limit, 'role': g.current_user['role']},
    )
    return jsonify({'success': True, 'items': rows})


@intelligence_bp.route('/api/v1/intelligence/hotspot-predictions', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def intelligence_hotspot_predictions_secure():
    limit = min(int(request.args.get('limit', 50)), 500)
    rows = compute_hotspot_predictions(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='intelligence_hotspot_predictions_viewed',
        resource_type='intelligence',
        details={'limit': limit, 'role': g.current_user['role']},
    )
    return jsonify({'success': True, 'items': rows})





@intelligence_bp.route('/api/v1/intelligence/bigquery-analytics', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def intelligence_bigquery_analytics_secure():
    try:
        limit = min(max(int(request.args.get('limit', 50)), 1), 500)
    except ValueError:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    result = compute_bigquery_analytics(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='intelligence_bigquery_analytics_viewed',
        resource_type='intelligence',
        details={'limit': limit, 'role': g.current_user['role']},
    )
    return jsonify({'success': True, **result})


@intelligence_bp.route('/api/v1/intelligence/vertex-predictions', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def intelligence_vertex_predictions_secure():
    try:
        limit = min(max(int(request.args.get('limit', 50)), 1), 500)
    except ValueError:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    result = compute_vertex_predictions(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='intelligence_vertex_predictions_viewed',
        resource_type='intelligence',
        details={'limit': limit, 'role': g.current_user['role']},
    )
    return jsonify({'success': True, **result})



@intelligence_bp.route('/api/v1/demand/clusters/recompute', methods=['POST'])
@require_roles('admin', 'analyst')
def recompute_demand_clusters_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        limit = min(max(int(data.get('limit', 5000) or 5000), 1), 20000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    result = recompute_demand_clusters(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='demand_clusters_recomputed',
        resource_type='demand_cluster',
        details={'limit': limit, 'clusters': result.get('clusters', 0), 'members': result.get('members', 0)},
    )
    return jsonify({'success': True, **result})


@intelligence_bp.route('/api/v1/demand/clusters', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def list_demand_clusters_secure():
    try:
        limit = min(max(int(request.args.get('limit', 100) or 100), 1), 1000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    state = (request.args.get('state') or '').strip() or None
    category = (request.args.get('category') or '').strip() or None
    items = list_demand_clusters(limit=limit, state=state, category=category)

    write_audit_log(
        actor=g.current_user['name'],
        action='demand_clusters_viewed',
        resource_type='demand_cluster',
        details={'limit': limit, 'state': state, 'category': category, 'count': len(items)},
    )
    return jsonify({'success': True, 'items': items, 'meta': {'limit': limit, 'count': len(items)}})

@intelligence_bp.route('/api/v1/demand/clusters/merge', methods=['POST'])
@require_roles('admin', 'analyst')
def merge_demand_clusters_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    source_cluster_id = str(data.get('source_cluster_id') or '').strip()
    target_cluster_id = str(data.get('target_cluster_id') or '').strip()
    if not source_cluster_id or not target_cluster_id:
        return jsonify({'success': False, 'error': 'source_cluster_id and target_cluster_id are required'}), 400

    try:
        result = merge_demand_clusters(source_cluster_id=source_cluster_id, target_cluster_id=target_cluster_id)
    except ValueError as err:
        return jsonify({'success': False, 'error': str(err)}), 400

    write_audit_log(
        actor=g.current_user['name'],
        action='demand_clusters_merged',
        resource_type='demand_cluster',
        details={
            'source_cluster_id': source_cluster_id,
            'target_cluster_id': target_cluster_id,
            'moved_members': int(result.get('moved_members') or 0),
        },
    )
    return jsonify({'success': True, **result})


@intelligence_bp.route('/api/v1/demand/clusters/split', methods=['POST'])
@require_roles('admin', 'analyst')
def split_demand_cluster_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    source_cluster_id = str(data.get('source_cluster_id') or '').strip()
    request_ids = data.get('request_ids') or []
    new_cluster_id = str(data.get('new_cluster_id') or '').strip() or None

    if not source_cluster_id:
        return jsonify({'success': False, 'error': 'source_cluster_id is required'}), 400
    if not isinstance(request_ids, list) or not request_ids:
        return jsonify({'success': False, 'error': 'request_ids must be a non-empty list'}), 400

    try:
        result = split_demand_cluster(source_cluster_id=source_cluster_id, request_ids=request_ids, new_cluster_id=new_cluster_id)
    except ValueError as err:
        return jsonify({'success': False, 'error': str(err)}), 400

    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_split',
        resource_type='demand_cluster',
        details={
            'source_cluster_id': source_cluster_id,
            'new_cluster_id': result.get('new_cluster_id'),
            'moved_members': int(result.get('moved_members') or 0),
        },
    )
    return jsonify({'success': True, **result})


@intelligence_bp.route('/api/v1/demand/clusters/low-confidence', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def list_low_confidence_cluster_members_secure():
    try:
        limit = min(max(int(request.args.get('limit', 100) or 100), 1), 1000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    state = (request.args.get('state') or '').strip() or None
    category = (request.args.get('category') or '').strip() or None
    result = list_low_confidence_cluster_members(limit=limit, state=state, category=category)

    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_low_confidence_viewed',
        resource_type='demand_cluster',
        details={'limit': limit, 'state': state, 'category': category, 'count': len(result.get('items') or [])},
    )
    return jsonify({'success': True, **result, 'meta': {'limit': limit, 'count': len(result.get('items') or [])}})


@intelligence_bp.route('/api/v1/demand/clusters/rescore-low-confidence', methods=['POST'])
@require_roles('admin', 'analyst')
def rescore_low_confidence_cluster_members_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        limit = min(max(int(data.get('limit', 200) or 200), 1), 5000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    result = rescore_low_confidence_cluster_members(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_low_confidence_rescored',
        resource_type='demand_cluster',
        details={'limit': limit, 'rescored': int(result.get('rescored') or 0), 'still_low_confidence': int(result.get('still_low_confidence') or 0)},
    )
    return jsonify({'success': True, **result})

@intelligence_bp.route('/api/v1/demand/clusters/member-override', methods=['POST'])
@require_roles('admin', 'analyst')
def set_cluster_member_override_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    request_id = str(data.get('request_id') or '').strip()
    cluster_id = str(data.get('cluster_id') or '').strip()
    status = str(data.get('status') or data.get('decision') or '').strip().lower()
    reason = str(data.get('reason') or '').strip()

    if not request_id or not cluster_id or not status:
        return jsonify({'success': False, 'error': 'request_id, cluster_id and status are required'}), 400

    try:
        result = set_cluster_member_override(
            request_id=request_id,
            cluster_id=cluster_id,
            status=status,
            reason=reason,
            actor=g.current_user['name'],
        )
    except ValueError as err:
        return jsonify({'success': False, 'error': str(err)}), 400

    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_member_override_set',
        resource_type='demand_cluster',
        resource_id=request_id,
        details={'cluster_id': cluster_id, 'status': status, 'reason': reason},
    )
    return jsonify({'success': True, 'override': result})

@intelligence_bp.route('/api/v1/demand/clusters/member-overrides/history', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def list_cluster_member_override_history_secure():
    request_id = str(request.args.get('request_id') or '').strip()
    cluster_id = str(request.args.get('cluster_id') or '').strip() or None
    try:
        limit = min(max(int(request.args.get('limit', 100) or 100), 1), 1000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    if not request_id:
        return jsonify({'success': False, 'error': 'request_id is required'}), 400

    result = list_cluster_member_override_history(request_id=request_id, cluster_id=cluster_id, limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_member_override_history_viewed',
        resource_type='demand_cluster',
        resource_id=request_id,
        details={'cluster_id': cluster_id, 'limit': limit, 'count': len(result.get('items') or [])},
    )
    return jsonify({'success': True, **result, 'meta': {'limit': limit, 'count': len(result.get('items') or [])}})


@intelligence_bp.route('/api/v1/demand/clusters/member-overrides/sweep-stale', methods=['POST'])
@require_roles('admin', 'analyst')
def sweep_stale_cluster_member_overrides_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        stale_hours = max(int(data.get('stale_hours', 168) or 168), 1)
    except Exception:
        return jsonify({'success': False, 'error': 'stale_hours must be an integer'}), 400
    try:
        limit = min(max(int(data.get('limit', 500) or 500), 1), 5000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    result = sweep_stale_cluster_member_overrides(stale_hours=stale_hours, limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_member_overrides_swept',
        resource_type='demand_cluster',
        details={'stale_hours': stale_hours, 'limit': limit, 'swept': int(result.get('swept') or 0), 'pending_after': int(result.get('pending_after') or 0)},
    )
    return jsonify({'success': True, **result})

@intelligence_bp.route('/api/v1/demand/clusters/member-overrides/history/export', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def export_cluster_member_override_history_secure():
    try:
        limit = min(max(int(request.args.get('limit', 500) or 500), 1), 5000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    cluster_id = (request.args.get('cluster_id') or '').strip() or None
    status = (request.args.get('status') or '').strip().lower() or None
    actor = (request.args.get('actor') or '').strip() or None
    date_from = (request.args.get('date_from') or '').strip() or None
    date_to = (request.args.get('date_to') or '').strip() or None
    export_format = (request.args.get('format') or 'json').strip().lower()

    result = export_cluster_member_override_history(
        limit=limit,
        cluster_id=cluster_id,
        status=status,
        actor=actor,
        date_from=date_from,
        date_to=date_to,
    )
    items = result.get('items') or []

    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_member_override_history_exported',
        resource_type='demand_cluster',
        details={'limit': limit, 'cluster_id': cluster_id, 'status': status, 'actor': actor, 'count': len(items), 'format': export_format},
    )

    if export_format == 'csv':
        import csv
        import io

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['request_id', 'cluster_id', 'status', 'decision', 'reason', 'actor', 'created_at', 'updated_at'])
        writer.writeheader()
        for item in items:
            writer.writerow(item)

        return current_app.response_class(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=cluster_member_overrides.csv'},
        )

    return jsonify({'success': True, 'items': items, 'meta': {'limit': limit, 'count': len(items), 'format': 'json'}})


@intelligence_bp.route('/api/v1/demand/clusters/member-overrides/metrics', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def get_cluster_member_override_metrics_secure():
    try:
        limit = min(max(int(request.args.get('limit', 2000) or 2000), 1), 5000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    metrics = get_cluster_member_override_metrics(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_member_override_metrics_viewed',
        resource_type='demand_cluster',
        details={'limit': limit},
    )
    return jsonify({'success': True, 'metrics': metrics})


@intelligence_bp.route('/api/v1/demand/clusters/member-overrides/alerts', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def get_cluster_member_override_alerts_secure():
    try:
        recent_hours = max(int(request.args.get('recent_hours', 24) or 24), 1)
    except Exception:
        return jsonify({'success': False, 'error': 'recent_hours must be an integer'}), 400
    try:
        rejected_spike_threshold = max(int(request.args.get('rejected_spike_threshold', 10) or 10), 1)
    except Exception:
        return jsonify({'success': False, 'error': 'rejected_spike_threshold must be an integer'}), 400
    try:
        superseded_spike_threshold = max(int(request.args.get('superseded_spike_threshold', 10) or 10), 1)
    except Exception:
        return jsonify({'success': False, 'error': 'superseded_spike_threshold must be an integer'}), 400

    notify = str(request.args.get('notify', 'false')).strip().lower() in {'1', 'true', 'yes'}

    result = get_cluster_member_override_alerts(
        rejected_spike_threshold=rejected_spike_threshold,
        superseded_spike_threshold=superseded_spike_threshold,
        recent_hours=recent_hours,
    )

    notifications = []
    if notify and (result.get('alerts') or []):
        stage = 'override_alert'
        alert_request_id = f"OVR-ALERT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        details = {
            'recent_hours': recent_hours,
            'rejected_spike_threshold': rejected_spike_threshold,
            'superseded_spike_threshold': superseded_spike_threshold,
            'alerts': result.get('alerts') or [],
            'recent_counts': result.get('recent_counts') or {},
        }
        notification = dispatch_sla_notification(
            request_id=alert_request_id,
            stage=stage,
            actor=g.current_user['name'],
            source_channel='override-ops',
            target={'department': 'Override Ops', 'assignee': 'Duty Analyst'},
            sla_due_at=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            details=details,
        )
        notifications.append(notification)

    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_member_override_alerts_viewed',
        resource_type='demand_cluster',
        details={
            'recent_hours': recent_hours,
            'rejected_spike_threshold': rejected_spike_threshold,
            'superseded_spike_threshold': superseded_spike_threshold,
            'alerts': len(result.get('alerts') or []),
            'notify': notify,
            'notifications_sent': len(notifications),
        },
    )
    return jsonify({'success': True, **result, 'notifications': notifications, 'notify': notify})


@intelligence_bp.route('/api/v1/demand/clusters/member-overrides/backlog-summary', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def get_cluster_member_override_backlog_summary_secure():
    result = get_cluster_member_override_backlog_summary()
    write_audit_log(
        actor=g.current_user['name'],
        action='demand_cluster_member_override_backlog_summary_viewed',
        resource_type='demand_cluster',
        details={'pending_total': int(result.get('pending_total') or 0)},
    )
    return jsonify({'success': True, **result})






def _ensure_satellite_signal_table():
    db = get_db()
    backend = str(getattr(db, 'backend', 'sqlite') or 'sqlite').strip().lower()
    if backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS anti_capture_satellite_signals (
                id SERIAL PRIMARY KEY,
                signal_id TEXT UNIQUE NOT NULL,
                project_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                signal_value DOUBLE PRECISION NOT NULL,
                signal_unit TEXT,
                observed_at TEXT NOT NULL,
                source_provider TEXT NOT NULL,
                source_uri TEXT,
                source_checksum_sha256 TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                ingested_by TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS anti_capture_satellite_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id TEXT UNIQUE NOT NULL,
                project_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                signal_value REAL NOT NULL,
                signal_unit TEXT,
                observed_at TEXT NOT NULL,
                source_provider TEXT NOT NULL,
                source_uri TEXT,
                source_checksum_sha256 TEXT NOT NULL,
                provenance_json TEXT NOT NULL,
                ingested_by TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def _compute_signal_checksum(payload: dict) -> str:
    canonical = json.dumps(payload or {}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _get_latest_satellite_signal(project_id: str, signal_type: str):
    _ensure_satellite_signal_table()
    db = get_db()
    row = db.execute(
        '''
        SELECT signal_id, project_id, signal_type, signal_value, signal_unit,
               observed_at, source_provider, source_uri, source_checksum_sha256,
               provenance_json, ingested_by, ingested_at
        FROM anti_capture_satellite_signals
        WHERE project_id = ? AND signal_type = ?
        ORDER BY observed_at DESC, ingested_at DESC
        LIMIT 1
        ''',
        (str(project_id or '').strip(), str(signal_type or '').strip()),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    try:
        item['provenance'] = json.loads(item.get('provenance_json') or '{}')
    except Exception:
        item['provenance'] = {}
    item.pop('provenance_json', None)
    return item


def _insert_satellite_signal_record(*, project_id: str, signal_type: str, signal_value: float, signal_unit: str, observed_at: str, source_provider: str, source_uri: str, provenance: dict, actor: str):
    _ensure_satellite_signal_table()
    db = get_db()
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    signal_id = f"SIG-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"

    checksum_payload = {
        'project_id': project_id,
        'signal_type': signal_type,
        'signal_value': signal_value,
        'signal_unit': signal_unit,
        'observed_at': observed_at,
        'source_provider': source_provider,
        'source_uri': source_uri,
        'provenance': provenance,
    }
    checksum = _compute_signal_checksum(checksum_payload)

    db.execute(
        '''
        INSERT INTO anti_capture_satellite_signals (
            signal_id, project_id, signal_type, signal_value, signal_unit,
            observed_at, source_provider, source_uri, source_checksum_sha256,
            provenance_json, ingested_by, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            signal_id,
            project_id,
            signal_type,
            signal_value,
            signal_unit,
            observed_at,
            source_provider,
            source_uri,
            checksum,
            json.dumps(provenance or {}),
            actor,
            now_iso,
        ),
    )
    db.commit()

    write_audit_log(
        actor=actor,
        action='anti_capture_satellite_signal_ingested',
        resource_type='anti_capture_satellite_signal',
        resource_id=signal_id,
        details={
            'project_id': project_id,
            'signal_type': signal_type,
            'signal_value': signal_value,
            'observed_at': observed_at,
            'source_provider': source_provider,
            'source_checksum_sha256': checksum,
        },
    )

    return {
        'signal_id': signal_id,
        'project_id': project_id,
        'signal_type': signal_type,
        'signal_value': signal_value,
        'signal_unit': signal_unit,
        'observed_at': observed_at,
        'source_provider': source_provider,
        'source_uri': source_uri,
        'source_checksum_sha256': checksum,
        'ingested_by': actor,
        'ingested_at': now_iso,
    }
@intelligence_bp.route('/api/v1/anti-capture/feed', methods=['GET'])
@require_roles('admin','auditor')
def get_anti_capture_feed():
    from ..services import auditor_workbench as audit
    scope=audit.scope_from(request.args)
    if scope.get('project_id'):
        result=audit.financial_review(scope['project_id'],scope)
        return jsonify(success=True,projects=[result],count=1)
    result=audit.projects(scope,limit=audit.page_limit(request.args.get('limit')))
    return jsonify(success=True,projects=result['items'],count=result['total'],notice='Select a project to inspect documented financial and site evidence. No automated fraud or financial decisions.')


@intelligence_bp.route('/api/v1/anti-capture/satellite-signals', methods=['POST'])
@require_roles('admin', 'auditor')
def ingest_satellite_signal_secure():
    try:
        data = request.get_json(silent=True) or {}
        project_id = str(data.get('project_id') or '').strip()
        signal_type = str(data.get('signal_type') or 'satellite_progress_pct').strip()
        if not project_id:
            return jsonify({'success': False, 'error': 'project_id is required'}), 400
        if not signal_type:
            return jsonify({'success': False, 'error': 'signal_type is required'}), 400

        signal_value = float(data.get('signal_value'))
        signal_unit = str(data.get('signal_unit') or 'pct').strip()[:20]
        observed_at = str(data.get('observed_at') or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')).strip()
        source_provider = str(data.get('source_provider') or 'google_earth_engine').strip()
        source_uri = str(data.get('source_uri') or '').strip()[:500]
        provenance = data.get('provenance') if isinstance(data.get('provenance'), dict) else {}

        actor = str((g.current_user or {}).get('name') or 'unknown')
        signal = _insert_satellite_signal_record(
            project_id=project_id,
            signal_type=signal_type,
            signal_value=signal_value,
            signal_unit=signal_unit,
            observed_at=observed_at,
            source_provider=source_provider,
            source_uri=source_uri,
            provenance=provenance,
            actor=actor,
        )

        return jsonify({'success': True, 'signal': signal})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@intelligence_bp.route('/api/v1/anti-capture/satellite-signals/live', methods=['POST'])
@require_roles('admin', 'auditor')
def ingest_satellite_signal_live_secure():
    try:
        from ..services.earth_engine import fetch_earth_engine_signal
        data = request.get_json(silent=True) or {}
        project_id = str(data.get('project_id') or '').strip()
        signal_type = str(data.get('signal_type') or 'satellite_progress_pct').strip()
        geo = data.get('geo') if isinstance(data.get('geo'), dict) else {}

        if not project_id:
            return jsonify({'success': False, 'error': 'project_id is required'}), 400

        ee_res = fetch_earth_engine_signal(
            project_id_or_geo={'project_id': project_id, 'geo': geo},
            signal_type=signal_type
        )

        actor = str((g.current_user or {}).get('name') or 'unknown')
        signal = _insert_satellite_signal_record(
            project_id=project_id,
            signal_type=ee_res['signal_type'],
            signal_value=ee_res['signal_value'],
            signal_unit=ee_res.get('signal_unit', 'pct'),
            observed_at=ee_res['observed_at'],
            source_provider=ee_res['source_provider'],
            source_uri=ee_res['source_uri'],
            provenance=ee_res['provenance'],
            actor=actor,
        )

        return jsonify({
            'success': True,
            'signal': signal,
            'earth_engine_result': ee_res,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@intelligence_bp.route('/api/v1/anti-capture/satellite-signals/bulk', methods=['POST'])
@require_roles('admin', 'auditor')
def ingest_satellite_signals_bulk_secure():
    actor = str((g.current_user or {}).get('name') or 'unknown')
    errors = []
    inserted = []

    file_obj = None
    if request.files:
        file_obj = request.files.get('file') or request.files.get('csv')

    if file_obj:
        raw_csv = file_obj.read().decode('utf-8-sig', errors='replace')
    else:
        body = request.get_json(silent=True) or {}
        raw_csv = str(body.get('csv_text') or '').strip()

    if not raw_csv:
        return jsonify({'success': False, 'error': 'CSV input is required. Provide multipart file field `file` or JSON `csv_text`.'}), 400

    reader = csv.DictReader(io.StringIO(raw_csv))
    required = {'project_id', 'signal_value'}
    headers = {str(h or '').strip() for h in (reader.fieldnames or [])}
    if not required.issubset(headers):
        return jsonify({'success': False, 'error': 'CSV must include headers: project_id, signal_value', 'headers': list(headers)}), 400

    for idx, row in enumerate(reader, start=2):
        try:
            project_id = str((row.get('project_id') or '')).strip()
            if not project_id:
                raise ValueError('project_id is required')

            signal_type = str((row.get('signal_type') or 'satellite_progress_pct')).strip() or 'satellite_progress_pct'
            signal_value = float(row.get('signal_value'))
            signal_unit = str((row.get('signal_unit') or 'pct')).strip()[:20]
            observed_at = str((row.get('observed_at') or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))).strip()
            source_provider = str((row.get('source_provider') or 'google_earth_engine')).strip()
            source_uri = str((row.get('source_uri') or '')).strip()[:500]

            provenance = {}
            provenance_raw = str((row.get('provenance_json') or row.get('provenance') or '')).strip()
            if provenance_raw:
                try:
                    parsed = json.loads(provenance_raw)
                    if isinstance(parsed, dict):
                        provenance = parsed
                except Exception:
                    provenance = {'raw': provenance_raw[:1000]}

            signal = _insert_satellite_signal_record(
                project_id=project_id,
                signal_type=signal_type,
                signal_value=signal_value,
                signal_unit=signal_unit,
                observed_at=observed_at,
                source_provider=source_provider,
                source_uri=source_uri,
                provenance=provenance,
                actor=actor,
            )
            inserted.append(signal)
        except Exception as err:
            errors.append({'row': idx, 'error': str(err)[:240], 'project_id': str((row or {}).get('project_id') or '').strip()})

    write_audit_log(
        actor=actor,
        action='anti_capture_satellite_signal_bulk_ingested',
        resource_type='anti_capture_satellite_signal',
        details={
            'inserted_count': len(inserted),
            'error_count': len(errors),
            'total_rows': len(inserted) + len(errors),
        },
    )

    return jsonify(
        {
            'success': True,
            'inserted_count': len(inserted),
            'error_count': len(errors),
            'inserted': inserted[:50],
            'errors': errors[:200],
        }
    )


@intelligence_bp.route('/api/v1/anti-capture/satellite-signals/bulk/template', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def download_satellite_signals_bulk_template_secure():
    header = [
        'project_id',
        'signal_type',
        'signal_value',
        'signal_unit',
        'observed_at',
        'source_provider',
        'source_uri',
        'provenance_json',
    ]
    sample = [
        'PRJ-2026-KAR-019',
        'satellite_progress_pct',
        '41.2',
        'pct',
        '2026-09-16T16:30:00Z',
        'google_earth_engine',
        'gs://nexus-visbharat-ee-exports/karur_2026_09_16.csv',
        '{"dataset":"COPERNICUS/S2_SR","ee_script":"users/nvb/karur_progress_v2"}',
    ]

    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerow(header)
    writer.writerow(sample)
    payload = sio.getvalue()

    write_audit_log(
        actor=g.current_user['name'],
        action='anti_capture_satellite_signal_bulk_template_downloaded',
        resource_type='anti_capture_satellite_signal',
        details={'template': 'satellite_signals_bulk', 'format': 'csv'},
    )

    return (
        payload,
        200,
        {
            'Content-Type': 'text/csv; charset=utf-8',
            'Content-Disposition': 'attachment; filename="anti_capture_satellite_signals_bulk_template.csv"',
            'Cache-Control': 'no-store',
        },
    )


@intelligence_bp.route('/api/v1/anti-capture/satellite-signals', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def list_satellite_signals_secure():
    _ensure_satellite_signal_table()
    project_id = str(request.args.get('project_id') or '').strip()
    signal_type = str(request.args.get('signal_type') or '').strip()
    limit = min(max(int(request.args.get('limit', 50) or 50), 1), 500)

    db = get_db()
    query = (
        'SELECT signal_id, project_id, signal_type, signal_value, signal_unit, observed_at, '
        'source_provider, source_uri, source_checksum_sha256, provenance_json, ingested_by, ingested_at '
        'FROM anti_capture_satellite_signals WHERE 1=1'
    )
    params = []
    if project_id:
        query += ' AND project_id = ?'
        params.append(project_id)
    if signal_type:
        query += ' AND signal_type = ?'
        params.append(signal_type)
    query += ' ORDER BY observed_at DESC, ingested_at DESC LIMIT ?'
    params.append(limit)

    rows = db.execute(query, tuple(params)).fetchall()
    items = []
    for row in rows:
        obj = dict(row)
        try:
            obj['provenance'] = json.loads(obj.get('provenance_json') or '{}')
        except Exception:
            obj['provenance'] = {}
        obj.pop('provenance_json', None)
        items.append(obj)

    return jsonify({'success': True, 'count': len(items), 'items': items, 'meta': {'limit': limit, 'project_id': project_id or None, 'signal_type': signal_type or None}})

@intelligence_bp.route('/api/v1/anti-capture/simulate', methods=['POST'])
@require_roles('admin','auditor')
def simulate_anti_capture_divergence():
    from ..services.anti_capture_triangulation import compute_triangulation_divergence
    data=request.get_json(silent=True) or {}
    result=compute_triangulation_divergence(str(data.get('project_id') or 'illustrative'),data.get('pfms_disbursed_pct',90),
        data.get('satellite_progress_pct',25),data.get('citizen_complaint_pct',85),data.get('gemini_defect_score',.85))
    return jsonify(success=True,triangulation_result=result)


@intelligence_bp.route('/api/v1/anti-capture/dispatch-audit', methods=['POST'])
@require_roles('admin','auditor')
def dispatch_third_party_audit():
    from ..services import auditor_actions, auditor_workbench as audit
    data=request.get_json(silent=True) or {}
    result=auditor_actions.create_case(data,audit.scope_from(request.args))
    return jsonify(success=True,case=result,dispatch_status='REVIEW_CASE_CREATED',disbursement_state='NO_FINANCIAL_ACTION'),201


@intelligence_bp.route('/api/v1/policy/sps-rankings', methods=['GET'])
def get_social_priority_score_rankings():
    from ..services.analyst_compat import priorities
    return jsonify(priorities(request.args))


