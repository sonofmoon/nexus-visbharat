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
from ..services.public_safety import allow as allow_public_request
from ..services.code_mix import normalize_code_mix
from ..services.policy_outputs import compute_demand_investment_alignment_map, generate_rag_policy_brief
from ..services.layer4_fusion import layer4_source_status, compute_layer4_fusion
from ..services.pii_scrubber import scrub_text, scrub_payload_fields
from ..services.ai_simulation import (
    simulate_gemini_intent_classification, simulate_speech_to_text,
    simulate_translation, simulate_dialogflow_cx_turn
)
from .api import *

governance_bp = Blueprint('governance_bp', __name__)


def _federation_source_key(source: str, external_event_id: str):
    return f"{str(source or '').strip().lower()}::{str(external_event_id or '').strip()}"


def _federation_find_existing(source: str, external_event_id: str):
    db = get_db()
    key = _federation_source_key(source, external_event_id)
    row = db.execute(
        '''
        SELECT source, external_event_id, source_key, request_id, status, created_at, updated_at
        FROM federation_external_events
        WHERE source_key = ?
        LIMIT 1
        ''',
        (key,),
    ).fetchone()
    return dict(row) if row else None


def _federation_record_event(source: str, external_event_id: str, request_id: str, payload: dict, status: str = 'accepted'):
    db = get_db()
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    key = _federation_source_key(source, external_event_id)
    existing = _federation_find_existing(source, external_event_id)
    if existing:
        db.execute(
            '''
            UPDATE federation_external_events
            SET request_id = ?, payload_json = ?, status = ?, updated_at = ?
            WHERE source_key = ?
            ''',
            (str(request_id), json.dumps(payload or {}), str(status), now, key),
        )
    else:
        db.execute(
            '''
            INSERT INTO federation_external_events (
                source, external_event_id, source_key, request_id, payload_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (str(source), str(external_event_id), key, str(request_id), json.dumps(payload or {}), str(status), now, now),
        )
    db.commit()


def _normalize_federation_payload(payload: dict):
    data = payload or {}
    body = {
        'channel': str(data.get('channel') or 'Federation').strip() or 'Federation',
        'language': str(data.get('language') or 'en').strip().lower() or 'en',
        'district': str(data.get('district') or '').strip(),
        'text': str(data.get('text') or '').strip(),
        'sender': str(data.get('sender') or data.get('submitted_by') or 'federation').strip() or 'federation',
        'ward': str(data.get('ward') or '').strip(),
        'pipeline': {'federation': True, 'source': str(data.get('source') or '').strip(), 'external_event_id': str(data.get('external_event_id') or '').strip()},
    }
    return body


def _federation_schema_document():
    schema_path = os.path.join(os.getcwd(), 'docs', 'release', 'india_demand_federation.schema.json')
    if os.path.exists(schema_path):
        try:
            with open(schema_path, 'r', encoding='utf-8') as handle:
                return json.load(handle)
        except Exception:
            pass
    return {
        '': 'https://json-schema.org/draft/2020-12/schema',
        'title': 'India Demand Federation Publish Event',
        'type': 'object',
        'required': ['source', 'external_event_id', 'text', 'district'],
        'properties': {
            'source': {'type': 'string', 'minLength': 2},
            'external_event_id': {'type': 'string', 'minLength': 2},
            'channel': {'type': 'string'},
            'language': {'type': 'string'},
            'district': {'type': 'string'},
            'state': {'type': 'string'},
            'text': {'type': 'string', 'minLength': 3},
            'sender': {'type': 'string'},
            'ward': {'type': 'string'},
            'occurred_at': {'type': 'string'},
            'meta': {'type': 'object'},
        },
    }


@governance_bp.route('/api/v1/federation/schema', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def federation_schema_secure():
    schema = _federation_schema_document()
    write_audit_log(
        actor=g.current_user['name'],
        action='federation_schema_viewed',
        resource_type='federation',
        details={'schema_title': schema.get('title', 'unknown')},
    )
    return jsonify({'success': True, 'schema': schema})


@governance_bp.route('/api/v1/federation/publish', methods=['POST'])
@require_roles('admin', 'analyst')
def federation_publish_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    source = str(data.get('source') or '').strip()
    external_event_id = str(data.get('external_event_id') or '').strip()
    if not source or not external_event_id:
        return jsonify({'success': False, 'error': 'source and external_event_id are required'}), 400

    existing = _federation_find_existing(source, external_event_id)
    if existing:
        return jsonify({'success': True, 'request_id': existing['request_id'], 'federation_reused': True, 'source': source, 'external_event_id': external_event_id})

    normalized = _normalize_federation_payload(data)
    if not normalized.get('text'):
        return jsonify({'success': False, 'error': 'text is required'}), 400

    result = process_ingestion_payload(normalized)
    request_id = str(result.get('request_id') or '')
    _federation_record_event(source, external_event_id, request_id, data, status='accepted')

    write_audit_log(
        actor=g.current_user['name'],
        action='federation_publish_accepted',
        resource_type='federation',
        resource_id=request_id,
        details={'source': source, 'external_event_id': external_event_id},
    )
    return jsonify({'success': True, 'request_id': request_id, 'federation_reused': False, 'source': source, 'external_event_id': external_event_id, 'ingestion': result})


@governance_bp.route('/api/v1/federation/batch-publish', methods=['POST'])
@require_roles('admin', 'analyst')
def federation_batch_publish_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    items = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return jsonify({'success': False, 'error': 'items must be a non-empty list'}), 400

    accepted = 0
    reused = 0
    failed = 0
    results = []

    for item in items[:1000]:
        source = str((item or {}).get('source') or '').strip()
        external_event_id = str((item or {}).get('external_event_id') or '').strip()
        if not source or not external_event_id:
            failed += 1
            results.append({'success': False, 'error': 'source and external_event_id are required', 'item': item})
            continue

        existing = _federation_find_existing(source, external_event_id)
        if existing:
            reused += 1
            results.append({'success': True, 'request_id': existing['request_id'], 'federation_reused': True, 'source': source, 'external_event_id': external_event_id})
            continue

        normalized = _normalize_federation_payload(item or {})
        if not normalized.get('text'):
            failed += 1
            results.append({'success': False, 'error': 'text is required', 'source': source, 'external_event_id': external_event_id})
            continue

        try:
            ingest = process_ingestion_payload(normalized)
            request_id = str(ingest.get('request_id') or '')
            _federation_record_event(source, external_event_id, request_id, item or {}, status='accepted')
            accepted += 1
            results.append({'success': True, 'request_id': request_id, 'federation_reused': False, 'source': source, 'external_event_id': external_event_id})
        except Exception as err:
            failed += 1
            results.append({'success': False, 'error': str(err), 'source': source, 'external_event_id': external_event_id})

    write_audit_log(
        actor=g.current_user['name'],
        action='federation_batch_publish_processed',
        resource_type='federation',
        details={'accepted': accepted, 'reused': reused, 'failed': failed, 'count': len(items)},
    )
    return jsonify({'success': True, 'accepted': accepted, 'reused': reused, 'failed': failed, 'results': results})

@governance_bp.route('/api/v1/policy/priority-rankings', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_priority_rankings_secure():
    from ..services.analyst_compat import rankings
    return jsonify(rankings(request.args))


@governance_bp.route('/api/v1/policy/scoring-weights', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_scoring_weights_get_secure():
    profile = str(request.args.get('profile', 'default') or 'default').strip() or 'default'
    payload = get_scoring_weights(profile=profile)
    active = get_active_scoring_profile()
    write_audit_log(
        actor=g.current_user['name'],
        action='policy_scoring_weights_viewed',
        resource_type='policy',
        details={
            'profile': profile,
            'active_profile': active.get('active_profile'),
            'role': g.current_user['role'],
            'source': payload.get('source', 'unknown'),
        },
    )
    return jsonify({'success': True, **payload, 'active_profile': active.get('active_profile', 'default')})


@governance_bp.route('/api/v1/policy/scoring-weights', methods=['PUT'])
@require_roles('admin')
def policy_scoring_weights_put_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    profile = str(data.get('profile') or 'default').strip() or 'default'
    weights = data.get('weights')
    change_reason = str(data.get('change_reason') or '').strip()

    if not change_reason:
        return jsonify({'success': False, 'error': 'change_reason is required'}), 400

    ok, error = validate_scoring_weights(weights)
    if not ok:
        return jsonify({'success': False, 'error': error}), 400

    before = get_scoring_weights(profile=profile)
    try:
        payload = upsert_scoring_weights(weights=weights, actor=g.current_user['name'], profile=profile)
    except ValueError as err:
        return jsonify({'success': False, 'error': str(err)}), 400

    previous_weights = dict(before.get('weights') or {})
    new_weights = dict(payload.get('weights') or {})
    diff = {}
    for key in sorted(set(previous_weights.keys()).union(new_weights.keys())):
        prev = float(previous_weights.get(key, 0.0) or 0.0)
        nxt = float(new_weights.get(key, 0.0) or 0.0)
        if prev != nxt:
            diff[key] = {'from': prev, 'to': nxt}

    write_audit_log(
        actor=g.current_user['name'],
        action='policy_scoring_weights_updated',
        resource_type='policy',
        details={
            'profile': profile,
            'change_reason': change_reason,
            'diff': diff,
            'weights': new_weights,
            'role': g.current_user['role'],
        },
    )
    return jsonify({'success': True, **payload})


@governance_bp.route('/api/v1/policy/scoring-profiles', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_scoring_profiles_list_secure():
    profiles = list_scoring_profiles()
    active = get_active_scoring_profile()
    write_audit_log(
        actor=g.current_user['name'],
        action='policy_scoring_profiles_viewed',
        resource_type='policy',
        details={'role': g.current_user['role'], 'active_profile': active.get('active_profile'), 'profile_count': len(profiles)},
    )
    return jsonify({'success': True, 'profiles': profiles, 'active': active})


@governance_bp.route('/api/v1/policy/scoring-profiles/activate', methods=['POST'])
@require_roles('admin')
def policy_scoring_profiles_activate_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    profile = str(data.get('profile') or '').strip()
    change_reason = str(data.get('change_reason') or '').strip()
    if not profile:
        return jsonify({'success': False, 'error': 'profile is required'}), 400
    if not change_reason:
        return jsonify({'success': False, 'error': 'change_reason is required'}), 400

    before = get_active_scoring_profile()
    try:
        after = activate_scoring_profile(profile=profile, actor=g.current_user['name'], change_reason=change_reason)
    except ValueError as err:
        return jsonify({'success': False, 'error': str(err)}), 400

    write_audit_log(
        actor=g.current_user['name'],
        action='policy_scoring_profile_activated',
        resource_type='policy',
        details={
            'change_reason': change_reason,
            'diff': {
                'active_profile': {'from': before.get('active_profile'), 'to': after.get('active_profile')},
                'previous_profile': {'from': before.get('previous_profile'), 'to': after.get('previous_profile')},
            },
            'role': g.current_user['role'],
        },
    )
    return jsonify({'success': True, 'active': after})


@governance_bp.route('/api/v1/policy/scoring-profiles/rollback', methods=['POST'])
@require_roles('admin')
def policy_scoring_profiles_rollback_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    change_reason = str(data.get('change_reason') or '').strip()
    if not change_reason:
        return jsonify({'success': False, 'error': 'change_reason is required'}), 400

    before = get_active_scoring_profile()
    try:
        after = rollback_scoring_profile(actor=g.current_user['name'], change_reason=change_reason)
    except ValueError as err:
        return jsonify({'success': False, 'error': str(err)}), 400

    write_audit_log(
        actor=g.current_user['name'],
        action='policy_scoring_profile_rolled_back',
        resource_type='policy',
        details={
            'change_reason': change_reason,
            'diff': {
                'active_profile': {'from': before.get('active_profile'), 'to': after.get('active_profile')},
                'previous_profile': {'from': before.get('previous_profile'), 'to': after.get('previous_profile')},
            },
            'role': g.current_user['role'],
        },
    )
    return jsonify({'success': True, 'active': after})



@governance_bp.route('/api/v1/policy/alignment-map', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_alignment_map_secure():
    try:
        limit = min(max(int(request.args.get('limit', 100) or 100), 1), 500)
    except ValueError:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    try:
        total_budget_lakh = float(request.args.get('total_budget_lakh', 5000) or 5000)
    except ValueError:
        return jsonify({'success': False, 'error': 'total_budget_lakh must be a number'}), 400

    state = str(request.args.get('state') or '').strip()
    payload = compute_demand_investment_alignment_map(limit=limit, total_budget_lakh=total_budget_lakh, state=state)

    write_audit_log(
        actor=g.current_user['name'],
        action='policy_alignment_map_viewed',
        resource_type='policy',
        details={'role': g.current_user['role'], 'limit': limit, 'state': state or None, 'total_budget_lakh': round(total_budget_lakh, 2), 'items': int((payload.get('meta') or {}).get('total_items') or 0)},
    )

    return jsonify({'success': True, **payload})


@governance_bp.route('/api/v1/policy/rag-brief', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_rag_brief_secure():
    query = str(request.args.get('query') or '').strip()
    district = str(request.args.get('district') or '').strip()
    try:
        limit = min(max(int(request.args.get('limit', 5) or 5), 1), 20)
    except ValueError:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    payload = generate_rag_policy_brief(query=query, district=district, limit=limit)

    write_audit_log(
        actor=g.current_user['name'],
        action='policy_rag_brief_viewed',
        resource_type='policy',
        details={'role': g.current_user['role'], 'query': query or None, 'district': district or None, 'citations': int(((payload.get('citation_chain') or {}).get('count') or 0))},
    )

    return jsonify({'success': True, **payload})


@governance_bp.route('/api/v1/policy/impact-brief', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_impact_brief_secure():
    from ..services.analyst_compat import rankings
    data=rankings(request.args)
    data['impact_metrics']={'funding_coverage_ratio':data['funded_count']/max(data['total_ranked'],1),'population_covered_estimate':None,'human_capital_npv_earnings_lakh':None}
    data['auto_brief']={'summary':'Draft scenario only. Benefits require catchment, baseline and engineering review.','human_capital_roi':data['outcomes'],
                        'recommendations':[{'project_id':p['project_id'],'title':p['title'],'review':'Verify engineering, catchment and scheme eligibility'} for p in data['items'] if p['funded_in_draft_plan']]}
    return jsonify(data)


@governance_bp.route('/api/v1/policy/human-capital-roi', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_human_capital_roi_secure():
    return jsonify(success=True,human_capital_roi={'status':'not_estimated','npv_earnings_uplift_lakh':None,'human_capital_irr':None,'reason':'No validated intervention cost, baseline, catchment or earnings cash-flow model.'})


@governance_bp.route('/api/v1/analyst/district-responsiveness', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def analyst_district_responsiveness_secure():
    from ..services.analyst_workbench import responsiveness,scope_from
    data=responsiveness(scope_from(request.args))
    return jsonify(success=True,league_table=data['items'],total_districts_evaluated=len(data['items']),metadata=data['metadata'],definitions=data['definitions'])


@governance_bp.route('/api/v1/policy/impact-decay', methods=['GET'])
@governance_bp.route('/api/v1/demand/decay-impact', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_impact_decay_secure():
    from ..services.analyst_workbench import outcomes,scope_from
    data=outcomes(scope_from(request.args),request.args.get('anchor_at'),request.args.get('post_days',28),request.args.get('project_id'),request.args.get('cluster_id'))
    return jsonify(success=True,**data)


def _parse_limit_and_budget(default_limit=25, default_budget=5000):
    try:
        limit = min(max(int(request.args.get('limit', default_limit)), 1), 500)
    except ValueError:
        return None, None, (jsonify({'success': False, 'error': 'limit must be an integer'}), 400)

    try:
        total_budget_lakh = float(request.args.get('total_budget_lakh', default_budget))
    except ValueError:
        return None, None, (jsonify({'success': False, 'error': 'total_budget_lakh must be a number'}), 400)

    if total_budget_lakh < 0:
        return None, None, (jsonify({'success': False, 'error': 'total_budget_lakh must be >= 0'}), 400)

    return limit, total_budget_lakh, None


@governance_bp.route('/api/v1/policy/decisions/draft', methods=['POST'])
@require_roles('admin', 'analyst')
def policy_decisions_create_draft_secure():
    limit, total_budget_lakh, err = _parse_limit_and_budget()
    if err:
        return err

    rankings = compute_priority_rankings_with_budget(limit=limit, total_budget_lakh=total_budget_lakh)
    items = rankings.get('items', [])
    if not items:
        return jsonify({'success': True, 'created_count': 0, 'decisions': []})


    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    actor = g.current_user['name']
    db = get_db()

    decisions = []
    for item in items:
        decision_id = f"PD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
        source = {
            'rank': item.get('rank'),
            'funded_in_draft_plan': bool(item.get('funded_in_draft_plan', False)),
            'cumulative_budget_used_lakh': item.get('cumulative_budget_used_lakh'),
        }
        db.execute(
            '''
            INSERT INTO policy_decisions (
                decision_id, district, priority_score, estimated_project_cost_lakh,
                total_budget_lakh, status, notes, source_json, created_by,
                approved_by, approved_at, rejected_by, rejected_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                decision_id,
                str(item.get('district', 'Unknown')),
                float(item.get('priority_score', 0.0)),
                float(item.get('estimated_project_cost_lakh', 0.0)),
                float(total_budget_lakh),
                'draft',
                None,
                json.dumps(source),
                actor,
                None,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        decisions.append(
            {
                'decision_id': decision_id,
                'district': str(item.get('district', 'Unknown')),
                'status': 'draft',
                'priority_score': float(item.get('priority_score', 0.0)),
                'estimated_project_cost_lakh': float(item.get('estimated_project_cost_lakh', 0.0)),
            }
        )

    db.commit()


    write_audit_log(
        actor=actor,
        action='policy_decisions_draft_created',
        resource_type='policy',
        details={
            'count': len(decisions),
            'limit': limit,
            'total_budget_lakh': round(total_budget_lakh, 2),
            'role': g.current_user['role'],
        },
    )

    return jsonify({'success': True, 'created_count': len(decisions), 'decisions': decisions})


@governance_bp.route('/api/v1/policy/decisions', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def list_policy_decisions_secure():
    status = (request.args.get('status') or '').strip().lower()
    limit = min(max(int(request.args.get('limit', 100)), 1), 500)

    db = get_db()
    params = []
    query = '''
        SELECT decision_id, district, priority_score, estimated_project_cost_lakh,
               total_budget_lakh, status, notes, created_by, approved_by,
               approved_at, rejected_by, rejected_at, created_at, updated_at
        FROM policy_decisions
    '''
    if status:
        if status not in {'draft', 'approved', 'rejected'}:
            return jsonify({'success': False, 'error': 'status must be one of draft, approved, rejected'}), 400
        query += ' WHERE status = ?'
        params.append(status)

    query += ' ORDER BY created_at DESC LIMIT ?'
    params.append(limit)
    rows = db.execute(query, tuple(params)).fetchall()

    write_audit_log(
        actor=g.current_user['name'],
        action='policy_decisions_viewed',
        resource_type='policy',
        details={'status': status or 'all', 'limit': limit, 'role': g.current_user['role']},
    )

    return jsonify({'success': True, 'items': [dict(r) for r in rows]})


@governance_bp.route('/api/v1/policy/decisions/<decision_id>/approve', methods=['POST'])
@require_roles('admin')
def approve_policy_decision_secure(decision_id):

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    actor = g.current_user['name']

    db = get_db()
    from ..services import analyst_workbench as work
    work.lock_policy_decisions()
    row = db.execute('SELECT decision_id, status, source_json, total_budget_lakh FROM policy_decisions WHERE decision_id = ?', (decision_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'decision not found'}), 404
    if row['status'] != 'draft':
        return jsonify({'success': False, 'error': 'only draft decisions can be approved'}), 400

    source=json.loads(row['source_json'] or '{}')
    from ..services.pilot import authorize_decision
    authorize_decision(source)
    if source.get('version') == work.VERSION:
        work.check_approval(source,float(row['total_budget_lakh']))

    db.execute(
        '''
        UPDATE policy_decisions
        SET status = ?, approved_by = ?, approved_at = ?, updated_at = ?, rejected_by = NULL, rejected_at = NULL
        WHERE decision_id = ?
        ''',
        ('approved', actor, now, now, decision_id),
    )
    db.commit()

    # Trigger Ex-Ante Matched-Control Auto-Selection Engine
    try:
        from ..services.causal_matching import auto_select_matched_controls
        dec_row = db.execute('SELECT decision_id, district, notes FROM policy_decisions WHERE decision_id = ?', (decision_id,)).fetchone()
        if dec_row and source.get('version') != work.VERSION:
            d_dict = dict(dec_row)
            auto_select_matched_controls(
                decision_id=decision_id,
                district=d_dict.get('district', 'Karur'),
                category='Infrastructure',
                project_name=d_dict.get('notes') or f"{d_dict.get('district')} Infrastructure Project"
            )
    except Exception as e:
        current_app.logger.warning(f"Ex-Ante Matching trigger error: {e}")

    write_audit_log(
        actor=actor,
        action='policy_decision_approved',
        resource_type='policy',
        resource_id=decision_id,
        details={'role': g.current_user['role']},
    )

    updated = db.execute(
        '''
        SELECT decision_id, district, priority_score, estimated_project_cost_lakh,
               total_budget_lakh, status, notes, created_by, approved_by,
               approved_at, rejected_by, rejected_at, created_at, updated_at
        FROM policy_decisions WHERE decision_id = ?
        ''',
        (decision_id,),
    ).fetchone()

    return jsonify({'success': True, 'decision': dict(updated)})


@governance_bp.route('/api/v1/policy/decisions/<decision_id>/reject', methods=['POST'])
@require_roles('admin')
def reject_policy_decision_secure(decision_id):
    payload = request.get_json(silent=True) or {}
    notes = (payload.get('notes') or '').strip() or None

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    actor = g.current_user['name']

    db = get_db()
    row = db.execute('SELECT decision_id, status FROM policy_decisions WHERE decision_id = ?', (decision_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'decision not found'}), 404
    if row['status'] != 'draft':
        return jsonify({'success': False, 'error': 'only draft decisions can be rejected'}), 400

    db.execute(
        '''
        UPDATE policy_decisions
        SET status = ?, notes = ?, rejected_by = ?, rejected_at = ?, updated_at = ?, approved_by = NULL, approved_at = NULL
        WHERE decision_id = ?
        ''',
        ('rejected', notes, actor, now, now, decision_id),
    )
    db.commit()


    write_audit_log(
        actor=actor,
        action='policy_decision_rejected',
        resource_type='policy',
        resource_id=decision_id,
        details={'role': g.current_user['role'], 'has_notes': notes is not None},
    )

    updated = db.execute(
        '''
        SELECT decision_id, district, priority_score, estimated_project_cost_lakh,
               total_budget_lakh, status, notes, created_by, approved_by,
               approved_at, rejected_by, rejected_at, created_at, updated_at
        FROM policy_decisions WHERE decision_id = ?
        ''',
        (decision_id,),
    ).fetchone()

    return jsonify({'success': True, 'decision': dict(updated)})


@governance_bp.route('/api/v1/governance/consent-ledger', methods=['GET'])
@governance_bp.route('/api/v1/dpdp/consent-ledger', methods=['GET'])
@require_roles('admin', 'auditor')
def governance_consent_ledger_secure():
    try:
        limit = int(request.args.get('limit', 100))
    except ValueError:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    request_id = str(request.args.get('request_id') or '').strip()
    payload = list_consent_events(limit=limit, request_id=request_id)
    write_audit_log(
        actor=g.current_user['name'],
        action='consent_ledger_viewed',
        resource_type='governance',
        details={'limit': payload['limit'], 'request_id': request_id, 'role': g.current_user['role']},
    )
    return jsonify({'success': True, 'ledger': payload})


@governance_bp.route('/api/v1/governance/dpdp-controls', methods=['GET'])
@require_roles('admin', 'auditor')
def governance_dpdp_controls_secure():
    payload = get_dpdp_controls()
    write_audit_log(
        actor=g.current_user['name'],
        action='dpdp_controls_viewed',
        resource_type='governance',
        details={'version': payload.get('version'), 'role': g.current_user['role']},
    )
    return jsonify({'success': True, 'dpdp': payload})


@governance_bp.route('/api/v1/governance/dpdp/evidence', methods=['GET'])
@require_roles('admin', 'auditor')
def governance_dpdp_evidence_secure():
    payload = get_dpdp_evidence()
    write_audit_log(
        actor=g.current_user['name'],
        action='dpdp_evidence_viewed',
        resource_type='governance',
        details={'version': payload.get('version')},
    )
    return jsonify({'success': True, 'evidence': payload})


@governance_bp.route('/api/v1/governance/dpdp/readiness', methods=['GET'])
@require_roles('admin', 'auditor')
def governance_dpdp_readiness_secure():
    payload = get_dpdp_readiness()
    write_audit_log(
        actor=g.current_user['name'],
        action='dpdp_readiness_viewed',
        resource_type='governance',
        details={'score': payload.get('score')},
    )
    return jsonify({'success': True, 'readiness': payload})


@governance_bp.route('/api/v1/governance/dpg-status', methods=['GET'])
@require_roles('admin', 'auditor')
def governance_dpg_status_secure():
    payload = get_dpg_status()
    write_audit_log(
        actor=g.current_user['name'],
        action='dpg_status_viewed',
        resource_type='governance',
        details={'registration_status': payload.get('registration_status')},
    )
    return jsonify({'success': True, 'dpg': payload})

@governance_bp.route('/api/public/transparency/summary', methods=['GET'])
def public_transparency_summary():
    allowed, retry_after = allow_public_request(f"transparency:{request.remote_addr or 'unknown'}", 60, 60)
    if not allowed:
        return jsonify({'success': False, 'error': 'Public transparency rate limit reached; retry shortly.'}), 429, {'Retry-After': str(retry_after)}
    db = get_db()
    min_group = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', 3)), 1)

    public_clause, public_params = _public_request_scope(request.args)
    total_row = db.execute(f'SELECT COUNT(*) AS c, MAX(c.created_at) AS latest_created_at FROM citizen_requests c WHERE {public_clause}', public_params).fetchone()
    district_rows = db.execute(
        f'''
        SELECT c.district, COUNT(*) AS request_count
        FROM citizen_requests c
        WHERE {public_clause}
        GROUP BY c.district
        ''', public_params
    ).fetchall()

    total_requests = int((total_row['c'] or 0) if total_row else 0)
    latest_created_at = (total_row['latest_created_at'] if total_row else None)

    published_groups = [row for row in district_rows if int(row['request_count']) >= min_group]
    published_request_count = sum(int(row['request_count']) for row in published_groups)
    dp = dp_metadata()
    if should_apply_dp():
        total_requests = privatize_count(total_requests, epsilon=float(dp.get('epsilon') or 0.75))
        published_request_count = privatize_count(published_request_count, epsilon=float(dp.get('epsilon') or 0.75))
    suppressed_groups = sum(1 for row in district_rows if int(row['request_count']) < min_group)

    return jsonify(
        {
            'success': True,
            'summary': {
                'total_requests': total_requests,
                'published_request_count': published_request_count,
                'published_district_groups': len(published_groups),
                'suppressed_district_groups': suppressed_groups,
                'anonymization': {
                    'min_group_size': min_group,
                    'strategy': 'k-anonymity by district/category group suppression',
                },
                'latest_created_at': latest_created_at,
                'differential_privacy': dp,
                'filters': {key: request.args.get(key, '') for key in ('pilot_id', 'state', 'district', 'category', 'urgency', 'language', 'channel') if request.args.get(key)},
                'notice': 'Counts are publication-safe aggregates. Small groups are suppressed and configured differential privacy may perturb counts.',
            },
        }
    )


def _public_request_scope(args):
    conditions = ['1=1']
    params = []
    for field in ('state', 'district', 'category', 'urgency', 'input_language', 'source_channel'):
        arg = 'language' if field == 'input_language' else ('channel' if field == 'source_channel' else field)
        value = str(args.get(arg) or '').strip()
        if value:
            conditions.append(f'c.{field}=?')
            params.append(value)
    pilot_id = str(args.get('pilot_id') or '').strip()
    if pilot_id:
        conditions.append('EXISTS (SELECT 1 FROM pilot_requests pr WHERE pr.request_id=c.request_id AND pr.pilot_id=?)')
        params.append(pilot_id)
    return ' AND '.join(conditions), params


@governance_bp.route('/api/public/transparency/priority-signals', methods=['GET'])
def public_transparency_priority_signals():
    """Publish only privacy-thresholded planning signals, never delivery claims."""
    allowed, retry_after = allow_public_request(f"transparency-signals:{request.remote_addr or 'unknown'}", 60, 60)
    if not allowed:
        return jsonify({'success': False, 'error': 'Public transparency rate limit reached; retry shortly.'}), 429, {'Retry-After': str(retry_after)}
    from ..services.analyst_workbench import candidates, provenance, scope_from

    minimum = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', 3)), 1)
    try:
        limit = min(max(int(request.args.get('limit', 12)), 1), 50)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400
    try:
        scope = scope_from(request.args)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    grouped = {}
    for item in candidates(scope):
        key = (item.get('state') or '', item.get('district') or '', item.get('category') or '')
        current = grouped.setdefault(key, {'state': key[0], 'district': key[1], 'category': key[2], 'request_count': 0, 'active_request_count': 0, 'screening_score': 0.0})
        current['request_count'] += int(item.get('reports') or 0)
        current['active_request_count'] += int(item.get('active_reports') or 0)
        current['screening_score'] = max(current['screening_score'], float(item.get('priority_score') or 0.0))

    ranked = [item for item in grouped.values() if item['request_count'] >= minimum]
    ranked.sort(key=lambda item: (-item['screening_score'], -item['request_count'], item['district'], item['category']))
    meta = provenance(scope)
    items = []
    for rank, item in enumerate(ranked[:limit], 1):
        published_count = privatize_count(item['request_count'], epsilon=float(dp_metadata().get('epsilon') or 0.75)) if should_apply_dp() else item['request_count']
        items.append({
            'rank': rank,
            'state': item['state'],
            'district': item['district'],
            'category': item['category'],
            'published_request_count': max(0, int(published_count)),
            'screening_score': round(item['screening_score'] * 100, 1),
            'signal_status': 'screening_only_unverified_reference',
            'delivery_status': 'not_published',
            'notice': 'This is a privacy-thresholded planning signal, not a funded project, delivery-progress measure, or impact claim.',
        })
    return jsonify(success=True, items=items, meta={
        'source': 'operational_database',
        'as_of': meta.get('as_of'),
        'data_mode': meta.get('data_mode'),
        'min_group_size': minimum,
        'differential_privacy': dp_metadata(),
        'notice': 'Priority signals are screening outputs. Engineering review, administrative approval and field evidence are required before implementation claims.',
    })


@governance_bp.route('/api/public/transparency/districts', methods=['GET'])
def public_transparency_districts():
    allowed, retry_after = allow_public_request(f"transparency-districts:{request.remote_addr or 'unknown'}", 60, 60)
    if not allowed:
        return jsonify({'success': False, 'error': 'Public transparency rate limit reached; retry shortly.'}), 429, {'Retry-After': str(retry_after)}
    try:
        limit = int(request.args.get('limit', 100))
    except ValueError:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    max_limit = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MAX_LIMIT', 200)), 1)
    limit = min(max(limit, 1), max_limit)
    min_group = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', 3)), 1)

    db = get_db()
    public_clause, public_params = _public_request_scope(request.args)
    rows = db.execute(
        f'''
        SELECT c.district, c.state, COUNT(*) AS request_count,
               SUM(CASE WHEN c.urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count,
               SUM(CASE WHEN c.urgency IN ('Emergency', 'Urgent') THEN 1 ELSE 0 END) AS high_priority_count,
               COUNT(DISTINCT c.category) AS category_diversity
        FROM citizen_requests c
        WHERE {public_clause}
        GROUP BY c.district, c.state
        HAVING COUNT(*) >= ?
        ORDER BY request_count DESC, emergency_count DESC, c.district ASC
        LIMIT ?
        ''',
        [*public_params, min_group, limit],
    ).fetchall()

    total_groups_row = db.execute(
        f'''
        SELECT COUNT(*) AS c
        FROM (SELECT c.district FROM citizen_requests c WHERE {public_clause} GROUP BY c.district) grouped
        ''', public_params
    ).fetchone()

    total_groups = int((total_groups_row['c'] or 0) if total_groups_row else 0)
    published_groups = len(rows)
    dp = dp_metadata()

    items = []
    dp = dp_metadata()
    for row in rows:
        items.append(
            {
                'district': row['district'],
                'state': row['state'],
                'request_count': privatize_count(int(row['request_count']), epsilon=float(dp.get('epsilon') or 0.75)) if should_apply_dp() else int(row['request_count']),
                'emergency_count': privatize_count(int(row['emergency_count'] or 0), epsilon=float(dp.get('epsilon') or 0.75)) if should_apply_dp() else int(row['emergency_count'] or 0),
                'high_priority_count': privatize_count(int(row['high_priority_count'] or 0), epsilon=float(dp.get('epsilon') or 0.75)) if should_apply_dp() else int(row['high_priority_count'] or 0),
                'category_diversity': int(row['category_diversity'] or 0),
            }
        )

    return jsonify(
        {
            'success': True,
            'items': items,
            'meta': {
                'limit': limit,
                'published_groups': published_groups,
                'suppressed_groups': max(total_groups - published_groups, 0),
                'min_group_size': min_group,
                'differential_privacy': dp,
            },
        }
    )


@governance_bp.route('/api/public/transparency/categories', methods=['GET'])
def public_transparency_categories():
    allowed, retry_after = allow_public_request(f"transparency-categories:{request.remote_addr or 'unknown'}", 60, 60)
    if not allowed:
        return jsonify({'success': False, 'error': 'Public transparency rate limit reached; retry shortly.'}), 429, {'Retry-After': str(retry_after)}
    min_group = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', 3)), 1)

    db = get_db()
    public_clause, public_params = _public_request_scope(request.args)
    rows = db.execute(
        f'''
        SELECT c.category, COUNT(*) AS request_count,
               SUM(CASE WHEN c.urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count,
               COUNT(DISTINCT CASE WHEN c.state IS NOT NULL AND TRIM(c.state) != '' AND LOWER(c.state) != 'unknown' AND c.district IS NOT NULL AND TRIM(c.district) != '' AND LOWER(c.district) != 'unknown' THEN c.district END) AS district_coverage
        FROM citizen_requests c
        WHERE {public_clause}
        GROUP BY c.category
        HAVING COUNT(*) >= ?
        ORDER BY request_count DESC, c.category ASC
        ''',
        [*public_params, min_group],
    ).fetchall()

    items = []
    dp = dp_metadata()
    for row in rows:
        items.append(
            {
                'category': row['category'],
                'request_count': privatize_count(int(row['request_count']), epsilon=float(dp.get('epsilon') or 0.75)) if should_apply_dp() else int(row['request_count']),
                'emergency_count': privatize_count(int(row['emergency_count'] or 0), epsilon=float(dp.get('epsilon') or 0.75)) if should_apply_dp() else int(row['emergency_count'] or 0),
                'district_coverage': int(row['district_coverage'] or 0),
            }
        )

    return jsonify(
        {
            'success': True,
            'items': items,
            'meta': {
                'published_groups': len(items),
                'min_group_size': min_group,
                'differential_privacy': dp,
            },
        }
    )


# ===== 4-LENS EINSTEIN PROJECTION & TRANSPARENCY LOG ENDPOINTS =====
from ..auth import parse_claims, require_claims
from ..log.chain import append_transparency_log, verify_chain_integrity
from ..panels.public import build_public_lens
from ..panels.analyst import build_analyst_lens
from ..panels.auditor import build_auditor_lens
from ..panels.admin import build_admin_lens
from ..panels.public import build_public_lens, resolve_pilot_metadata


@governance_bp.route('/api/v1/lens/cluster/<cluster_id>', methods=['GET'])
@governance_bp.route('/api/v1/lens/<role>/cluster/<cluster_id>', methods=['GET'])
def get_cluster_multi_lens(cluster_id, role=None):
    claims = parse_claims()
    effective_role = (role or claims['role']).lower().strip()

    meta = resolve_pilot_metadata(cluster_id)
    cluster_data = {
        'cluster_id': cluster_id,
        'state': meta['state'],
        'district': meta['district'],
        'size': meta['size'],
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    }

    if effective_role == 'public':
        lens_data = build_public_lens(cluster_data)
    elif effective_role == 'analyst':
        lens_data = build_analyst_lens(cluster_data)
    elif effective_role == 'auditor':
        lens_data = build_auditor_lens(cluster_data)
    elif effective_role == 'admin':
        lens_data = build_admin_lens(cluster_data)
    else:
        lens_data = build_public_lens(cluster_data)

    # Append hash-chained transparency log entry
    append_transparency_log(
        actor=claims['role'],
        action='cluster_lens_viewed',
        resource_type='cluster',
        details={'cluster_id': cluster_id, 'projected_role': effective_role}
    )

    return jsonify({
        'success': True,
        'cluster_id': cluster_id,
        'claims': claims,
        'projected_role': effective_role,
        'lens': lens_data,
        'available_lenses': ['public', 'analyst', 'auditor', 'admin']
    })


@governance_bp.route('/api/v1/transparency/verify-chain', methods=['GET'])
def verify_transparency_chain():
    allowed, retry_after = allow_public_request(f"transparency-verify:{request.remote_addr or 'unknown'}", 30, 60)
    if not allowed:
        return jsonify({'success': False, 'error': 'Transparency verification rate limit reached; retry shortly.'}), 429, {'Retry-After': str(retry_after)}
    result = verify_chain_integrity(limit=500)
    return jsonify({'success': True, **result})


@governance_bp.route('/api/v1/auditor/project-controls', methods=['GET'])
@require_roles('admin', 'auditor', 'analyst')
def get_project_controls_secure():
    district = (request.args.get('district') or '').strip()
    db = get_db()
    
    sql = 'SELECT * FROM project_controls'
    params = []
    if district:
        sql += ' WHERE district = ?'
        params.append(district)
    sql += ' ORDER BY id DESC LIMIT 50'
    
    rows = db.execute(sql, params).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d['control_geofence_ids'] = json.loads(d['control_geofence_ids'])
        except Exception:
            pass
        items.append(d)
        
    return jsonify({'success': True, 'count': len(items), 'project_controls': items})


@governance_bp.route('/api/v1/auditor/did-proof', methods=['GET'])
@require_roles('admin', 'auditor', 'analyst')
def get_auditor_did_proof_secure():
    from ..services.causal_matching import compute_did_causal_impact
    return jsonify(success=True,did_proof=compute_did_causal_impact(district=request.args.get('district'),decision_id=request.args.get('decision_id')))



@governance_bp.route('/api/v1/auditor/ai-telemetry', methods=['GET'])
@require_roles('admin', 'auditor', 'analyst')
def get_auditor_ai_telemetry():
    from ..services import auditor_workbench as audit, auditor_evaluation
    if request.args.get('mode')=='recompute':
        return jsonify(success=False,error='Submit a labelled evaluation job through /api/v2/auditor/evaluations'),410
    result=audit.summary(audit.scope_from(request.args))
    return jsonify(success=True,source='operational_database',generated_at=result['metadata']['as_of'],
        model_backend={'is_model_backed':False,'live':False,'backend':'measured_records','badge_label':'Recorded observations'},
        kpis={'model_drift_score':None,'bias_risk_index':None,'data_quality_score':None,'anomaly_count':None},
        slices=result['distributions'],evaluations=auditor_evaluation.results(audit.scope_from(request.args)),
        notice='Unvalidated heuristic scores retired. Use versioned evaluation results; no model calls during rendering.')

# ===== BHARAT FUTURES — CIVIC PREDICTION MARKET ENDPOINTS =====
from ..services.prediction_market import (
    list_prediction_market_projects,
    place_civic_stake,
    compute_hybrid_super_prediction,
    _DYNAMIC_PROJECTS,
)

@governance_bp.route('/api/v1/futures/markets', methods=['GET'])
def get_prediction_markets():
    return jsonify(success=True,projects=[],count=0,status='research_only',notice='Fixture probabilities retired from policy decisions. No model success probabilities asserted.')


@governance_bp.route('/api/v1/futures/stake', methods=['POST'])
def post_prediction_stake():
    return jsonify(success=False,error='Research staking is retired; use a documented expert review.'),410


@governance_bp.route('/api/v1/futures/super-prediction', methods=['GET'])
def get_super_prediction_detail():
    return jsonify(success=False,error='Uncalibrated fixture predictions are retired.'),410


# ===== RCT-AS-A-SERVICE — STEPPED-WEDGE ADAPTIVE POLICY LAB ENDPOINTS =====
from ..services.rct_experimentation import (
    list_rct_experiments,
    compute_late_causal_effect,
)

@governance_bp.route('/api/v1/rct/experiments', methods=['GET'])
def get_rct_experiments():
    return jsonify(success=True,count=0,experiments=[],status='design_only',notice='No live trial outcomes. Preregister a reviewed evaluation before collecting trial data.')


@governance_bp.route('/api/v1/rct/telemetry', methods=['GET'])
def get_rct_telemetry():
    return jsonify(success=True,status='not_evaluated',high_freq_telemetry={},late_causal_lift_pct=None,mab_status_label='No adaptive rollout decisions')


# ===== ANTI-CAPTURE SATELLITE + CITIZEN TRIANGULATION ENGINE ENDPOINTS =====
from ..services.anti_capture_triangulation import (
    list_anti_capture_projects,
    compute_triangulation_divergence,
)
