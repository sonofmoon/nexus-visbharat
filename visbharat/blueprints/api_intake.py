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
from . import api as _api_core

# Dynamic dispatch for functions patched on visbharat.blueprints.api by tests
def _run_speech_to_text(*args, **kwargs):
    return _api_core._run_speech_to_text(*args, **kwargs)

def _run_translation(*args, **kwargs):
    return _api_core._run_translation(*args, **kwargs)

def _run_classification(*args, **kwargs):
    return _api_core._run_classification(*args, **kwargs)

def _dispatch_live_bigquery_and_pubsub(*args, **kwargs):
    return _api_core._dispatch_live_bigquery_and_pubsub(*args, **kwargs)

def _apply_sla_policy_if_needed(*args, **kwargs):
    return _api_core._apply_sla_policy_if_needed(*args, **kwargs)

def assign_request_to_cluster(*args, **kwargs):
    return _api_core.assign_request_to_cluster(*args, **kwargs)

def get_db(*args, **kwargs):
    return _api_core.get_db(*args, **kwargs)

intake_bp = Blueprint('intake_bp', __name__)


@intake_bp.route('/api/submit', methods=['POST'])
def submit_complaint():
    data = request.get_json(silent=True) or request.form.to_dict()
    return _submit_complaint(data, (request.headers.get('X-Idempotency-Key') or '').strip())


def _submit_complaint(data, idempotency_key='', assistant_session_id=None):
    from ..services import citizen_review
    try: citizen_review.validate(data)
    except ValueError as error: return jsonify(success=False,error=str(error)),400
    data, scrub_meta = _scrub_ingress_payload(data, text_keys=['text', 'address', 'sender', 'phone'])
    if idempotency_key:
        existing = _get_existing_idempotent_request('/api/submit', idempotency_key)
        if existing:
            return jsonify({'success': True, 'request_id': existing['request_id'], 'classification': {'category': existing.get('category'), 'urgency': existing.get('urgency'), 'sentiment': existing.get('sentiment')}, 'routing': {'ward': existing.get('ward') or '', 'service': existing.get('service_type') or '', 'department': existing.get('routed_department') or ''}, 'sla': {'due_at': existing.get('sla_due_at'), 'breached_at': existing.get('sla_breached_at'), 'escalation_level': int(existing.get('sla_escalation_level') or 0)}, 'ai_mode': _get_ai_mode(), 'stt_mode': _get_stt_mode(), 'submission_evidence': _stored_submission_evidence(existing), 'idempotent_reused': True})

    repo = current_app.extensions['reference_repo']

    language = data.get('language', 'en')
    source = data.get('source', 'Web Form')
    district = data.get('district', 'Unknown')
    text = str(data.get('text', '')).strip()

    is_voice = data.get('is_voice') in (True, 'true', 'True', '1')
    if is_voice:
        try:
            stt = _run_speech_to_text(data, language)
            text = scrub_text(citizen_review.transcript(data, stt)).get('scrubbed')
        except Exception as err:
            return jsonify({'success': False, 'error': f'voice processing failed: {err}'}), 400
    else:
        stt = None

    if not text:
        return jsonify({'success': False, 'error': 'text is required'}), 400

    code_mix = normalize_code_mix(text, language)
    normalized_text = str(code_mix.get('normalized_text') or text).strip() or text

    fast_path = fast_path_emergency_triage(normalized_text, language)
    if fast_path:
        classification = fast_path
        if language == 'en':
            translation = {
                'translated_text': normalized_text,
                'source_language': 'en',
                'target_language': 'en',
                'model': 'VisBharat-FastPath-Emergency',
                'provider_mode': 'fast_path_deterministic',
            }
        else:
            translation = {
                'translated_text': f"EMERGENCY HAZARD ({fast_path.get('emergency_hazard')}): {normalized_text}",
                'source_language': language,
                'target_language': 'en',
                'model': 'VisBharat-FastPath-Emergency',
                'provider_mode': 'fast_path_deterministic',
            }
    else:
        try:
            translation = _run_translation(normalized_text, language)
            classification = _run_classification(normalized_text, language)
        except ValueError:
            current_app.logger.warning('Submission AI processing unavailable')
            return jsonify({'success': False, 'error': 'AI processing is currently unavailable. Your request was not saved. Please try again shortly.'}), 503
    citizen_review.outputs(data, translation, classification)
    if data.get('category'):
        classification['category'] = str(data['category']).strip()
    if data.get('urgency'):
        classification['urgency'] = str(data['urgency']).strip()

    lat, lng, state = _district_geo(repo, district)
    geo = resolve_geo_ward_context(
        district=district,
        text=normalized_text,
        ward=(data.get('ward') or ''),
        lat=data.get('lat'),
        lng=data.get('lng'),
        pin=(data.get('pin') or data.get('pincode') or ''),
        place_id=(data.get('place_id') or ''),
        address=(data.get('address') or ''),
        grid=current_app.config.get('MAP_WARD_GRID', {}),
        ward_polygons=current_app.config.get('WARD_POLYGONS', {}),
        svamitva_maps=current_app.config.get('SVAMITVA_VILLAGE_MAPS', {}),
        pin_index=current_app.config.get('PIN_GEOCODE_INDEX', {}),
        google_maps_client=current_app.extensions.get('google_maps_client'),
    )
    geo_err, geo_rule = _validate_geo_resolution_for_intake(district, geo)
    geo_override_used = None
    if geo_err:
        override_id = str(data.get('geo_override_id') or request.headers.get('X-Geo-Override-Id') or '').strip()
        if override_id:
            ok, rec = _consume_geo_override(
                override_id=override_id,
                endpoint='/api/submit',
                requested_district=str((geo_rule or {}).get('input_district') or district or '').strip(),
                evidence_district=str((geo_rule or {}).get('evidence_district') or '').strip(),
            )
            if ok:
                geo_override_used = {
                    'override_id': str(rec.get('override_id') or override_id),
                    'approved_by': str(rec.get('approved_by') or ''),
                    'approved_role': str(rec.get('approved_role') or ''),
                    'approved_at': str(rec.get('approved_at') or ''),
                    'reason': str(rec.get('reason') or ''),
                    'used_count': int(rec.get('used_count') or 0),
                    'max_uses': int(rec.get('max_uses') or 1),
                    'remaining_uses': max(int(rec.get('max_uses') or 1) - int(rec.get('used_count') or 0), 0),
                }
                write_audit_log(
                    actor='anonymous',
                    action='geo_validation_override_consumed',
                    resource_type='geo_validation_override',
                    resource_id=geo_override_used['override_id'],
                    details={'endpoint': '/api/submit', 'request_district': district, 'evidence_district': str((geo_rule or {}).get('evidence_district') or '')},
                )
                geo_err = None
                geo_rule = None
            else:
                return jsonify({'success': False, 'error': geo_err, 'geo_validation': geo_rule, 'geo_override': {'override_id': override_id, 'status': 'rejected', **(rec or {})}, 'geo_context': geo}), 400
        if geo_err:
            return jsonify({'success': False, 'error': geo_err, 'geo_validation': geo_rule, 'geo_context': geo}), 400
    map_ward = geo.get('ward') or ''
    ward_source = geo.get('ward_source') or 'none'
    if geo.get('lat') is not None and geo.get('lng') is not None:
        lat = float(geo.get('lat'))
        lng = float(geo.get('lng'))
    route = resolve_ward_department_route(
        category=classification['category'],
        district=district,
        state=state,
        text=text,
        ward=map_ward,
        matrix=current_app.config.get('WARD_ROUTING_MATRIX', {}),
        category_to_service=current_app.config.get('CATEGORY_TO_SERVICE', {}),
        service_to_department=current_app.config.get('SERVICE_TO_DEPARTMENT', {}),
    )
    sla_policy = resolve_sla_policy(
        urgency=classification['urgency'],
        category=classification['category'],
        channel=source,
        routed_department=route['department'],
        urgency_to_hours=current_app.config.get('SLA_URGENCY_HOURS', {}),
        sla_rules=current_app.config.get('SLA_RULES', {}),
        escalation_targets=current_app.config.get('SLA_ESCALATION_TARGETS', {}),
    )
    sla_due_at = sla_policy['due_at']
    request_id = _generate_request_id()

    ai_metadata = {
        'is_synthetic': bool(current_app.config.get('DEMO_MODE')),
        'data_mode': 'showcase_submission' if current_app.config.get('DEMO_MODE') else 'unverified_submission',
        'mode': _get_ai_mode(),
        'stt_mode': _get_stt_mode(),
        'stt': stt,
        'translation': translation,
        'code_mix_normalization': code_mix,
        'classification': classification,
        'model_trace': _extract_model_trace(classification=classification, translation=translation, stt=stt),
        'urgency_source': data.get('urgency_source', 'ai'),
        'routing': route,
        'ward_source': ward_source,
        'geo_override': geo_override_used or {},
        'geo_context': {
            'pin': geo.get('pin') or '',
            'village': geo.get('village') or '',
            'resolved_district': geo.get('district') or district,
            'geo_confidence': float(geo.get('geo_confidence') or 0.0),
            'district_validation': geo.get('district_validation') or {},
            'geo_telemetry': geo.get('geo_telemetry') or {},
        },
        'sla_policy': sla_policy,
        'pii_scrub': scrub_meta,
    }

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    is_emergency = (classification.get('urgency') == 'Emergency')
    emergency_status = 'dispatched' if is_emergency else 'none'
    if is_emergency:
        dept = route.get('department') or 'District Disaster Management Authority (DDMA)'
        dispatched_to = f"{dept} & District Disaster Management Authority (DDMA)" if 'Disaster' not in dept else dept
    else:
        dispatched_to = None
    dispatched_at = now if is_emergency else None
    # Persist a hash only; the raw capability is for the response-desk
    # connector and must not be embedded in citizen evidence or responses.
    ack_token = secrets.token_urlsafe(32) if is_emergency else None
    ack_token_hash = hash_api_token(ack_token) if ack_token else None
    fallback_target = 'Collectorate 24/7 Crisis Hotline & District Magistrate Desk' if is_emergency else None

    if is_emergency:
        ai_metadata['emergency_dispatch'] = {
            'status': emergency_status,
            'dispatched_to': dispatched_to,
            'dispatched_at': dispatched_at,
            'acknowledgement_token_issued': bool(ack_token),
            'fallback_target': fallback_target,
            'requires_acknowledgement': True,
        }

    db = get_db()
    if assistant_session_id:
        claimed = db.execute('UPDATE assistant_sessions SET request_id=? WHERE session_id=? AND request_id IS NULL RETURNING session_id',
                             (request_id, assistant_session_id)).fetchone()
        if not claimed:
            existing = db.execute('SELECT request_id FROM assistant_sessions WHERE session_id=?', (assistant_session_id,)).fetchone()
            return jsonify(success=True, request_id=existing['request_id'], idempotent_reused=True)
    db.execute(
        '''
        INSERT INTO citizen_requests (
            request_id, source_channel, input_language, district, state, lat, lng,
            original_text, translated_text, category, urgency, sentiment, status,
            submitted_by, ward, service_type, routed_department, sla_due_at, sla_breached_at, sla_escalation_level,
            emergency_dispatch_status, dispatched_to, dispatched_at, acknowledgement_token, fallback_target,
            ai_metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            request_id,
            source,
            language,
            district,
            state,
            lat,
            lng,
            text,
            translation['translated_text'],
            classification['category'],
            classification['urgency'],
            classification['sentiment'],
            'New',
            'anonymous',
            route['ward'],
            route['service'],
            route['department'],
            sla_due_at,
            None,
            0,
            emergency_status,
            dispatched_to,
            dispatched_at,
            ack_token_hash,
            fallback_target,
            json.dumps(ai_metadata),
            now,
        ),
    )
    try:
        consent_event = _record_ingest_consent(
            request_id,
            data,
            channel='nvb_assistant' if assistant_session_id else 'web',
            language=language,
            actor='anonymous',
            commit=False,
        )
        if assistant_session_id:
            write_audit_log('anonymous','assistant_request_saved','citizen_request',request_id,
                            {'language':language,'district':district,'consent_event':consent_event['event_id']},commit=False)
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Unable to persist citizen submission and consent record')
        return jsonify({'success': False, 'error': 'Your request could not be saved. Please try again.'}), 503
    _save_idempotency_key('/api/submit', idempotency_key, request_id)

    ingest_payload = {
        'request_id': request_id,
        'district': district,
        'state': state,
        'urgency': classification.get('urgency'),
        'category': classification.get('category'),
        'source_channel': source,
        'created_at': now,
        'input_language': language,
        'original_text': text,
        'translated_text': translation.get('translated_text'),
        'sentiment': classification.get('sentiment'),
        'status': 'New',
        'lat': lat,
        'lng': lng,
    }
    ingest_telemetry = _dispatch_live_bigquery_and_pubsub(ingest_payload)
    submission_evidence = _record_submission_evidence(
        request_id, ai_metadata, '/api/submit', now, ingest_telemetry
    )

    cluster = assign_request_to_cluster(
        request_id=request_id,
        state=state,
        category=classification.get('category'),
        text=(translation.get('translated_text') or text),
    )

    write_audit_log(
        actor='anonymous',
        action='citizen_request_submitted',
        resource_type='citizen_request',
        resource_id=request_id,
        details={
            'district': district,
            'source': source,
            'category': classification['category'],
            'ai_mode': _get_ai_mode(),
            'stt_mode': _get_stt_mode(),
            'is_voice': is_voice,
            'model_trace': _extract_model_trace(classification=classification, translation=translation, stt=stt),
            'geo_override': geo_override_used or {},
            'cloud_ingestion': ingest_telemetry,
            'submission_evidence': submission_evidence,
        },
    )

    return jsonify(
        {
            'success': True,
            'request_id': request_id,
            'classification': classification,
            'routing': route,
            'cluster': cluster,
            'consent': consent_event,
            'ward_source': ward_source,
            'geo_confidence': float(geo.get('geo_confidence') or 0.0),
            'district_validation': geo.get('district_validation') or {},
            'geo_telemetry': geo.get('geo_telemetry') or {},
            'geo_override': geo_override_used or {},
            'sla_policy': sla_policy,
            'sla': {'due_at': sla_due_at, 'breached_at': None, 'escalation_level': 0, 'policy_mode': sla_policy.get('policy_mode'), 'tier1_target': sla_policy.get('tier1_target', {}), 'tier2_delay_hours': sla_policy.get('tier2_delay_hours'), 'tier2_target': sla_policy.get('tier2_target', {})},
            'ai_mode': _get_ai_mode(),
            'stt_mode': _get_stt_mode(),
            'model_trace': _extract_model_trace(classification=classification, translation=translation, stt=stt),
            'cloud_ingestion': ingest_telemetry,
            'submission_evidence': submission_evidence,
            'emergency_dispatch': ai_metadata.get('emergency_dispatch'),
        }
    )


def _validate_attachment_manifest_payload(data):
    request_id = str((data or {}).get('request_id', '')).strip()
    if not request_id:
        return None, None, ['request_id is required']

    manifest = (data or {}).get('attachment_manifest')
    if not isinstance(manifest, dict):
        return request_id, None, ['attachment_manifest object is required']

    issues = []
    normalized = {'photos': [], 'files': [], 'camera_captures': [], 'google_ready': bool(manifest.get('google_ready'))}

    for key in ('photos', 'files', 'camera_captures'):
        entries = manifest.get(key, [])
        if not isinstance(entries, list):
            issues.append(f'attachment_manifest.{key} must be a list')
            entries = []

        if len(entries) > 20:
            issues.append(f'attachment_manifest.{key} exceeds max count of 20')

        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                issues.append(f'attachment_manifest.{key}[{idx}] must be an object')
                continue

            name = str(entry.get('name', '')).strip()
            mime_type = str(entry.get('type', '')).strip()
            source = str(entry.get('source', '')).strip()
            if not name:
                issues.append(f'attachment_manifest.{key}[{idx}].name is required')
            if not mime_type:
                issues.append(f'attachment_manifest.{key}[{idx}].type is required')
            if not source:
                issues.append(f'attachment_manifest.{key}[{idx}].source is required')

            normalized[key].append(
                {
                    'name': name,
                    'type': mime_type,
                    'source': source,
                }
            )

    camera_captures_base64 = (data or {}).get('camera_captures_base64', [])
    if camera_captures_base64 is None:
        camera_captures_base64 = []
    if not isinstance(camera_captures_base64, list):
        issues.append('camera_captures_base64 must be a list when provided')
        camera_captures_base64 = []

    if len(camera_captures_base64) != len(normalized['camera_captures']):
        issues.append('camera_captures_base64 count must match attachment_manifest.camera_captures count')

    if len(camera_captures_base64) > 20:
        issues.append('camera_captures_base64 exceeds max count of 20')

    for idx, capture in enumerate(camera_captures_base64):
        if not isinstance(capture, str) or not capture.strip():
            issues.append(f'camera_captures_base64[{idx}] must be a non-empty base64 string')
            continue
        if len(capture) > 8_000_000:
            issues.append(f'camera_captures_base64[{idx}] exceeds max size placeholder')

    return request_id, normalized, issues


@intake_bp.route('/api/attachments/ingest-stub', methods=['POST'])
def ingest_attachment_manifest_stub():
    data = request.get_json(silent=True) or request.form.to_dict()
    request_id, normalized_manifest, issues = _validate_attachment_manifest_payload(data)

    if not request_id:
        return jsonify({'success': False, 'validated': False, 'issues': issues}), 400

    photos_count = len((normalized_manifest or {}).get('photos', []))
    files_count = len((normalized_manifest or {}).get('files', []))
    camera_count = len((normalized_manifest or {}).get('camera_captures', []))
    accepted_count = photos_count + files_count + camera_count

    validated = len(issues) == 0
    response = {
        'success': validated,
        'validated': validated,
        'request_id': request_id,
        'accepted_count': accepted_count,
        'rejected_count': len(issues),
        'issues': issues,
    }

    current_app.logger.info(
        'Attachment ingest stub processed request_id=%s validated=%s accepted=%s rejected=%s',
        request_id,
        validated,
        accepted_count,
        len(issues),
    )

    write_audit_log(
        actor='anonymous',
        action='attachment_manifest_ingested_stub',
        resource_type='attachment_manifest',
        resource_id=request_id,
        details={
            'validated': validated,
            'accepted_count': accepted_count,
            'rejected_count': len(issues),
            'photos_count': photos_count,
            'files_count': files_count,
            'camera_count': camera_count,
            'google_ready': bool((normalized_manifest or {}).get('google_ready', False)),
            'issues': issues,
        },
    )

    return jsonify(response), (200 if validated else 400)



def _get_existing_idempotent_request(endpoint, idempotency_key):
    if not idempotency_key:
        return None
    row = get_db().execute(
        '''
        SELECT request_id
        FROM request_idempotency_keys
        WHERE endpoint = ? AND idempotency_key = ?
        ORDER BY id DESC
        LIMIT 1
        ''',
        (endpoint, idempotency_key),
    ).fetchone()
    if not row:
        return None
    request_row = get_db().execute(
        '''
        SELECT request_id, source_channel, input_language, district, state, category, urgency, sentiment, status, original_text, ward, service_type, routed_department, sla_due_at, sla_breached_at, sla_escalation_level, ai_metadata_json
        FROM citizen_requests
        WHERE request_id = ?
        LIMIT 1
        ''',
        (row['request_id'],),
    ).fetchone()
    return dict(request_row) if request_row else None


def _save_idempotency_key(endpoint, idempotency_key, request_id):
    key = (idempotency_key or '').strip()
    if not key:
        return
    db = get_db()

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    try:
        db.execute(
            'INSERT INTO request_idempotency_keys (endpoint, idempotency_key, request_id, created_at) VALUES (?, ?, ?, ?)',
            (endpoint, key, request_id, now),
        )
        db.commit()
    except Exception:
        pass



def _parse_iso8601(value):
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00')).astimezone(timezone.utc)
    except Exception:
        return None


def _emit_sla_notification(request_id, stage, actor, channel, target, sla_due_at, details=None):
    payload = dispatch_sla_notification(
        request_id=request_id,
        stage=stage,
        actor=actor,
        source_channel=channel,
        target=target,
        sla_due_at=sla_due_at,
        details=details,
    )
    write_audit_log(
        actor=actor,
        action='sla_notification_hook_emitted',
        resource_type='citizen_request',
        resource_id=request_id,
        details=payload,
    )
    return payload


def _apply_sla_policy_if_needed_impl(request_id, actor='sla-engine'):
    db = get_db()
    row = db.execute(
        '''
        SELECT request_id, status, source_channel, category, routed_department, sla_due_at, sla_breached_at, sla_escalation_level
        FROM citizen_requests
        WHERE request_id = ?
        LIMIT 1
        ''',
        (request_id,),
    ).fetchone()
    if not row:
        return None

    status = str(row['status'] or '')
    if status in {'Closed', 'Resolved'}:
        return dict(row)

    due_at = _parse_iso8601(row['sla_due_at'])
    if not due_at:
        return dict(row)

    sla_policy = resolve_sla_policy(
        urgency='Routine',
        category=str(row['category'] or 'Other'),
        channel=str(row['source_channel'] or 'Unknown'),
        routed_department=str(row['routed_department'] or 'District Grievance Cell'),
        urgency_to_hours=current_app.config.get('SLA_URGENCY_HOURS', {}),
        sla_rules=current_app.config.get('SLA_RULES', {}),
        escalation_targets=current_app.config.get('SLA_ESCALATION_TARGETS', {}),
        tier2_default_delay_hours=int(current_app.config.get('SLA_TIER2_DELAY_HOURS', 24) or 24),
    )
    tier1_target = sla_policy.get('tier1_target', {})
    tier2_target = sla_policy.get('tier2_target', {})
    tier2_delay_hours = int(sla_policy.get('tier2_delay_hours', current_app.config.get('SLA_TIER2_DELAY_HOURS', 24)) or 24)

    now = datetime.now(timezone.utc)
    if now < due_at:
        return dict(row)

    breached_at_raw = str(row['sla_breached_at'] or '').strip()
    breached_at_dt = _parse_iso8601(breached_at_raw) if breached_at_raw else None
    escalation_level = int(row['sla_escalation_level'] or 0)

    now_iso = now.isoformat().replace('+00:00', 'Z')

    if not breached_at_raw:
        breached_at_raw = now_iso
        breached_at_dt = now
        db.execute(
            "UPDATE citizen_requests SET sla_breached_at = ? WHERE request_id = ?",
            (breached_at_raw, request_id),
        )
        db.commit()
        _append_lifecycle_event(
            request_id,
            event_type='sla_breached',
            actor=actor,
            channel=str(row['source_channel'] or ''),
            from_status=status,
            to_status=status,
            reason='SLA deadline exceeded',
            sla_due_at=str(row['sla_due_at'] or ''),
            details={'engine': 'sla_v1.3', 'tier1_target': tier1_target, 'tier2_target': tier2_target},
        )

    if escalation_level < 1:
        db.execute(
            "UPDATE citizen_requests SET sla_escalation_level = ?, status = ? WHERE request_id = ?",
            (1, 'Escalated', request_id),
        )
        db.commit()

        notif1 = _emit_sla_notification(
            request_id,
            stage='tier1',
            actor=actor,
            channel=str(row['source_channel'] or ''),
            target=tier1_target,
            sla_due_at=str(row['sla_due_at'] or ''),
            details={'engine': 'sla_v1.3'},
        )

        _append_lifecycle_event(
            request_id,
            event_type='sla_escalated_tier1',
            actor=actor,
            channel=str(row['source_channel'] or ''),
            from_status=status,
            to_status='Escalated',
            reason='Auto escalation tier 1 after SLA breach',
            sla_due_at=str(row['sla_due_at'] or ''),
            details={'engine': 'sla_v1.3', 'escalation_level': 1, 'tier1_target': tier1_target, 'notification': notif1},
        )
        write_audit_log(
            actor=actor,
            action='request_sla_tier1_escalated',
            resource_type='citizen_request',
            resource_id=request_id,
            details={'from_status': status, 'to_status': 'Escalated', 'sla_due_at': str(row['sla_due_at'] or ''), 'tier1_target': tier1_target, 'notification': notif1},
        )
        escalation_level = 1

    tier2_ready = False
    if breached_at_dt is not None:
        tier2_ready = now >= (breached_at_dt + timedelta(hours=max(tier2_delay_hours, 1)))

    if escalation_level < 2 and tier2_ready:
        db.execute(
            "UPDATE citizen_requests SET sla_escalation_level = ?, status = ? WHERE request_id = ?",
            (2, 'Escalated', request_id),
        )
        db.commit()

        notif2 = _emit_sla_notification(
            request_id,
            stage='tier2',
            actor=actor,
            channel=str(row['source_channel'] or ''),
            target=tier2_target,
            sla_due_at=str(row['sla_due_at'] or ''),
            details={'engine': 'sla_v1.3', 'tier2_delay_hours': tier2_delay_hours},
        )

        _append_lifecycle_event(
            request_id,
            event_type='sla_escalated_tier2',
            actor=actor,
            channel=str(row['source_channel'] or ''),
            from_status='Escalated',
            to_status='Escalated',
            reason='Auto escalation tier 2 after post-breach delay',
            sla_due_at=str(row['sla_due_at'] or ''),
            details={'engine': 'sla_v1.3', 'escalation_level': 2, 'tier2_target': tier2_target, 'tier2_delay_hours': tier2_delay_hours, 'notification': notif2},
        )
        write_audit_log(
            actor=actor,
            action='request_sla_tier2_escalated',
            resource_type='citizen_request',
            resource_id=request_id,
            details={'sla_due_at': str(row['sla_due_at'] or ''), 'tier2_target': tier2_target, 'tier2_delay_hours': tier2_delay_hours, 'notification': notif2},
        )

    final_row = db.execute(
        '''
        SELECT request_id, status, source_channel, category, routed_department, sla_due_at, sla_breached_at, sla_escalation_level
        FROM citizen_requests
        WHERE request_id = ?
        LIMIT 1
        ''',
        (request_id,),
    ).fetchone()
    return dict(final_row) if final_row else None
def _append_lifecycle_event(request_id, event_type, actor='system', channel=None, from_status=None, to_status=None, reason=None, sla_due_at=None, details=None):
    db = get_db()
    db.execute(
        '''
        INSERT INTO request_lifecycle_events (
            request_id, event_type, from_status, to_status, actor, channel, reason, sla_due_at, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            request_id,
            event_type,
            from_status,
            to_status,
            actor,
            channel,
            reason,
            sla_due_at,
            json.dumps(details or {}),
            datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        ),
    )
    db.commit()


def _fetch_request_timeline(request_id):
    db = get_db()
    request_row = db.execute(
        '''
        SELECT request_id, source_channel, district, state, category, urgency, status, ward, service_type, routed_department, sla_due_at, sla_breached_at, sla_escalation_level, created_at
        FROM citizen_requests
        WHERE request_id = ?
        LIMIT 1
        ''',
        (request_id,),
    ).fetchone()
    if not request_row:
        return None, []

    events = db.execute(
        '''
        SELECT event_type, from_status, to_status, actor, channel, reason, sla_due_at, details_json, created_at
        FROM request_lifecycle_events
        WHERE request_id = ?
        ORDER BY created_at ASC, id ASC
        ''',
        (request_id,),
    ).fetchall()
    timeline = []
    for event in events:
        entry = dict(event)
        try:
            entry['details'] = json.loads(entry.get('details_json') or '{}')
        except Exception:
            entry['details'] = {}
        entry.pop('details_json', None)
        timeline.append(entry)

    if not timeline:
        timeline.append(
            {
                'event_type': 'submitted',
                'from_status': None,
                'to_status': request_row['status'],
                'actor': 'system',
                'channel': request_row['source_channel'],
                'reason': None,
                'sla_due_at': request_row['sla_due_at'],
                'details': {},
                'created_at': request_row['created_at'],
            }
        )

    return dict(request_row), timeline


@intake_bp.route('/api/v1/requests/<request_id>/track', methods=['GET'])
@intake_bp.route('/api/requests/<request_id>/track', methods=['GET'])
def track_request_progress(request_id):
    req_data, timeline = _fetch_request_timeline(request_id)
    if not req_data:
        return jsonify({'success': False, 'error': f'Request Ticket ID "{request_id}" not found.'}), 404
    
    raw_status = str(req_data.get('status') or 'Pending').strip()
    status_lower = raw_status.lower()
    
    stage_map = {
        'pending': 1,
        'ingested': 1,
        'new': 1,
        'submitted': 1,
        'triaged': 2,
        'acknowledged': 2,
        'prioritized': 3,
        'capex allocated': 3,
        'in progress': 4,
        'in_progress': 4,
        'work in progress': 4,
        'escalated': 4,
        'reopened': 4,
        'resolved': 5,
        'closed': 5,
    }
    stage_idx = stage_map.get(status_lower, 2)
    
    stages = [
        {'step': 1, 'name': 'Ingested & Verified', 'desc': f"Submitted via {req_data.get('source_channel', 'Omni-Channel')}"},
        {'step': 2, 'name': 'Triaged & Priority Scored', 'desc': 'SPS v2.4 Econometric Score Computed'},
        {'step': 3, 'name': 'Budget & Scheme Fit', 'desc': 'PMGSY / 15th FC Capex Allocated'},
        {'step': 4, 'name': 'Work Execution In Progress', 'desc': f"Routed to {req_data.get('routed_department') or 'Public Works Dept'}"},
        {'step': 5, 'name': 'Causal Resolution', 'desc': 'Citizen Impact Receipt Issued'},
    ]

    return jsonify({
        'success': True,
        'request_id': request_id,
        'district': req_data.get('district'),
        'state': req_data.get('state'),
        'category': req_data.get('category'),
        'urgency': req_data.get('urgency'),
        'status': raw_status,
        'current_stage_index': stage_idx,
        'stages': stages,
        'routed_department': req_data.get('routed_department') or 'Public Works Department',
        'sla_due_at': req_data.get('sla_due_at'),
        'created_at': req_data.get('created_at'),
        'timeline_events': timeline
    })


@intake_bp.route('/api/transcribe-voice', methods=['POST'])
def transcribe_voice_endpoint():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    language = (data.get('language') or 'en').strip()
    audio_b64 = (data.get('audio_base64') or '').strip()
    mime_type = (data.get('audio_mime_type') or 'audio/webm').strip()

    if data.get('assistant') and (language not in {'en','ta','te'} or len(audio_b64)>4*1024*1024):
        return jsonify(success=False, error='Unsupported language or audio exceeds 3 MiB'),400

    if not audio_b64:
        return jsonify({'success': False, 'error': 'No audio data provided'}), 400

    try:
        audio_bytes = base64.b64decode(audio_b64, validate=True)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid base64 audio string'}), 400

    try:
        stt = _run_speech_to_text(data, language, audio_bytes=audio_bytes, mime_type=mime_type)
        if data.get('assistant') and not _is_live_model_result(stt):
            return jsonify(success=False, error='Live transcription unavailable; type your message', fallback='text'),503
        transcript = (stt.get('transcript') or '').strip()
        confidence = stt.get('confidence')
        provider = stt.get('provider', 'stt')
        model = stt.get('model', 'SpeechToText')
    except Exception as err:
        return jsonify({'success': False, 'error': f'Transcription failed: {err}'}), 400

    if not transcript:
        return jsonify({'success': False, 'error': 'Speech-to-text returned an empty transcript'}), 400

    return jsonify({
        'success': True,
        'transcript': transcript,
        'confidence': confidence,
        'provider': provider,
        'model': model,
        'language': language,
        'stt_mode': stt.get('provider_mode', 'unverified'),
        'provider_evidence': stt.get('provider_evidence')
    })


@intake_bp.route('/api/submit-voice', methods=['POST'])
def submit_voice_complaint():

    data = request.get_json(silent=True) or request.form.to_dict()
    from ..services import citizen_review
    try: citizen_review.validate(data)
    except ValueError as error: return jsonify(success=False,error=str(error)),400
    data, scrub_meta = _scrub_ingress_payload(data, text_keys=['address', 'sender', 'phone'])
    idempotency_key = (request.headers.get('X-Idempotency-Key') or '').strip()
    if idempotency_key:
        existing = _get_existing_idempotent_request('/api/submit-voice', idempotency_key)
        if existing:
            return jsonify({'success': True, 'request_id': existing['request_id'], 'transcript': existing.get('original_text', ''), 'classification': {'category': existing.get('category'), 'urgency': existing.get('urgency'), 'sentiment': existing.get('sentiment')}, 'routing': {'ward': existing.get('ward') or '', 'service': existing.get('service_type') or '', 'department': existing.get('routed_department') or ''}, 'sla': {'due_at': existing.get('sla_due_at'), 'breached_at': existing.get('sla_breached_at'), 'escalation_level': int(existing.get('sla_escalation_level') or 0)}, 'ai_mode': _get_ai_mode(), 'stt_mode': _get_stt_mode(), 'submission_evidence': _stored_submission_evidence(existing), 'idempotent_reused': True})
    repo = current_app.extensions['reference_repo']

    error, validated = _validate_voice_submit_payload(data, repo)
    if error:
        return jsonify({'success': False, 'error': error}), 400

    language = validated['language']
    district = validated['district']
    source = validated['source']
    audio_bytes = validated['audio_bytes']
    mime_type = validated['mime_type']

    try:
        stt = _run_speech_to_text(data, language, audio_bytes=audio_bytes, mime_type=mime_type)
        text = scrub_text(citizen_review.transcript(data, stt)).get('scrubbed')
    except Exception as err:
        return jsonify({'success': False, 'error': f'voice processing failed: {err}'}), 400

    if not text:
        return jsonify({'success': False, 'error': 'speech-to-text returned empty transcript'}), 400

    code_mix = normalize_code_mix(text, language)
    normalized_text = str(code_mix.get('normalized_text') or text).strip() or text

    try:
        translation = _run_translation(normalized_text, language)
        classification = _run_classification(normalized_text, language)
    except ValueError:
        current_app.logger.warning('Voice submission AI processing unavailable')
        return jsonify({'success': False, 'error': 'AI processing is currently unavailable. Your request was not saved. Please try again shortly.'}), 503
    citizen_review.outputs(data, translation, classification)
    if data.get('category'):
        classification['category'] = str(data['category']).strip()
    if data.get('urgency'):
        classification['urgency'] = str(data['urgency']).strip()

    lat, lng, state = _district_geo(repo, district)
    geo = resolve_geo_ward_context(
        district=district,
        text=normalized_text,
        ward=(data.get('ward') or ''),
        lat=data.get('lat'),
        lng=data.get('lng'),
        pin=(data.get('pin') or data.get('pincode') or ''),
        place_id=(data.get('place_id') or ''),
        address=(data.get('address') or ''),
        grid=current_app.config.get('MAP_WARD_GRID', {}),
        ward_polygons=current_app.config.get('WARD_POLYGONS', {}),
        svamitva_maps=current_app.config.get('SVAMITVA_VILLAGE_MAPS', {}),
        pin_index=current_app.config.get('PIN_GEOCODE_INDEX', {}),
        google_maps_client=current_app.extensions.get('google_maps_client'),
    )
    geo_err, geo_rule = _validate_geo_resolution_for_intake(district, geo)
    geo_override_used = None
    if geo_err:
        override_id = str(data.get('geo_override_id') or request.headers.get('X-Geo-Override-Id') or '').strip()
        if override_id:
            ok, rec = _consume_geo_override(
                override_id=override_id,
                endpoint='/api/submit-voice',
                requested_district=str((geo_rule or {}).get('input_district') or district or '').strip(),
                evidence_district=str((geo_rule or {}).get('evidence_district') or '').strip(),
            )
            if ok:
                geo_override_used = {
                    'override_id': str(rec.get('override_id') or override_id),
                    'approved_by': str(rec.get('approved_by') or ''),
                    'approved_role': str(rec.get('approved_role') or ''),
                    'approved_at': str(rec.get('approved_at') or ''),
                    'reason': str(rec.get('reason') or ''),
                    'used_count': int(rec.get('used_count') or 0),
                    'max_uses': int(rec.get('max_uses') or 1),
                    'remaining_uses': max(int(rec.get('max_uses') or 1) - int(rec.get('used_count') or 0), 0),
                }
                write_audit_log(
                    actor='anonymous',
                    action='geo_validation_override_consumed',
                    resource_type='geo_validation_override',
                    resource_id=geo_override_used['override_id'],
                    details={'endpoint': '/api/submit-voice', 'request_district': district, 'evidence_district': str((geo_rule or {}).get('evidence_district') or '')},
                )
                geo_err = None
                geo_rule = None
            else:
                return jsonify({'success': False, 'error': geo_err, 'geo_validation': geo_rule, 'geo_override': {'override_id': override_id, 'status': 'rejected', **(rec or {})}, 'geo_context': geo}), 400
        if geo_err:
            return jsonify({'success': False, 'error': geo_err, 'geo_validation': geo_rule, 'geo_context': geo}), 400
    map_ward = geo.get('ward') or ''
    ward_source = geo.get('ward_source') or 'none'
    if geo.get('lat') is not None and geo.get('lng') is not None:
        lat = float(geo.get('lat'))
        lng = float(geo.get('lng'))
    route = resolve_ward_department_route(
        category=classification['category'],
        district=district,
        state=state,
        text=text,
        ward=map_ward,
        matrix=current_app.config.get('WARD_ROUTING_MATRIX', {}),
        category_to_service=current_app.config.get('CATEGORY_TO_SERVICE', {}),
        service_to_department=current_app.config.get('SERVICE_TO_DEPARTMENT', {}),
    )
    sla_policy = resolve_sla_policy(
        urgency=classification['urgency'],
        category=classification['category'],
        channel=source,
        routed_department=route['department'],
        urgency_to_hours=current_app.config.get('SLA_URGENCY_HOURS', {}),
        sla_rules=current_app.config.get('SLA_RULES', {}),
        escalation_targets=current_app.config.get('SLA_ESCALATION_TARGETS', {}),
    )
    sla_due_at = sla_policy['due_at']
    request_id = _generate_request_id()

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    ai_metadata = {
        'is_synthetic': bool(current_app.config.get('DEMO_MODE')),
        'data_mode': 'showcase_submission' if current_app.config.get('DEMO_MODE') else 'unverified_submission',
        'mode': _get_ai_mode(),
        'stt_mode': _get_stt_mode(),
        'stt': stt,
        'translation': translation,
        'code_mix_normalization': code_mix,
        'classification': classification,
        'model_trace': _extract_model_trace(classification=classification, translation=translation, stt=stt),
        'voice_validation': {
            'mime_type': mime_type,
            'audio_size_bytes': len(audio_bytes),
            'endpoint': '/api/submit-voice',
        },
        'ward_source': ward_source,
        'geo_context': {
            'pin': geo.get('pin') or '',
            'village': geo.get('village') or '',
            'resolved_district': geo.get('district') or district,
            'geo_confidence': float(geo.get('geo_confidence') or 0.0),
            'district_validation': geo.get('district_validation') or {},
            'geo_telemetry': geo.get('geo_telemetry') or {},
        },
        'sla_policy': sla_policy,
        'geo_override': geo_override_used or {},
        'pii_scrub': scrub_meta,
    }

    db = get_db()
    db.execute(
        '''
        INSERT INTO citizen_requests (
            request_id, source_channel, input_language, district, state, lat, lng,
            original_text, translated_text, category, urgency, sentiment, status,
            submitted_by, ward, service_type, routed_department, sla_due_at, sla_breached_at, sla_escalation_level, ai_metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            request_id,
            source,
            language,
            district,
            state,
            lat,
            lng,
            text,
            translation['translated_text'],
            classification['category'],
            classification['urgency'],
            classification['sentiment'],
            'New',
            'anonymous',
            route['ward'],
            route['service'],
            route['department'],
            sla_due_at,
            None,
            0,
            json.dumps(ai_metadata),
            now,
        ),
    )
    try:
        consent_event = _record_ingest_consent(request_id, data, channel='web', language=language, actor='anonymous', commit=False)
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Unable to persist citizen voice submission and consent record')
        return jsonify({'success': False, 'error': 'Your request could not be saved. Please try again.'}), 503
    _save_idempotency_key('/api/submit-voice', idempotency_key, request_id)

    ingest_payload = {
        'request_id': request_id,
        'district': district,
        'state': state,
        'urgency': classification.get('urgency'),
        'category': classification.get('category'),
        'source_channel': source,
        'created_at': now,
        'input_language': language,
        'original_text': text,
        'translated_text': translation.get('translated_text'),
        'sentiment': classification.get('sentiment'),
        'status': 'New',
        'lat': lat,
        'lng': lng,
    }
    ingest_telemetry = _dispatch_live_bigquery_and_pubsub(ingest_payload)
    submission_evidence = _record_submission_evidence(
        request_id, ai_metadata, '/api/submit-voice', now, ingest_telemetry
    )

    cluster = assign_request_to_cluster(
        request_id=request_id,
        state=state,
        category=classification.get('category'),
        text=(translation.get('translated_text') or text),
    )

    write_audit_log(
        actor='anonymous',
        action='citizen_voice_request_submitted',
        resource_type='citizen_request',
        resource_id=request_id,
        details={
            'district': district,
            'source': source,
            'category': classification['category'],
            'ai_mode': _get_ai_mode(),
            'stt_mode': _get_stt_mode(),
            'mime_type': mime_type,
            'audio_size_bytes': len(audio_bytes),
            'model_trace': _extract_model_trace(classification=classification, translation=translation, stt=stt),
            'geo_override': geo_override_used or {},
            'endpoint': '/api/submit-voice',
        },
    )

    return jsonify(
        {
            'success': True,
            'request_id': request_id,
            'transcript': text,
            'classification': classification,
            'routing': route,
            'cluster': cluster,
            'consent': consent_event,
        'ward_source': ward_source,
        'geo_confidence': float(geo.get('geo_confidence') or 0.0),
        'district_validation': geo.get('district_validation') or {},
        'geo_telemetry': geo.get('geo_telemetry') or {},
        'geo_override': geo_override_used or {},
        'sla_policy': sla_policy,
            'sla': {'due_at': sla_due_at, 'breached_at': None, 'escalation_level': 0, 'policy_mode': sla_policy.get('policy_mode'), 'tier1_target': sla_policy.get('tier1_target', {}), 'tier2_delay_hours': sla_policy.get('tier2_delay_hours'), 'tier2_target': sla_policy.get('tier2_target', {})},
            'ai_mode': _get_ai_mode(),
            'stt_mode': _get_stt_mode(),
            'model_trace': _extract_model_trace(classification=classification, translation=translation, stt=stt),
            'cloud_ingestion': ingest_telemetry,
            'submission_evidence': submission_evidence,
        }
    )



@intake_bp.route('/api/classify', methods=['POST'])
def classify_text():
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    language = data.get('language', 'en')
    result = _run_classification(text, language)
    return jsonify({'success': True, 'result': result, 'ai_mode': _get_ai_mode()})


@intake_bp.route('/api/hotspots', methods=['GET'])
@intake_bp.route('/api/v1/demand/heatmap', methods=['GET'])
def hotspots():
    from ..services.analyst_compat import geo
    return jsonify(geo(request.args))


@intake_bp.route('/api/v1/geo/layers', methods=['GET'])
def geo_layers():
    from ..services.analyst_compat import geo
    return jsonify(geo(request.args))


@intake_bp.route('/api/priority-projects', methods=['GET'])
def priority_projects():
    from ..services.analyst_compat import priorities
    return jsonify(priorities(request.args))



@intake_bp.route('/api/policy-brief/<district>', methods=['GET'])
def policy_brief(district):
    import html
    from ..services.analyst_workbench import stats as scoped_stats,scope_from
    scope=scope_from(request.args);scope['district']=district
    data=scoped_stats(scope)
    summary=f"{data['total_complaints']} requests; {data['emergency_count']} emergencies in the selected scope."
    brief=f"<h4>{html.escape(district)} decision evidence</h4><p>{html.escape(summary)}</p><p>Open Budget Scenarios for a project-specific, cited decision brief. No unsupported benefit estimate is generated.</p>"
    return jsonify(success=True,brief=brief,summary=summary,citations=[{'source':'operational_database','scope':scope,'as_of':data['metadata']['as_of']}],ai_mode='deterministic_evidence_summary')



@intake_bp.route('/api/security/webhook-status', methods=['GET'])
@require_roles('admin')
def webhook_security_status():
    allowed_ips_raw = (current_app.config.get('WEBHOOK_ALLOWED_IPS') or '').strip()
    allowed_ips = [item.strip() for item in allowed_ips_raw.split(',') if item.strip()]

    payload = {
        'success': True,
        'webhook_security': {
            'shared_token_required': bool((current_app.config.get('WEBHOOK_SHARED_TOKEN') or '').strip()),
            'whatsapp_verify_token_configured': bool((current_app.config.get('WHATSAPP_VERIFY_TOKEN') or '').strip()),
            'meta_signature_enforced': bool((current_app.config.get('META_APP_SECRET') or '').strip()),
            'telegram_secret_enforced': bool((current_app.config.get('TELEGRAM_WEBHOOK_SECRET') or '').strip()),
            'twilio_signature_enforced': bool((current_app.config.get('TWILIO_AUTH_TOKEN') or '').strip()),
            'exotel_signature_enforced': bool((current_app.config.get('EXOTEL_WEBHOOK_SECRET') or '').strip()),
            'replay_protection_enabled': bool(current_app.config.get('WEBHOOK_REQUIRE_REPLAY_PROTECTION', True)),
            'replay_window_seconds': int(current_app.config.get('WEBHOOK_REPLAY_WINDOW_SECONDS', 300)),
            'replay_store': (current_app.config.get('WEBHOOK_REPLAY_STORE') or 'db').strip().lower(),
            'bind_replay_in_signature': bool(current_app.config.get('WEBHOOK_BIND_REPLAY_IN_SIGNATURE', False)),
            'source_ip_allowlist_enabled': len(allowed_ips) > 0,
            'source_ip_allowlist_count': len(allowed_ips),
            'source_ip_allowlist': allowed_ips,
            'last_checked_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'policy_version': (current_app.config.get('WEBHOOK_SECURITY_POLICY_VERSION') or 'unknown'),
        }
    }

    write_audit_log(
        actor=g.current_user['name'],
        action='webhook_security_status_viewed',
        resource_type='security',
        details={
            'role': g.current_user['role'],
            'allowlist_count': len(allowed_ips),
            'replay_store': payload['webhook_security']['replay_store'],
        },
    )

    return jsonify(payload)
@intake_bp.route('/api/v1/auth/me', methods=['GET'])
@require_auth
def auth_me():
    return jsonify({'success': True, 'user': g.current_user})


@intake_bp.route('/api/v1/requests', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def list_requests_secure():
    try:
        payload = list_execution_requests(request.args)
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400

    write_audit_log(
        actor=g.current_user['name'],
        action='secure_requests_listed',
        resource_type='citizen_request',
        details={'limit': payload['limit'], 'offset': payload['offset'], 'role': g.current_user['role']},
    )

    return jsonify(payload)


@intake_bp.route('/api/requests/<request_id>/timeline', methods=['GET'])
def request_timeline_public(request_id):
    _apply_sla_policy_if_needed(request_id)
    request_row, timeline = _fetch_request_timeline(request_id)
    if not request_row:
        return jsonify({'success': False, 'error': 'request not found'}), 404
    return jsonify({'success': True, 'request': request_row, 'timeline': timeline})


@intake_bp.route('/api/requests/<request_id>/closure-feedback', methods=['POST'])
def submit_closure_feedback(request_id):
    data = request.get_json(silent=True) or request.form.to_dict()
    rating = int(data.get('rating', 0) or 0)
    feedback_text = str(data.get('feedback_text') or data.get('comment') or '').strip()
    submitted_by = str(data.get('submitted_by') or 'citizen').strip() or 'citizen'

    if rating < 1 or rating > 5:
        return jsonify({'success': False, 'error': 'rating must be between 1 and 5'}), 400

    db = get_db()
    row = db.execute('SELECT request_id, status FROM citizen_requests WHERE request_id = ? LIMIT 1', (request_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'request not found'}), 404

    reopen_triggered = 1 if rating <= 2 else 0

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    if db.backend == 'postgres':
        db.execute(
            '''
            INSERT INTO closure_feedback (request_id, rating, feedback_text, submitted_by, reopen_triggered, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (request_id) DO UPDATE SET
              rating = EXCLUDED.rating,
              feedback_text = EXCLUDED.feedback_text,
              submitted_by = EXCLUDED.submitted_by,
              reopen_triggered = EXCLUDED.reopen_triggered,
              created_at = EXCLUDED.created_at
            ''',
            (request_id, rating, feedback_text, submitted_by, reopen_triggered, now),
        )
    else:
        db.execute(
            '''
            INSERT OR REPLACE INTO closure_feedback (request_id, rating, feedback_text, submitted_by, reopen_triggered, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (request_id, rating, feedback_text, submitted_by, reopen_triggered, now),
        )

    if reopen_triggered:
        prev_status = str(row['status'])
        db.execute('UPDATE citizen_requests SET status = ? WHERE request_id = ?', ('Reopened', request_id))
        db.commit()
        _append_lifecycle_event(
            request_id,
            event_type='reopened_by_feedback',
            actor=submitted_by,
            channel='Citizen Portal',
            from_status=prev_status,
            to_status='Reopened',
            reason='low closure rating',
            details={'rating': rating},
        )
        write_audit_log(
            actor=submitted_by,
            action='request_reopened_by_feedback',
            resource_type='citizen_request',
            resource_id=request_id,
            details={'rating': rating},
        )
    else:
        db.commit()

    return jsonify({'success': True, 'request_id': request_id, 'rating': rating, 'reopen_triggered': bool(reopen_triggered)})


@intake_bp.route('/api/v1/requests/<request_id>/lifecycle/update', methods=['POST'])
@require_roles('admin', 'analyst', 'auditor')
def update_request_lifecycle_secure(request_id):
    data = request.get_json(silent=True) or request.form.to_dict()
    action = str(data.get('action') or '').strip().lower()
    reason = str(data.get('reason') or '').strip()
    sla_due_at = str(data.get('sla_due_at') or '').strip() or None

    action_to_status = {
        'acknowledged': 'Acknowledged',
        'prioritized': 'Prioritized',
        'capex_allocated': 'Capex Allocated',
        'in_progress': 'In Progress',
        'escalated': 'Escalated',
        'resolved': 'Resolved',
        'closed': 'Closed',
        'reopened': 'Reopened',
    }
    if action not in action_to_status:
        return jsonify({'success': False, 'error': f"action must be one of {sorted(action_to_status.keys())}"}), 400

    db = get_db()
    row = db.execute(
        'SELECT request_id, status, source_channel FROM citizen_requests WHERE request_id = ? LIMIT 1',
        (request_id,),
    ).fetchone()
    if not row:
        return jsonify({'success': False, 'error': 'request not found'}), 404

    from_status = str(row['status'])
    to_status = action_to_status[action]

    if action == 'closed':
        rating_row = db.execute('SELECT rating FROM closure_feedback WHERE request_id = ? LIMIT 1', (request_id,)).fetchone()
        if not rating_row:
            now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            db.execute(
                'INSERT INTO closure_feedback (request_id, rating, feedback_text, submitted_by, created_at) VALUES (?, ?, ?, ?, ?)',
                (request_id, 5, 'Administrative closure verified by officer execution team', 'Platform Admin', now_iso),
            )

    if sla_due_at:
        db.execute('UPDATE citizen_requests SET status = ?, sla_due_at = ? WHERE request_id = ?', (to_status, sla_due_at, request_id))
    else:
        db.execute('UPDATE citizen_requests SET status = ? WHERE request_id = ?', (to_status, request_id))
    db.commit()

    actor = getattr(g, 'current_user', {}).get('name', 'system') if hasattr(g, 'current_user') else 'system'
    _append_lifecycle_event(
        request_id,
        event_type=action,
        actor=actor,
        channel=str(row['source_channel']),
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        sla_due_at=sla_due_at,
        details={'endpoint': '/api/v1/requests/<request_id>/lifecycle/update'},
    )

    write_audit_log(
        actor=actor,
        action='request_lifecycle_updated',
        resource_type='citizen_request',
        resource_id=request_id,
        details={'from_status': from_status, 'to_status': to_status, 'reason': reason, 'sla_due_at': sla_due_at},
    )

    _apply_sla_policy_if_needed(request_id)
    request_row, timeline = _fetch_request_timeline(request_id)
    analytics_sync = 'not_configured'
    bigquery_client = current_app.extensions.get('google_bigquery_client')
    if bigquery_client:
        try:
            analytics_sync = 'synced' if bigquery_client.update_request_progress(request_row) else 'not_found'
        except Exception:
            current_app.logger.warning('Request progress saved locally; BigQuery status sync failed for %s', request_id)
            analytics_sync = 'failed'
    return jsonify({'success': True, 'request': request_row, 'timeline': timeline, 'analytics_sync': analytics_sync})




@intake_bp.route('/api/v1/requests/<request_id>/cosign-status', methods=['GET'])
def request_cosign_status(request_id):
    db = get_db()
    req = db.execute('SELECT request_id FROM citizen_requests WHERE request_id = ? LIMIT 1', (request_id,)).fetchone()
    if not req:
        return jsonify({'success': False, 'error': 'request not found'}), 404

    token_row = db.execute(
        '''
        SELECT token, token_status, issued_at, expires_at, used_at
        FROM request_tokens
        WHERE request_id = ?
        ORDER BY id DESC
        LIMIT 1
        ''',
        (request_id,),
    ).fetchone()

    if token_row is None:
        issued_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        token = f"CS-{secrets.token_hex(8)}"
        db.execute(
            '''
            INSERT INTO request_tokens (request_id, token, token_status, issued_at, expires_at, used_at, meta_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (request_id, token, 'active', issued_at, None, None, json.dumps({'auto_generated': True})),
        )
        db.commit()
        token_row = {'token': token, 'token_status': 'active', 'issued_at': issued_at, 'expires_at': None, 'used_at': None}

    support_row = db.execute(
        '''
        SELECT COUNT(*) AS verified_support_count
        FROM request_cosigns
        WHERE request_id = ? AND verified = 1
        ''',
        (request_id,),
    ).fetchone()

    return jsonify(
        {
            'success': True,
            'request_id': request_id,
            'verified_support_count': int(support_row['verified_support_count'] or 0),
            'token': {
                'value': token_row['token'],
                'status': token_row['token_status'],
                'issued_at': token_row['issued_at'],
                'expires_at': token_row['expires_at'],
                'used_at': token_row['used_at'],
            },
        }
    )


@intake_bp.route('/api/v1/requests/<request_id>/cosign', methods=['POST'])
def request_cosign(request_id):
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    token = str(data.get('token') or '').strip()
    supporter_ref = str(data.get('supporter_ref') or data.get('mobile') or data.get('email') or '').strip()
    supporter_name = str(data.get('supporter_name') or data.get('name') or '').strip()
    channel = str(data.get('channel') or 'public').strip() or 'public'

    if not token:
        return jsonify({'success': False, 'error': 'token is required'}), 400
    if not supporter_ref:
        return jsonify({'success': False, 'error': 'supporter_ref is required'}), 400

    db = get_db()
    req = db.execute('SELECT request_id FROM citizen_requests WHERE request_id = ? LIMIT 1', (request_id,)).fetchone()
    if not req:
        return jsonify({'success': False, 'error': 'request not found'}), 404

    token_row = db.execute(
        '''
        SELECT token, token_status, expires_at
        FROM request_tokens
        WHERE request_id = ? AND token = ?
        LIMIT 1
        ''',
        (request_id, token),
    ).fetchone()
    if token_row is None:
        return jsonify({'success': False, 'error': 'invalid token'}), 400
    if str(token_row['token_status'] or '').lower() != 'active':
        return jsonify({'success': False, 'error': 'token is not active'}), 400

    existing = db.execute(
        '''
        SELECT id FROM request_cosigns
        WHERE request_id = ? AND token = ? AND supporter_ref = ?
        LIMIT 1
        ''',
        (request_id, token, supporter_ref),
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    if existing is None:
        db.execute(
            '''
            INSERT INTO request_cosigns (request_id, token, supporter_ref, supporter_name, channel, verified, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (request_id, token, supporter_ref, supporter_name, channel, 1, now),
        )
        created = True
    else:
        created = False

    db.execute(
        'UPDATE request_tokens SET used_at = ?, token_status = ? WHERE request_id = ? AND token = ?',
        (now, 'active', request_id, token),
    )
    db.commit()

    support_row = db.execute(
        'SELECT COUNT(*) AS c FROM request_cosigns WHERE request_id = ? AND verified = 1',
        (request_id,),
    ).fetchone()

    write_audit_log(
        actor='public',
        action='request_cosigned',
        resource_type='citizen_request',
        resource_id=request_id,
        details={'supporter_ref': supporter_ref, 'channel': channel, 'created': created},
    )

    return jsonify({'success': True, 'request_id': request_id, 'created': created, 'verified_support_count': int(support_row['c'] or 0)})

@intake_bp.route('/api/v1/sla/sweep', methods=['POST'])
@require_roles('admin', 'analyst')
def sla_sweep_secure():
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        limit = min(max(int(data.get('limit', 200) or 200), 1), 1000)
    except Exception:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    db = get_db()
    rows = db.execute(
        '''
        SELECT request_id
        FROM citizen_requests
        WHERE sla_due_at IS NOT NULL
          AND status NOT IN ('Resolved', 'Closed')
          AND (sla_breached_at IS NULL OR sla_escalation_level < 2)
        ORDER BY id ASC
        LIMIT ?
        ''',
        (limit,),
    ).fetchall()

    touched = []
    for row in rows:
        updated = _apply_sla_policy_if_needed(str(row['request_id']), actor=g.current_user['name'])
        if updated and (updated.get('sla_breached_at') or int(updated.get('sla_escalation_level') or 0) >= 1):
            touched.append(str(row['request_id']))

    write_audit_log(
        actor=g.current_user['name'],
        action='sla_sweep_executed',
        resource_type='citizen_request',
        details={'limit': limit, 'touched_count': len(touched)},
    )

    return jsonify({'success': True, 'checked': len(rows), 'touched': len(touched), 'request_ids': touched})

@intake_bp.route('/api/v1/notifications/deliveries', methods=['GET'])
@require_roles('admin', 'auditor')
def list_notification_deliveries_secure():
    limit = min(max(int(request.args.get('limit', 100)), 1), 500)
    request_id = (request.args.get('request_id') or '').strip()
    stage = (request.args.get('stage') or '').strip().lower()
    status = (request.args.get('status') or '').strip().lower()

    clauses = []
    params = []
    if request_id:
        clauses.append('request_id = ?')
        params.append(request_id)
    if stage:
        clauses.append('LOWER(stage) = ?')
        params.append(stage)
    if status:
        clauses.append('LOWER(status) = ?')
        params.append(status)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''

    db = get_db()
    rows = db.execute(
        f'''
        SELECT delivery_id, request_id, stage, provider, channel, target_json, payload_json,
               status, attempt_count, max_retries, external_id, last_error, created_at, updated_at
        FROM notification_deliveries
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
        ''',
        tuple(params + [limit]),
    ).fetchall()

    items = []
    dp = dp_metadata()
    for row in rows:
        obj = dict(row)
        try:
            obj['target'] = json.loads(obj.get('target_json') or '{}')
        except Exception:
            obj['target'] = {}
        try:
            obj['payload'] = json.loads(obj.get('payload_json') or '{}')
        except Exception:
            obj['payload'] = {}
        obj.pop('target_json', None)
        obj.pop('payload_json', None)
        items.append(obj)

    write_audit_log(
        actor=g.current_user['name'],
        action='notification_deliveries_viewed',
        resource_type='notification_delivery',
        details={'limit': limit, 'request_id': request_id or None, 'stage': stage or None, 'status': status or None, 'count': len(items)},
    )

    return jsonify({'success': True, 'deliveries': items, 'meta': {'limit': limit, 'count': len(items)}})

@intake_bp.route('/api/v1/notifications/metrics', methods=['GET'])
@require_roles('admin', 'auditor')
def notification_metrics_secure():
    metrics = get_notification_metrics(limit=int(request.args.get('limit', 1000) or 1000))
    write_audit_log(
        actor=g.current_user['name'],
        action='notification_metrics_viewed',
        resource_type='notification_delivery',
        details={'limit': int(request.args.get('limit', 1000) or 1000), 'total': metrics.get('total', 0)},
    )
    return jsonify({'success': True, 'metrics': metrics})



@intake_bp.route('/api/v1/notifications/connectors/health', methods=['GET'])
@require_roles('admin', 'auditor')
def notification_connector_health_secure():
    limit = int(request.args.get('limit', 200) or 200)
    payload = get_notification_connector_health(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='notification_connector_health_viewed',
        resource_type='notification_connector',
        details={'limit': limit, 'count': int(payload.get('count') or 0)},
    )
    return jsonify({'success': True, 'connectors': payload.get('items', []), 'meta': {'count': int(payload.get('count') or 0), 'limit': limit}})


@intake_bp.route('/api/v1/notifications/receipt-events', methods=['GET'])
@require_roles('admin', 'auditor')
def notification_receipt_events_secure():
    limit = int(request.args.get('limit', 200) or 200)
    provider = (request.args.get('provider') or '').strip().lower()
    delivery_id = (request.args.get('delivery_id') or '').strip()
    payload = list_notification_receipt_events(limit=limit, provider=provider, delivery_id=delivery_id)
    write_audit_log(
        actor=g.current_user['name'],
        action='notification_receipt_events_viewed',
        resource_type='notification_receipt_event',
        details={'limit': limit, 'provider': provider or None, 'delivery_id': delivery_id or None, 'count': int(payload.get('count') or 0)},
    )
    return jsonify({'success': True, 'events': payload.get('items', []), 'meta': {'count': int(payload.get('count') or 0), 'limit': limit}})





def _emergency_ack_token_valid(raw_token, stored_digest, dispatched_at):
    """Validate a short-lived, one-time response-desk capability in constant time."""
    if not raw_token or not stored_digest or not dispatched_at:
        return False
    try:
        issued = datetime.fromisoformat(str(dispatched_at).replace('Z', '+00:00'))
        age_seconds = (datetime.now(timezone.utc) - issued).total_seconds()
        ttl_seconds = int(current_app.config.get('EMERGENCY_ACK_TOKEN_TTL_SECONDS', 900) or 900)
        if age_seconds < 0 or age_seconds > ttl_seconds:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(hash_api_token(str(raw_token)), str(stored_digest))


def _emergency_ack_bearer(db, auth_header):
    """Return an authorised emergency operator identity, never a general user."""
    if not auth_header:
        return None
    parts = auth_header.split(' ', 1)
    if len(parts) != 2 or parts[0].lower() != 'bearer' or not parts[1].strip():
        return None
    token_hash = hash_api_token(parts[1].strip())
    user_row = db.execute('SELECT id, name, role FROM users WHERE api_token_hash = ?', (token_hash,)).fetchone()
    if not user_row:
        user_row = db.execute('SELECT id, name, role FROM users WHERE api_token = ?', (parts[1].strip(),)).fetchone()
    if not user_row:
        return None
    allowed = {str(role).strip().lower() for role in current_app.config.get('EMERGENCY_ACK_ROLES', ['admin', 'auditor'])}
    return dict(user_row) if str(user_row['role']).strip().lower() in allowed else None


@intake_bp.route('/api/emergency/acknowledge', methods=['POST'])
@intake_bp.route('/emergency/acknowledge', methods=['POST'])
def acknowledge_emergency_dispatch():
    """
    DDMA / QRT Emergency Dispatch Acknowledgement Endpoint.
    Requires either the secure dispatch acknowledgement_token or authenticated session/bearer token.
    Transitions dispatch status to 'acknowledged', stopping the 15-minute fallback escalation timer.
    """
    data = request.get_json(silent=True) or {}
    request_id = str(data.get('request_id') or '').strip()
    ack_token = str(data.get('acknowledgement_token') or data.get('token') or '').strip()
    officer_name = str(data.get('officer_name') or 'Duty Officer').strip()
    officer_badge = str(data.get('officer_badge') or 'DDMA-QRT-DESK').strip()
    notes = str(data.get('notes') or '').strip()

    if not request_id:
        return jsonify({'success': False, 'error': 'request_id is required'}), 400

    db = get_db()
    row = db.execute(
        '''
        SELECT request_id, urgency, category, district, state,
               emergency_dispatch_status, dispatched_to, dispatched_at,
               acknowledgement_token, acknowledged_at, acknowledged_by
        FROM citizen_requests
        WHERE request_id = ?
        ''',
        (request_id,),
    ).fetchone()

    if not row:
        return jsonify({'success': False, 'error': f'Request {request_id} not found'}), 404

    # Acknowledgement can use a short-lived response-desk capability or a
    # specifically authorised DDMA/auditor identity; generic analyst users
    # are intentionally unable to stop escalation.
    token_valid = _emergency_ack_token_valid(ack_token, row['acknowledgement_token'], row['dispatched_at'])
    caller = _emergency_ack_bearer(db, request.headers.get('Authorization', '')) if not token_valid else None
    caller_authenticated = bool(caller)
    actor = officer_name
    if caller:
        actor = caller['name']

    if not token_valid and not caller_authenticated:
        return jsonify({
            'success': False,
            'error': 'Unauthorized: Valid acknowledgement_token or authorized bearer token required'
        }), 401

    if row['emergency_dispatch_status'] != 'dispatched':
        return jsonify({'success': False, 'error': 'Emergency dispatch is no longer awaiting acknowledgement'}), 409

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    db.execute(
        '''
        UPDATE citizen_requests
        SET emergency_dispatch_status = 'acknowledged',
            acknowledged_at = ?,
            acknowledged_by = ?,
            acknowledgement_notes = ?
        WHERE request_id = ? AND emergency_dispatch_status = 'dispatched'
        ''',
        (now, f"{officer_name} ({officer_badge})", notes or 'Dispatched unit confirmed on-scene', request_id),
    )
    db.commit()

    write_audit_log(
        actor=actor,
        action='emergency_dispatch_acknowledged',
        resource_type='citizen_request',
        resource_id=request_id,
        details={
            'officer_name': officer_name,
            'officer_badge': officer_badge,
            'notes': notes,
            'dispatched_to': row['dispatched_to'],
            'acknowledged_at': now,
        },
    )

    return jsonify({
        'success': True,
        'request_id': request_id,
        'emergency_dispatch_status': 'acknowledged',
        'dispatched_to': row['dispatched_to'],
        'acknowledged_at': now,
        'acknowledged_by': f"{officer_name} ({officer_badge})",
        'sla_hours': 2,
        'message': 'Emergency dispatch receipt successfully acknowledged by response team.',
    })


@intake_bp.route('/api/emergency/dispatch-status/<request_id>', methods=['GET'])
@intake_bp.route('/emergency/dispatch-status/<request_id>', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def get_emergency_dispatch_status(request_id):
    db = get_db()
    row = db.execute(
        '''
        SELECT request_id, urgency, category, district, state,
               emergency_dispatch_status, dispatched_to, dispatched_at,
               acknowledged_at, acknowledged_by, acknowledgement_notes,
               fallback_escalated_at, fallback_target, sla_due_at
        FROM citizen_requests
        WHERE request_id = ?
        ''',
        (request_id,),
    ).fetchone()

    if not row:
        return jsonify({'success': False, 'error': f'Request {request_id} not found'}), 404

    now_dt = datetime.now(timezone.utc)
    is_dispatched = (row['emergency_dispatch_status'] == 'dispatched')
    minutes_since_dispatch = None
    if row['dispatched_at']:
        try:
            disp_dt = datetime.fromisoformat(row['dispatched_at'].replace('Z', '+00:00'))
            minutes_since_dispatch = round((now_dt - disp_dt).total_seconds() / 60.0, 1)
        except Exception:
            pass

    return jsonify({
        'success': True,
        'request_id': row['request_id'],
        'urgency': row['urgency'],
        'category': row['category'],
        'district': row['district'],
        'state': row['state'],
        'emergency_dispatch_status': row['emergency_dispatch_status'],
        'dispatched_to': row['dispatched_to'],
        'dispatched_at': row['dispatched_at'],
        'acknowledged_at': row['acknowledged_at'],
        'acknowledged_by': row['acknowledged_by'],
        'acknowledgement_notes': row['acknowledgement_notes'],
        'fallback_escalated_at': row['fallback_escalated_at'],
        'fallback_target': row['fallback_target'],
        'sla_due_at': row['sla_due_at'],
        'minutes_since_dispatch': minutes_since_dispatch,
        'is_acknowledged': (row['emergency_dispatch_status'] == 'acknowledged'),
        'acknowledgement_sla_minutes': 15,
        'requires_immediate_fallback': bool(is_dispatched and minutes_since_dispatch is not None and minutes_since_dispatch > 15),
    })


@intake_bp.route('/api/emergency/check-escalations', methods=['POST'])
@intake_bp.route('/emergency/check-escalations', methods=['POST'])
@require_roles('admin')
def check_emergency_dispatch_escalations():
    """
    Checks all pending emergency dispatches. If an emergency has been in 'dispatched'
    status for > 15 minutes without officer acknowledgement, automatically escalates
    to the Collectorate 24/7 Crisis Hotline & District Magistrate Desk.
    """
    db = get_db()
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat().replace('+00:00', 'Z')
    cutoff_iso = (now_dt - timedelta(minutes=15)).isoformat().replace('+00:00', 'Z')

    rows = db.execute(
        '''
        SELECT request_id, district, dispatched_to, dispatched_at, fallback_target
        FROM citizen_requests
        WHERE urgency = 'Emergency'
          AND emergency_dispatch_status = 'dispatched'
          AND dispatched_at IS NOT NULL
          AND dispatched_at <= ?
        ''',
        (cutoff_iso,),
    ).fetchall()

    escalated = []
    for r in rows:
        req_id = r['request_id']
        fb_target = r['fallback_target'] or 'Collectorate 24/7 Crisis Hotline & District Magistrate Desk'
        db.execute(
            '''
            UPDATE citizen_requests
            SET emergency_dispatch_status = 'fallback_escalated',
                fallback_escalated_at = ?
            WHERE request_id = ?
            ''',
            (now_iso, req_id),
        )
        write_audit_log(
            actor='system_sla_daemon',
            action='emergency_dispatch_fallback_escalated',
            resource_type='citizen_request',
            resource_id=req_id,
            details={
                'reason': 'Emergency unacknowledged after 15-minute dispatch SLA',
                'dispatched_to': r['dispatched_to'],
                'dispatched_at': r['dispatched_at'],
                'escalated_to': fb_target,
                'escalated_at': now_iso,
            },
        )
        escalated.append({
            'request_id': req_id,
            'district': r['district'],
            'escalated_to': fb_target,
        })

    db.commit()
    return jsonify({
        'success': True,
        'checked_at': now_iso,
        'escalated_count': len(escalated),
        'escalated_requests': escalated,
    })

