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
from ..services.ivr_orchestrator import orchestrate_missed_call_callback, get_callback_status, process_due_callbacks, get_callback_metrics, reconcile_provider_callback, parse_provider_callback_payload, get_callback_alerts, emit_callback_alerts, list_callback_alert_history
from ..services.demand_analytics import compute_demand_velocity, compute_silence_map, compute_latent_demand_surface
from ..services.intelligence import (
    compute_gap_analysis,
    compute_hotspot_predictions,
    compute_priority_rankings_with_budget,
    compute_impact_metrics_and_auto_brief,
    compute_demand_decay_impact,
    compute_bigquery_analytics,
    compute_vertex_predictions,
)
from ..services.pipeline_queue import (
    enqueue_ingestion_job,
    get_pipeline_job,
    list_pipeline_jobs,
    list_worker_heartbeats,
    find_job_by_idempotency_key,
    retry_pipeline_job,
    cancel_pipeline_job,
    get_pipeline_metrics,
    process_ingestion_payload,
    get_outbox_replication_metrics,
    replicate_pending_outbox,
    fast_path_emergency_triage,
)
from ..services.routing import resolve_ward_department_route
from ..services.request_queue import list_execution_requests
from ..services.sla import resolve_sla_policy
from ..services.scoring import get_scoring_weights, upsert_scoring_weights, validate_scoring_weights, list_scoring_profiles, get_active_scoring_profile, activate_scoring_profile, rollback_scoring_profile
from ..services.notifications import dispatch_sla_notification, reconcile_notification_receipt, get_notification_metrics, register_notification_receipt_event, get_notification_connector_health, list_notification_receipt_events, get_notification_slo_dashboard, emit_notification_slo_alerts, get_notification_ops_overlay
from ..services.geo_ward import resolve_ward_from_map, resolve_geo_ward_context
from ..services.clustering import recompute_demand_clusters, list_demand_clusters, assign_request_to_cluster, merge_demand_clusters, split_demand_cluster, list_low_confidence_cluster_members, rescore_low_confidence_cluster_members, set_cluster_member_override, list_cluster_member_override_history, sweep_stale_cluster_member_overrides, export_cluster_member_override_history, get_cluster_member_override_metrics, get_cluster_member_override_alerts, get_cluster_member_override_backlog_summary
from ..services.governance import record_consent_event, list_consent_events, privatize_count, should_apply_dp, dp_metadata, get_dpdp_controls, get_dpdp_evidence, get_dpdp_readiness, get_dpg_status
from ..services.code_mix import normalize_code_mix
from ..services.policy_outputs import compute_demand_investment_alignment_map, generate_rag_policy_brief
from ..services.layer4_fusion import layer4_source_status, compute_layer4_fusion
from ..services.pii_scrubber import scrub_text, scrub_payload_fields
from ..services.ai_simulation import (
    simulate_gemini_intent_classification,
    simulate_speech_to_text,
    simulate_translation,
    simulate_dialogflow_cx_turn,
)

api_bp = Blueprint('api', __name__)


@api_bp.before_request
def validate_intake_consent_type():
    # Reject ambiguous booleans before model processing or persistence.
    if request.method=='POST' and request.path in ('/api/submit','/api/submit-voice'):
        payload=request.get_json(silent=True) or {}
        if isinstance(payload,dict) and 'consent_granted' in payload and not isinstance(payload['consent_granted'],bool):
            return jsonify(success=False,error='consent_granted must be a boolean'),400

_AI_TELEMETRY_BREACH_STATE = {}


LANG_TO_LOCALE = {
    'en': 'en-IN',
    'ta': 'ta-IN',
    'bn': 'bn-IN',
    'te': 'te-IN',
    'mr': 'mr-IN',
}


def _generate_request_id():
    date_str = datetime.now(timezone.utc).strftime('%Y%m%d')
    db = get_db()
    while True:
        suffix = secrets.token_hex(2).upper()
        rid = f"NVB-{date_str}{suffix}"
        row = db.execute("SELECT request_id FROM citizen_requests WHERE request_id = ? LIMIT 1", (rid,)).fetchone()
        if not row:
            return rid


def _dispatch_live_bigquery_and_pubsub(record_dict):
    """
    Dispatches ingested citizen request to both:
    1. Direct Real-Time BigQuery Streaming
    2. GCP Pub/Sub Event Topic (which triggers Cloud Function -> BigQuery)
    Returns explicit telemetry dictionary for API response transparency.
    """
    telemetry = {
        'bigquery_stream': 'disabled',
        'pubsub_publish': 'disabled',
        'ingest_status': 'not_configured',
        'errors': []
    }
    
    # 1. Direct Real-Time BigQuery Streaming Ingestion
    bigquery_client = current_app.extensions.get('google_bigquery_client')
    if bigquery_client:
        try:
            bigquery_client.insert_request(record_dict)
            telemetry['bigquery_stream'] = 'success'
            current_app.logger.info(f"Directly streamed request {record_dict.get('request_id')} to BigQuery.")
        except Exception as bq_err:
            telemetry['bigquery_stream'] = 'failed'
            telemetry['errors'].append('BigQuery replication failed')
            current_app.logger.warning('BigQuery real-time streaming ingestion failed for request %s', record_dict.get('request_id'))

    # 2. Event-Driven GCP Pub/Sub -> Cloud Function -> BigQuery Pipeline
    pubsub_client = current_app.extensions.get('google_pubsub_client')
    if pubsub_client:
        try:
            msg_id = pubsub_client.publish_request(record_dict)
            telemetry['pubsub_publish'] = 'success'
            telemetry['pubsub_msg_id'] = msg_id
            current_app.logger.info(f"Published complaint {record_dict.get('request_id')} to Pub/Sub topic {pubsub_client.topic_id}, msg_id={msg_id}")
        except Exception as ps_err:
            telemetry['pubsub_publish'] = 'failed'
            telemetry['errors'].append('Pub/Sub replication failed')
            current_app.logger.warning('Pub/Sub event publishing failed for request %s', record_dict.get('request_id'))

    configured = bool(bigquery_client or pubsub_client)
    if telemetry['bigquery_stream'] == 'success' and telemetry['pubsub_publish'] == 'success':
        telemetry['ingest_status'] = 'synced_dual_write'
    elif telemetry['bigquery_stream'] == 'success' or telemetry['pubsub_publish'] == 'success':
        telemetry['ingest_status'] = 'synced_partial'
    elif configured:
        telemetry['ingest_status'] = 'failed_dead_letter'

    replicated_bq = 1 if telemetry['bigquery_stream'] == 'success' else 0
    replicated_ps = 1 if telemetry['pubsub_publish'] == 'success' else 0
    rep_err_str = ' | '.join(telemetry['errors']) if telemetry['errors'] else None
    rep_at = (record_dict.get('created_at') or datetime.now(timezone.utc).isoformat()) if (replicated_bq or replicated_ps) else None

    try:
        db = get_db()
        db.execute(
            '''
            UPDATE citizen_requests
            SET replicated_bigquery = ?,
                replicated_pubsub = ?,
                replication_error = ?,
                replicated_at = ?
            WHERE request_id = ?
            ''',
            (replicated_bq, replicated_ps, rep_err_str, rep_at, record_dict.get('request_id')),
        )
        db.commit()
    except Exception as upd_err:
        current_app.logger.warning(f"Failed to update outbox replication status for request {record_dict.get('request_id')}: {upd_err}")

    if telemetry['errors']:
        try:
            db = get_db()
            db.execute(
                '''
                INSERT OR IGNORE INTO cloud_ingestion_dead_letters (
                    request_id, district, state, source_channel, payload_json, error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    record_dict.get('request_id'),
                    record_dict.get('district'),
                    record_dict.get('state'),
                    record_dict.get('source_channel'),
                    json.dumps(record_dict),
                    ' | '.join(telemetry['errors']),
                    record_dict.get('created_at') or datetime.now(timezone.utc).isoformat(),
                ),
            )
            db.commit()
            telemetry['dead_letter_logged'] = True
        except Exception as dl_err:
            current_app.logger.error(f"Failed to log ingestion dead letter: {dl_err}")

    return telemetry


def _submission_evidence(endpoint, recorded_at, cloud_ingestion=None):
    cloud = cloud_ingestion or {'ingest_status': 'pending'}
    cloud_status = str(cloud.get('ingest_status') or 'pending')
    if cloud_status == 'not_configured':
        cloud_validation = 'not_configured'
    elif cloud_status == 'pending':
        cloud_validation = 'pending'
    else:
        cloud_validation = 'attempted'
    return {
        'endpoint': endpoint,
        'recorded_at': recorded_at,
        'local_persistence': 'saved',
        'cloud_replication_status': cloud_status,
        'cloud_validation': cloud_validation,
        'field_validation': 'not_completed',
        'notice': 'A saved local request is not proof of cloud delivery, field service completion, or beneficiary outcomes.',
    }


def _record_submission_evidence(request_id, metadata, endpoint, recorded_at, cloud_ingestion):
    evidence = _submission_evidence(endpoint, recorded_at, cloud_ingestion)
    metadata['submission_evidence'] = evidence
    try:
        get_db().execute(
            'UPDATE citizen_requests SET ai_metadata_json = ? WHERE request_id = ?',
            (json.dumps(metadata), request_id),
        )
        get_db().commit()
        evidence['provenance_record'] = 'stored'
    except Exception:
        get_db().rollback()
        evidence['provenance_record'] = 'not_stored'
        current_app.logger.exception('Unable to record submission evidence for request %s', request_id)
    return evidence


def _stored_submission_evidence(request_row):
    try:
        metadata = json.loads((request_row or {}).get('ai_metadata_json') or '{}')
    except (TypeError, ValueError):
        metadata = {}
    return metadata.get('submission_evidence') or None


def _district_geo(repo, district):
    row = repo.get_district_row(district)
    if row is None:
        return 20.5937, 78.9629, 'Unknown'
    lat = float(row['lat']) + random.uniform(-0.03, 0.03)
    lng = float(row['lng']) + random.uniform(-0.03, 0.03)
    return round(lat, 4), round(lng, 4), str(row['state'])
def _scrub_ingress_payload(data, text_keys=None):
    payload = data if isinstance(data, dict) else {}
    scrubbed, meta = scrub_payload_fields(payload, text_keys=text_keys)
    return scrubbed, meta


def _record_ingest_consent(request_id: str, data, channel: str, language: str, actor: str = 'anonymous', commit=True):
    consent_granted = (data or {}).get('consent_granted')
    if consent_granted is not None and not isinstance(consent_granted,bool):
        raise ValueError('consent_granted must be a boolean')
    consent_scope = str((data or {}).get('consent_scope') or 'request_processing').strip() or 'request_processing'
    legal_basis = str((data or {}).get('legal_basis') or 'consent').strip() or 'consent'
    metadata = {
        'consent_text_version': str((data or {}).get('consent_text_version') or 'v1').strip() or 'v1',
        'source': str((data or {}).get('source') or channel).strip() or channel,
        'actor_role': actor,
        'event_state': 'missing' if consent_granted is None else ('granted' if consent_granted else 'declined'),
    }
    return record_consent_event(
        request_id=request_id,
        channel=channel,
        consent_scope=consent_scope,
        consent_granted=bool(consent_granted),
        language=language,
        actor=actor,
        legal_basis=legal_basis,
        metadata=metadata,
        commit=commit,
    )


def _get_ai_mode():
    google_client = current_app.extensions.get('google_ai_client')
    return 'google_ai_configured' if google_client else 'simulation'


def _bigquery_top_stats():
    bq = current_app.extensions.get('google_bigquery_client')
    if not bq:
        return None
    try:
        row = bq._run(
            f'''
            SELECT
              COUNT(1) AS total_complaints,
              COUNT(DISTINCT NULLIF(TRIM({bq.col_lang}), '')) AS languages_supported,
              COUNT(DISTINCT NULLIF(TRIM(district), '')) AS districts_covered,
              COUNT(DISTINCT NULLIF(TRIM(state), '')) AS states_covered,
              SAFE_DIVIDE(SUM(CASE WHEN LOWER(COALESCE(status,'')) IN ('resolved', 'closed') THEN 1 ELSE 0 END), COUNT(1)) * 100 AS resolution_rate
            FROM {bq.table_ref}
            '''
        )[0]
        return {
            'total_complaints': int(row['total_complaints'] or 0),
            'districts_covered': int(row['districts_covered'] or 0),
            'states_covered': int(row['states_covered'] or 0),
            'languages_supported': int(row['languages_supported'] or 0),
            'resolution_rate': int(round(float(row['resolution_rate'] or 0.0))),
        }
    except Exception:
        return None



def _bq_client():
    return current_app.extensions.get('google_bigquery_client')


def _bq_filters_sql(district: str = '', state: str = '', category: str = '', urgency: str = ''):
    where = []
    params = []
    if district:
        where.append("district = @district")
        params.append(('district', 'STRING', district))
    if state:
        where.append("state = @state")
        params.append(('state', 'STRING', state))
    if category:
        where.append("category = @category")
        params.append(('category', 'STRING', category))
    if urgency:
        where.append("urgency = @urgency")
        params.append(('urgency', 'STRING', urgency))
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    return clause, params


def _bq_params(bq, items):
    return [bq.bigquery.ScalarQueryParameter(name, typ, val) for (name, typ, val) in items]


def _bq_complaints(limit: int = 200, district: str = '', state: str = '', category: str = '', urgency: str = ''):
    bq = _bq_client()
    if not bq:
        return None
    safe_limit = min(max(int(limit or 200), 1), 500)
    where, pairs = _bq_filters_sql(district=district, state=state, category=category, urgency=urgency)
    pairs.append(('limit', 'INT64', safe_limit))
    params = _bq_params(bq, pairs)
    fields = getattr(bq, 'schema_fields', set())
    details_sql = ', '.join(
        f'CAST({field} AS STRING) AS {field}' if field in fields else f'CAST(NULL AS STRING) AS {field}'
        for field in ('ward', 'routed_department', 'sla_due_at', 'demo_dataset_version')
    )

    rows = bq._run(
        f'''
        SELECT
          {bq.col_id} AS request_id,
          district,
          state,
          {bq.col_lang} AS input_language,
          original_text,
          translated_text,
          category,
          urgency,
          sentiment,
          status,
          {bq.col_source} AS source_channel,
          CAST({bq.col_date} AS STRING) AS created_at,
          {details_sql}
        FROM {bq.table_ref}
        {where}
        ORDER BY created_at DESC
        LIMIT @limit
        ''',
        params,
    )

    out = []
    for row in rows:
        req = str(row.get('request_id') or '')
        out.append({
            'request_id': req,
            'district': str(row.get('district') or ''),
            'state': str(row.get('state') or ''),
            'input_language': str(row.get('input_language') or 'en'),
            'language': str(row.get('input_language') or 'en'),
            'original_text': str(row.get('original_text') or ''),
            'translated_text': str(row.get('translated_text') or ''),
            'category': str(row.get('category') or ''),
            'urgency': str(row.get('urgency') or ''),
            'sentiment': str(row.get('sentiment') or ''),
            'status': str(row.get('status') or ''),
            'source_channel': str(row.get('source_channel') or ''),
            'source': str(row.get('source_channel') or ''),
            'ward': str(row.get('ward') or ''),
            'routed_department': str(row.get('routed_department') or ''),
            'sla_due_at': str(row.get('sla_due_at') or ''),
            'demo_dataset_version': row.get('demo_dataset_version'),
            'created_at': str(row.get('created_at') or ''),
        })
    return out


def _bq_stats_payload(district: str = '', state: str = '', category: str = '', urgency: str = ''):
    bq = _bq_client()
    if not bq:
        return None
    where, pairs = _bq_filters_sql(district=district, state=state, category=category, urgency=urgency)
    params = _bq_params(bq, pairs)

    agg = bq._run(
        f'''
        SELECT
          COUNT(1) AS total_complaints,
          SUM(CASE WHEN urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count,
          SUM(CASE WHEN LOWER(COALESCE(status,'')) IN ('resolved', 'closed') THEN 1 ELSE 0 END) AS resolved_count,
          COUNT(DISTINCT NULLIF(TRIM(district), '')) AS districts_covered,
          COUNT(DISTINCT NULLIF(TRIM(state), '')) AS states_covered,
          COUNT(DISTINCT NULLIF(TRIM({bq.col_lang}), '')) AS languages_supported
        FROM {bq.table_ref}
        {where}
        ''',
        params,
    )[0]

    cats = bq._run(
        f'''
        SELECT category, COUNT(1) AS c
        FROM {bq.table_ref}
        {where}
        GROUP BY category
        ORDER BY c DESC
        ''',
        params,
    )

    trend = bq._run(
        f'''
        SELECT SUBSTR(CAST({bq.col_date} AS STRING), 1, 10) AS d, COUNT(1) AS c
        FROM {bq.table_ref}
        {where}
        GROUP BY d
        ORDER BY d ASC
        ''',
        params,
    )

    total = int(agg.get('total_complaints') or 0)
    resolved = int(agg.get('resolved_count') or 0)
    payload = {
        'total_complaints': total,
        'emergency_count': int(agg.get('emergency_count') or 0),
        'resolution_rate': int(round((resolved / total) * 100)) if total else 0,
        'districts_covered': int(agg.get('districts_covered') or 0),
        'states_covered': int(agg.get('states_covered') or 0),
        'languages_supported': int(agg.get('languages_supported') or 0),
        'categories': {str(r.get('category') or ''): int(r.get('c') or 0) for r in cats if str(r.get('category') or '')},
        'daily_trend': {str(r.get('d') or ''): int(r.get('c') or 0) for r in trend if str(r.get('d') or '')},
    }
    return payload

def _get_stt_mode():
    stt_client = current_app.extensions.get('google_stt_client')
    if stt_client:
        return 'google_stt_live'
    return 'simulation'


def _jury_live_models_required() -> bool:
    if current_app:
        val = current_app.config.get('JURY_REQUIRE_LIVE_MODELS')
        if val is not None:
            return bool(val)
    return os.environ.get('JURY_REQUIRE_LIVE_MODELS', '0').strip().lower() in {'1', 'true', 'yes'}


def _is_live_model_result(result: dict | None) -> bool:
    payload = result if isinstance(result, dict) else {}
    provider_mode = str(payload.get('provider_mode') or '').strip().lower()
    if provider_mode.startswith('google_ai_live') or provider_mode.endswith('_live'):
        return True
    if payload.get('fallback_used') is True:
        return False
    model = str(payload.get('model') or '').strip().lower()
    if 'simulat' in model or 'fallback' in model or provider_mode in {'simulation', 'local_dialogflow_cx_simulation'}:
        return False
    return bool(model)


def _extract_model_trace(classification=None, translation=None, stt=None):
    return {
        'classification_model_id': str(((classification or {}).get('model') or '')).strip(),
        'translation_model_id': str(((translation or {}).get('model') or '')).strip(),
        'stt_model_id': str(((stt or {}).get('model') or '')).strip(),
        'classification_provider_mode': str(((classification or {}).get('provider_mode') or '')).strip(),
        'translation_provider_mode': str(((translation or {}).get('provider_mode') or '')).strip(),
        'stt_provider_mode': str(((stt or {}).get('provider_mode') or '')).strip(),
    }


def _validate_geo_resolution_for_intake(requested_district: str, geo: dict):
    geo_payload = geo if isinstance(geo, dict) else {}
    district_validation = geo_payload.get('district_validation') or {}
    mismatch = bool(district_validation.get('mismatch'))
    input_district = str(district_validation.get('input_district') or requested_district or '').strip()
    evidence_district = str(district_validation.get('evidence_district') or '').strip()
    geo_confidence = float(geo_payload.get('geo_confidence') or 0.0)

    if mismatch and bool(current_app.config.get('GEO_STRICT_DISTRICT_MATCH', True)):
        return (
            'geo validation failed: district mismatch',
            {
                'rule': 'district_mismatch_block',
                'input_district': input_district,
                'evidence_district': evidence_district,
                'geo_confidence': round(geo_confidence, 3),
            },
        )

    if bool(current_app.config.get('GEO_ENFORCE_MIN_CONFIDENCE', False)):
        threshold = float(current_app.config.get('GEO_MIN_CONFIDENCE', 0.45) or 0.45)
        if geo_confidence < threshold:
            return (
                'geo validation failed: low geospatial confidence',
                {
                    'rule': 'min_geo_confidence',
                    'threshold': round(threshold, 3),
                    'geo_confidence': round(geo_confidence, 3),
                },
            )

    return None, None


def _ensure_geo_override_table():
    db = get_db()
    backend = str(getattr(db, 'backend', 'sqlite') or 'sqlite').strip().lower()
    if backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS geo_validation_overrides (
                id SERIAL PRIMARY KEY,
                override_id TEXT UNIQUE NOT NULL,
                endpoint TEXT NOT NULL,
                requested_district TEXT,
                evidence_district TEXT,
                reason TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                approved_role TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                max_uses INTEGER NOT NULL,
                used_count INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS geo_validation_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                override_id TEXT UNIQUE NOT NULL,
                endpoint TEXT NOT NULL,
                requested_district TEXT,
                evidence_district TEXT,
                reason TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                approved_role TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                max_uses INTEGER NOT NULL,
                used_count INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def _issue_geo_override(endpoint: str, requested_district: str, evidence_district: str, reason: str, approved_by: str, approved_role: str, ttl_minutes: int = 30, max_uses: int = 1):
    _ensure_geo_override_table()
    db = get_db()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat().replace('+00:00', 'Z')
    ttl = min(max(int(ttl_minutes or 30), 1), 180)
    uses = min(max(int(max_uses or 1), 1), 20)
    expires_iso = (now + timedelta(minutes=ttl)).isoformat().replace('+00:00', 'Z')
    override_id = f"GEO-OVR-{now.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"

    db.execute(
        '''
        INSERT INTO geo_validation_overrides (
            override_id, endpoint, requested_district, evidence_district, reason,
            approved_by, approved_role, approved_at, expires_at,
            max_uses, used_count, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            override_id,
            str(endpoint or '/api/submit').strip(),
            str(requested_district or '').strip(),
            str(evidence_district or '').strip(),
            str(reason or 'manual geo override').strip()[:500],
            str(approved_by or '').strip(),
            str(approved_role or '').strip(),
            now_iso,
            expires_iso,
            uses,
            0,
            1,
            now_iso,
            now_iso,
        ),
    )
    db.commit()
    return {
        'override_id': override_id,
        'endpoint': str(endpoint or '/api/submit').strip(),
        'requested_district': str(requested_district or '').strip(),
        'evidence_district': str(evidence_district or '').strip(),
        'expires_at': expires_iso,
        'max_uses': uses,
        'used_count': 0,
        'is_active': True,
    }


def _consume_geo_override(override_id: str, endpoint: str, requested_district: str, evidence_district: str):
    key = str(override_id or '').strip()
    if not key:
        return False, {'reason': 'missing_override_id'}

    _ensure_geo_override_table()
    db = get_db()
    row = db.execute(
        '''
        SELECT override_id, endpoint, requested_district, evidence_district,
               reason, approved_by, approved_role, approved_at, expires_at,
               max_uses, used_count, is_active
        FROM geo_validation_overrides
        WHERE override_id = ?
        LIMIT 1
        ''',
        (key,),
    ).fetchone()

    if not row:
        return False, {'reason': 'override_not_found'}

    rec = dict(row)
    if not bool(rec.get('is_active')):
        return False, {'reason': 'override_inactive', 'override_id': key}

    expires_at = str(rec.get('expires_at') or '').strip()
    try:
        exp_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
    except Exception:
        exp_dt = None
    now_dt = datetime.now(timezone.utc)
    if exp_dt and now_dt > exp_dt:
        db.execute('UPDATE geo_validation_overrides SET is_active = 0, updated_at = ? WHERE override_id = ?', (now_dt.isoformat().replace('+00:00', 'Z'), key))
        db.commit()
        return False, {'reason': 'override_expired', 'override_id': key, 'expires_at': expires_at}

    rec_ep = str(rec.get('endpoint') or '').strip().lower().rstrip('/')
    req_ep = str(endpoint or '').strip().lower().rstrip('/')
    if rec_ep != req_ep:
        return False, {'reason': 'endpoint_mismatch', 'override_id': key, 'override_endpoint': rec_ep, 'request_endpoint': req_ep}

    rec_req = str(rec.get('requested_district') or '').strip()
    rec_evi = str(rec.get('evidence_district') or '').strip()
    if rec_req and rec_req.lower() != str(requested_district or '').strip().lower():
        return False, {'reason': 'requested_district_mismatch', 'override_id': key}
    if rec_evi and rec_evi.lower() != str(evidence_district or '').strip().lower():
        return False, {'reason': 'evidence_district_mismatch', 'override_id': key}

    used_count = int(rec.get('used_count') or 0)
    max_uses = int(rec.get('max_uses') or 1)
    if used_count >= max_uses:
        db.execute('UPDATE geo_validation_overrides SET is_active = 0, updated_at = ? WHERE override_id = ?', (now_dt.isoformat().replace('+00:00', 'Z'), key))
        db.commit()
        return False, {'reason': 'override_exhausted', 'override_id': key}

    new_used = used_count + 1
    still_active = 1 if new_used < max_uses else 0
    now_iso = now_dt.isoformat().replace('+00:00', 'Z')
    db.execute(
        'UPDATE geo_validation_overrides SET used_count = ?, is_active = ?, updated_at = ? WHERE override_id = ?',
        (new_used, still_active, now_iso, key),
    )
    db.commit()

    rec['used_count'] = new_used
    rec['is_active'] = bool(still_active)
    return True, rec


def _detect_script_language(text, default_lang='en'):
    if not text:
        return default_lang or 'en'
    import re
    detected = default_lang or 'en'
    if re.search(r'[\u0B80-\u0BFF]', text):
        detected = 'ta'
    elif re.search(r'[\u0C00-\u0C7F]', text):
        detected = 'te'
    elif re.search(r'[\u0900-\u097F]', text):
        detected = 'hi'

    allowed_langs = current_app.config.get('LANGUAGES', {}) if current_app else {'ta': 'Tamil', 'te': 'Telugu', 'en': 'English'}
    if detected not in allowed_langs:
        return default_lang if default_lang in allowed_langs else 'en'
    return detected


def _run_translation(text, source_lang, target_lang='en'):
    source_lang = _detect_script_language(text, source_lang)
    if source_lang == target_lang:
        return {
            'translated_text': text,
            'source_language': source_lang,
            'target_language': target_lang,
            'model': 'VisBharat-Translation-NoOp'
        }
    # 1. Try Google Cloud Translation API if registered
    translate_client = current_app.extensions.get('google_translation_client')
    if translate_client:
        try:
            out = translate_client.translate_text(text, source_lang, target_lang)
            if _jury_live_models_required() and not _is_live_model_result(out):
                raise ValueError('live translation provider returned fallback/simulated output')
            return out
        except Exception:
            pass

    # 2. Try Google Gemini AI Client
    google_client = current_app.extensions.get('google_ai_client')
    if google_client:
        try:
            out = google_client.translate_text(text, source_lang, target_lang)
            if _jury_live_models_required() and not _is_live_model_result(out):
                raise ValueError('live translation provider returned fallback/simulated output')
            return out
        except Exception:
            pass

    # 3. Multilingual NLP Translation Engine Fallback
    if _jury_live_models_required():
        raise ValueError('live translation model required for jury path; fallback disabled')
    return simulate_translation(text, source_lang, target_lang)


def _run_classification(text, language):
    language = _detect_script_language(text, language)
    google_client = current_app.extensions.get('google_ai_client')
    if google_client:
        try:
            out = google_client.classify_request(text, language, current_app.config['CATEGORIES'])
            if _jury_live_models_required() and not _is_live_model_result(out):
                raise ValueError('live classifier returned fallback/simulated output')
            return out
        except Exception:
            pass
    if _jury_live_models_required():
        raise ValueError('live classifier required for jury path; fallback disabled')
    return simulate_gemini_intent_classification(text, language, current_app.config['CATEGORIES'])


def _extract_audio_payload(payload):
    if request.files and request.files.get('audio'):
        audio_file = request.files['audio']
        return audio_file.read(), (audio_file.mimetype or 'audio/wav')

    audio_b64 = (payload or {}).get('audio_base64')
    if audio_b64:
        if ',' in audio_b64 and audio_b64.startswith('data:'):
            header, audio_b64 = audio_b64.split(',', 1)
            mime_type = header.split(';')[0].replace('data:', '')
        else:
            mime_type = (payload or {}).get('audio_mime_type', 'audio/wav')
        return base64.b64decode(audio_b64), mime_type

    return None, None


def _asr_circuit_store():
    store = current_app.extensions.get('asr_circuit_state')
    if not isinstance(store, dict):
        store = {}
        current_app.extensions['asr_circuit_state'] = store
    return store


def _read_asr_circuit(provider: str):
    provider_key = str(provider or '').strip().lower()
    store = _asr_circuit_store()
    data = store.get(provider_key) or {}
    return {
        'provider': provider_key,
        'failure_count': int(data.get('failure_count') or 0),
        'open_until_epoch': int(data.get('open_until_epoch') or 0),
        'last_error': str(data.get('last_error') or ''),
        'updated_at': str(data.get('updated_at') or ''),
    }


def _write_asr_circuit(provider: str, failure_count: int, open_until_epoch: int, last_error: str):
    provider_key = str(provider or '').strip().lower()
    store = _asr_circuit_store()
    store[provider_key] = {
        'failure_count': max(int(failure_count or 0), 0),
        'open_until_epoch': max(int(open_until_epoch or 0), 0),
        'last_error': str(last_error or ''),
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }


def _get_asr_override_state():
    store = _asr_circuit_store()
    override = store.get('__override__') if isinstance(store.get('__override__'), dict) else {}
    mode = str(override.get('mode') or 'auto').strip().lower()
    if mode not in {'auto', 'force'}:
        mode = 'auto'
    forced_provider = str(override.get('forced_provider') or '').strip().lower()
    if forced_provider not in {'google', 'simulation', ''}:
        forced_provider = ''
    bypass_circuit = bool(override.get('bypass_circuit', False))
    updated_at = str(override.get('updated_at') or '')
    updated_by = str(override.get('updated_by') or '')
    return {
        'mode': mode,
        'forced_provider': forced_provider,
        'bypass_circuit': bypass_circuit,
        'updated_at': updated_at,
        'updated_by': updated_by,
    }


def _set_asr_override_state(mode: str, forced_provider: str, bypass_circuit: bool, updated_by: str):
    normalized_mode = str(mode or 'auto').strip().lower()
    if normalized_mode not in {'auto', 'force'}:
        normalized_mode = 'auto'

    normalized_provider = str(forced_provider or '').strip().lower()
    if normalized_provider not in {'google', 'simulation', ''}:
        normalized_provider = ''

    store = _asr_circuit_store()
    payload = {
        'mode': normalized_mode,
        'forced_provider': normalized_provider,
        'bypass_circuit': bool(bypass_circuit),
        'updated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'updated_by': str(updated_by or ''),
    }
    store['__override__'] = payload
    return payload


def _reset_asr_circuit_state(provider: str = ''):
    store = _asr_circuit_store()
    target = str(provider or '').strip().lower()
    reset = []
    if target:
        if target in {'google'}:
            _write_asr_circuit(target, failure_count=0, open_until_epoch=0, last_error='')
            reset.append(target)
        return reset

    for name in ['google']:
        _write_asr_circuit(name, failure_count=0, open_until_epoch=0, last_error='')
        reset.append(name)
    return reset


def _is_asr_circuit_open(provider: str):
    if not bool(current_app.config.get('LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED', True)):
        return False
    circuit = _read_asr_circuit(provider)
    return int(circuit.get('open_until_epoch') or 0) > int(time.time())


def _record_asr_circuit_success(provider: str):
    if not bool(current_app.config.get('LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED', True)):
        return
    _write_asr_circuit(provider, failure_count=0, open_until_epoch=0, last_error='')


def _record_asr_circuit_failure(provider: str, error_text: str):
    if not bool(current_app.config.get('LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED', True)):
        return

    fail_threshold = max(int(current_app.config.get('LANGUAGE_ASR_CIRCUIT_FAIL_THRESHOLD', 3) or 3), 1)
    base_open_seconds = max(int(current_app.config.get('LANGUAGE_ASR_CIRCUIT_OPEN_SECONDS', 60) or 60), 1)

    circuit = _read_asr_circuit(provider)
    failure_count = int(circuit.get('failure_count') or 0) + 1
    open_until = int(circuit.get('open_until_epoch') or 0)

    if failure_count >= fail_threshold:
        multiplier = min(max(failure_count - fail_threshold + 1, 1), 5)
        open_until = int(time.time()) + (base_open_seconds * multiplier)

    _write_asr_circuit(provider, failure_count=failure_count, open_until_epoch=open_until, last_error=str(error_text or '')[:300])


def _run_speech_to_text(payload, language, audio_bytes=None, mime_type=None):
    stt_client = current_app.extensions.get('google_stt_client')
    google_ai_client = current_app.extensions.get('google_ai_client')
    if audio_bytes is None:
        audio_bytes, mime_type = _extract_audio_payload(payload)

    effective_google_client = stt_client or google_ai_client

    locale = LANG_TO_LOCALE.get(language, 'en-IN')
    preferred = str(current_app.config.get('LANGUAGE_ASR_PROVIDER', 'google') or 'google').strip().lower()
    override = _get_asr_override_state()
    if override.get('mode') == 'force' and str(override.get('forced_provider') or '').strip():
        preferred = str(override.get('forced_provider') or preferred).strip().lower()

    require_live = _jury_live_models_required()

    if not audio_bytes:
        if preferred in {'google'}:
            raise ValueError('Voice input requires audio file (`audio`) or `audio_base64` for live ASR providers')
        if require_live:
            raise ValueError('live ASR required for jury path; simulation disabled')
        _record_asr_metric(provider='simulation', status='success', latency_ms=0.0)
        return simulate_speech_to_text(language)

    if preferred == 'simulation':
        if require_live:
            raise ValueError('ASR provider is simulation while jury requires live ASR')
        _record_asr_metric(provider='simulation', status='success', latency_ms=0.0)
        return simulate_speech_to_text(language)

    ordered = [('google', effective_google_client)]

    available = [(name, client) for name, client in ordered if client]

    if override.get('mode') == 'force' and preferred in {'google'}:
        available = [(name, client) for name, client in available if name == preferred]

    if bool(current_app.config.get('LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED', True)) and available and not bool(override.get('bypass_circuit', False)):
        closed = [(name, client) for name, client in available if not _is_asr_circuit_open(name)]
        open_only = [(name, client) for name, client in available if _is_asr_circuit_open(name)]
        closed = sorted(closed, key=lambda item: int(_read_asr_circuit(item[0]).get('failure_count') or 0))
        available = closed + open_only

    errors = []
    primary_provider = available[0][0] if available else (ordered[0][0] if ordered else 'simulation')

    for provider_name, provider_client in available:
        if _is_asr_circuit_open(provider_name) and not bool(override.get('bypass_circuit', False)):
            _record_asr_metric(
                provider=provider_name,
                status='circuit_open',
                latency_ms=0.0,
                fallback_from=(primary_provider if provider_name != primary_provider else ''),
            )
            errors.append(f"{provider_name}:circuit_open")
            continue

        started = time.perf_counter()
        try:
            out = provider_client.transcribe_bytes(audio_bytes=audio_bytes, language_code=locale, mime_type=mime_type or 'audio/wav')
            latency_ms = (time.perf_counter() - started) * 1000.0
            _record_asr_metric(
                provider=provider_name,
                status='success',
                latency_ms=latency_ms,
                fallback_from=(primary_provider if provider_name != primary_provider else ''),
            )
            _record_asr_circuit_success(provider_name)
            if require_live and not _is_live_model_result(out):
                raise ValueError('live ASR provider returned fallback/simulated output')
            return out
        except Exception as err:
            latency_ms = (time.perf_counter() - started) * 1000.0
            _record_asr_metric(provider=provider_name, status='failed', latency_ms=latency_ms)
            _record_asr_circuit_failure(provider_name, str(err))
            errors.append(f"{provider_name}:{err}")
            continue

    if errors and bool(current_app.config.get('LANGUAGE_ASR_STRICT_MODE', False)):
        raise ValueError('; '.join(errors))

    if require_live:
        raise ValueError('live ASR providers unavailable; simulation fallback disabled for jury path')

    _record_asr_metric(provider='simulation', status='success', latency_ms=0.0, fallback_from=primary_provider)
    return simulate_speech_to_text(language)


def _asr_metrics_store():
    store = current_app.extensions.get('asr_metrics_store')
    if not isinstance(store, dict):
        store = {'events': []}
        current_app.extensions['asr_metrics_store'] = store
    if 'events' not in store or not isinstance(store['events'], list):
        store['events'] = []
    return store


def _record_asr_metric(provider: str, status: str, latency_ms: float, fallback_from: str = ''):
    store = _asr_metrics_store()
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    events = store['events']
    events.append(
        {
            'provider': str(provider or 'unknown').strip().lower() or 'unknown',
            'status': str(status or 'unknown').strip().lower() or 'unknown',
            'latency_ms': round(float(latency_ms or 0.0), 3),
            'fallback_from': str(fallback_from or '').strip().lower(),
            'created_at': now,
        }
    )
    max_events = int(current_app.config.get('ASR_METRICS_MAX_EVENTS', 1000) or 1000)
    if len(events) > max_events:
        del events[: len(events) - max_events]


def _get_asr_metrics_summary(limit: int = 500):
    store = _asr_metrics_store()
    events = list(store.get('events') or [])[-min(max(int(limit or 500), 1), 5000):]

    by_provider = {}
    for event in events:
        provider = str(event.get('provider') or 'unknown')
        item = by_provider.setdefault(
            provider,
            {
                'provider': provider,
                'total': 0,
                'success': 0,
                'failed': 0,
                'fallback': 0,
                'circuit_open': 0,
                'avg_latency_ms': 0.0,
                'p95_latency_ms': 0.0,
                'recent_status': 'unknown',
            },
        )
        item['total'] += 1
        status = str(event.get('status') or '').lower()
        if status == 'success':
            item['success'] += 1
        elif status == 'failed':
            item['failed'] += 1
        elif status == 'circuit_open':
            item['circuit_open'] += 1
        if str(event.get('fallback_from') or '').strip():
            item['fallback'] += 1
        item['recent_status'] = status or item['recent_status']

    for provider, item in by_provider.items():
        latencies = [float(e.get('latency_ms') or 0.0) for e in events if str(e.get('provider') or '') == provider and float(e.get('latency_ms') or 0.0) > 0]
        if latencies:
            latencies_sorted = sorted(latencies)
            item['avg_latency_ms'] = round(sum(latencies) / len(latencies), 3)
            p95_index = max(min(int(len(latencies_sorted) * 0.95) - 1, len(latencies_sorted) - 1), 0)
            item['p95_latency_ms'] = round(float(latencies_sorted[p95_index]), 3)

    circuit_states = []
    for provider in ['google']:
        c = _read_asr_circuit(provider)
        c['is_open'] = int(c.get('open_until_epoch') or 0) > int(time.time())
        circuit_states.append(c)

    return {
        'total_events': len(events),
        'providers': sorted(by_provider.values(), key=lambda x: x['provider']),
        'recent_events': events[-20:],
        'circuit_breaker': {
            'enabled': bool(current_app.config.get('LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED', True)),
            'fail_threshold': int(current_app.config.get('LANGUAGE_ASR_CIRCUIT_FAIL_THRESHOLD', 3) or 3),
            'open_seconds': int(current_app.config.get('LANGUAGE_ASR_CIRCUIT_OPEN_SECONDS', 60) or 60),
            'states': circuit_states,
        },
        'override': _get_asr_override_state(),
    }


def _validate_voice_submit_payload(payload, repo):
    language = (payload.get('language') or '').strip()
    district = (payload.get('district') or '').strip()
    source = (payload.get('source') or '').strip()

    if not language:
        return 'language is required', None
    if language not in current_app.config['LANGUAGES']:
        return f"language must be one of {list(current_app.config['LANGUAGES'].keys())}", None

    if not district:
        return 'district is required', None
    if repo.get_district_row(district) is None:
        return 'district is not recognized in reference dataset', None

    if not source:
        return 'source is required', None

    try:
        audio_bytes, mime_type = _extract_audio_payload(payload)
    except Exception:
        return 'audio payload is invalid base64 or corrupted', None

    if not audio_bytes:
        return 'voice endpoint requires `audio` file or `audio_base64`', None

    if len(audio_bytes) > int(current_app.config.get('VOICE_MAX_BYTES', 10 * 1024 * 1024)):
        return f"audio exceeds max allowed size of {current_app.config.get('VOICE_MAX_BYTES')} bytes", None

    allowed_mime = {str(m).lower() for m in current_app.config.get('ALLOWED_AUDIO_MIME_TYPES', [])}
    normalized_mime = (mime_type or 'audio/wav').lower()
    mime_base = normalized_mime.split(';', 1)[0].strip()
    if mime_base not in allowed_mime:
        return f"unsupported audio mime type: {normalized_mime}", None

    return None, {'audio_bytes': audio_bytes, 'mime_type': mime_base, 'language': language, 'district': district, 'source': source}

@api_bp.route('/api/health', methods=['GET'])
def health():
    google_maps_key = (current_app.config.get('GOOGLE_MAPS_API_KEY') or '').strip()
    outbox_metrics = get_outbox_replication_metrics()
    return jsonify(
        {
            'success': True,
            'service': 'Nexus VisBharat v1',
            'status': 'healthy',
            'ai_mode': _get_ai_mode(),
            'stt_mode': _get_stt_mode(),
            'google_maps_configured': bool(google_maps_key),
            'cloud_outbox': outbox_metrics,
        }
    )


@api_bp.route('/api/outbox/status', methods=['GET'])
def outbox_status():
    metrics = get_outbox_replication_metrics()
    return jsonify(success=True, outbox=metrics)


@api_bp.route('/api/outbox/replicate', methods=['POST'])
@require_auth
def outbox_replicate():
    limit = int(request.args.get('limit') or 50)
    result = replicate_pending_outbox(limit=limit)
    metrics = get_outbox_replication_metrics()
    return jsonify(success=True, result=result, outbox=metrics)


@api_bp.route('/api/v1/geo/maps-health', methods=['GET'])
@require_roles('admin', 'auditor')
def maps_health_secure():
    key = str(current_app.config.get('GOOGLE_MAPS_API_KEY') or '').strip()
    client = current_app.extensions.get('google_maps_client')
    use_real = bool(current_app.config.get('USE_REAL_GOOGLE_MAPS', False))

    payload = {
        'success': True,
        'maps': {
            'use_real_google_maps': use_real,
            'api_key_configured': bool(key),
            'client_initialized': bool(client),
            'provider': 'google_maps_platform',
            'health_status': 'ok' if (bool(key) and bool(client)) else 'degraded',
        },
    }

    probe = str(request.args.get('probe') or '').strip().lower() in {'1', 'true', 'yes'}
    if probe and client and hasattr(client, 'geocode_address'):
        query = str(request.args.get('query') or 'Chennai, India').strip() or 'Chennai, India'
        started = time.perf_counter()
        try:
            res = client.geocode_address(query)
            payload['maps']['probe'] = {
                'query': query,
                'latency_ms': round((time.perf_counter() - started) * 1000.0, 3),
                'success': bool(res.get('success')),
                'status': str(res.get('status') or ''),
                'model': str(res.get('model') or ''),
            }
        except Exception as err:
            payload['maps']['probe'] = {
                'query': query,
                'latency_ms': round((time.perf_counter() - started) * 1000.0, 3),
                'success': False,
                'status': 'ERROR',
                'error': str(err)[:240],
            }

    write_audit_log(
        actor=g.current_user['name'],
        action='geo_maps_health_viewed',
        resource_type='geo_maps',
        details={'probe': probe, 'client_initialized': bool(client), 'api_key_configured': bool(key)},
    )
    return jsonify(payload)


@api_bp.route('/api/v1/geo/overrides', methods=['POST'])
@require_roles('admin', 'auditor')
def create_geo_override_secure():
    data = request.get_json(silent=True) or {}
    endpoint = str(data.get('endpoint') or '/api/submit').strip() or '/api/submit'
    if endpoint not in {'/api/submit', '/api/submit-voice'}:
        return jsonify({'success': False, 'error': 'endpoint must be /api/submit or /api/submit-voice'}), 400

    requested_district = str(data.get('requested_district') or '').strip()
    evidence_district = str(data.get('evidence_district') or '').strip()
    reason = str(data.get('reason') or '').strip()
    ttl_minutes = int(data.get('ttl_minutes') or 30)
    max_uses = int(data.get('max_uses') or 1)

    if not reason:
        return jsonify({'success': False, 'error': 'reason is required'}), 400

    issued = _issue_geo_override(
        endpoint=endpoint,
        requested_district=requested_district,
        evidence_district=evidence_district,
        reason=reason,
        approved_by=str((g.current_user or {}).get('name') or 'unknown'),
        approved_role=str((g.current_user or {}).get('role') or 'unknown'),
        ttl_minutes=ttl_minutes,
        max_uses=max_uses,
    )

    write_audit_log(
        actor=g.current_user['name'],
        action='geo_validation_override_issued',
        resource_type='geo_validation_override',
        resource_id=issued.get('override_id'),
        details={
            'endpoint': endpoint,
            'requested_district': requested_district,
            'evidence_district': evidence_district,
            'ttl_minutes': min(max(ttl_minutes, 1), 180),
            'max_uses': min(max(max_uses, 1), 20),
            'reason': reason[:300],
        },
    )
    return jsonify({'success': True, 'override': issued})


@api_bp.route('/api/v1/geo/overrides', methods=['GET'])
@require_roles('admin', 'auditor')
def list_geo_overrides_secure():
    _ensure_geo_override_table()
    limit = min(max(int(request.args.get('limit', 20) or 20), 1), 200)
    db = get_db()
    rows = db.execute(
        '''
        SELECT override_id, endpoint, requested_district, evidence_district,
               reason, approved_by, approved_role, approved_at, expires_at,
               max_uses, used_count, is_active, created_at, updated_at
        FROM geo_validation_overrides
        ORDER BY id DESC
        LIMIT ?
        ''',
        (limit,),
    ).fetchall()
    return jsonify({'success': True, 'items': [dict(r) for r in rows], 'meta': {'limit': limit}})


@api_bp.route('/api/v1/geo/overrides/<override_id>/revoke', methods=['POST'])
@require_roles('admin')
def revoke_geo_override_secure(override_id):
    oid = str(override_id or '').strip()
    if not oid:
        return jsonify({'success': False, 'error': 'override_id is required'}), 400

    _ensure_geo_override_table()
    db = get_db()
    row = db.execute(
        '''
        SELECT override_id, endpoint, requested_district, evidence_district,
               reason, approved_by, approved_role, approved_at, expires_at,
               max_uses, used_count, is_active, created_at, updated_at
        FROM geo_validation_overrides
        WHERE override_id = ?
        LIMIT 1
        ''',
        (oid,),
    ).fetchone()

    if not row:
        return jsonify({'success': False, 'error': 'override not found', 'override_id': oid}), 404

    rec = dict(row)
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    if bool(rec.get('is_active')):
        db.execute(
            'UPDATE geo_validation_overrides SET is_active = 0, updated_at = ? WHERE override_id = ?',
            (now_iso, oid),
        )
        db.commit()
        rec['is_active'] = 0
        rec['updated_at'] = now_iso

    write_audit_log(
        actor=g.current_user['name'],
        action='geo_validation_override_revoked',
        resource_type='geo_validation_override',
        resource_id=oid,
        details={
            'endpoint': rec.get('endpoint'),
            'requested_district': rec.get('requested_district'),
            'evidence_district': rec.get('evidence_district'),
            'used_count': int(rec.get('used_count') or 0),
            'max_uses': int(rec.get('max_uses') or 0),
            'was_active': bool(row['is_active']),
            'revoked_at': now_iso,
        },
    )

    return jsonify(
        {
            'success': True,
            'override': {
                'override_id': oid,
                'endpoint': rec.get('endpoint'),
                'requested_district': rec.get('requested_district'),
                'evidence_district': rec.get('evidence_district'),
                'is_active': False,
                'used_count': int(rec.get('used_count') or 0),
                'max_uses': int(rec.get('max_uses') or 0),
                'updated_at': rec.get('updated_at'),
            },
        }
    )


@api_bp.route('/api/v1/analyst/demand-surface', methods=['GET'])
def analyst_demand_surface():
    from ..services.analyst_workbench import inclusion,scope_from
    data=inclusion(scope_from(request.args))
    return jsonify(success=True,surface={'raw_surface':data['items'],'latent_need_surface':[],'summary':{'total_districts_analyzed':len(data['items']),'corrected_red_zones_count':0,'max_vai_multiplier':None},'status':'Unvalidated latent inference retired; use observed access-risk screening'},inclusion=data)


@api_bp.route('/api/deployment/profile', methods=['GET'])
def deployment_profile():
    repo = current_app.extensions['reference_repo']
    profile = (current_app.config.get('DEPLOYMENT_PROFILE') or 'pilot').strip().lower()
    languages = current_app.config.get('LANGUAGES', {})
    scope = current_app.config.get('ALLOWED_STATE_TO_DISTRICTS', {}) or {}

    return jsonify(
        {
            'success': True,
            'deployment_profile': profile,
            'profile_mode': 'pilot' if profile == 'pilot' else 'national',
            'pilot_scope_enforced': bool(scope),
            'languages': languages,
            'language_codes': list(languages.keys()),
            'states': repo.list_states(),
            'districts': repo.list_districts(),
            'state_to_districts_scope': scope,
            'counts': {
                'languages': len(languages),
                'states': len(repo.list_states()),
                'districts': len(repo.list_districts()),
            },
        }
    )


@api_bp.route('/api/ai/status', methods=['GET'])
def ai_status():
    from ..services.provider_evidence import status, verify_all_live_providers
    should_probe = request.args.get('probe', '').lower() in ('1', 'true', 'yes') or request.args.get('verify', '').lower() in ('1', 'true', 'yes')
    if should_probe:
        try:
            verify_all_live_providers(current_app)
        except Exception as err:
            current_app.logger.warning('Live probe failed: %s', err)
    result = status()
    services = result['services']
    def operation(provider, name):
        return services[provider]['operations'][name]['status']

    # Both intake paths can translate; summarize the latest attempted operation.
    translations = [services[p]['operations']['translate_text']
                    for p in ('google_translation', 'google_ai')]
    attempted_translations = [item for item in translations if item['last_invocation']]
    translation_status = (max(attempted_translations,
        key=lambda item: item['last_invocation']['checked_at'])['status']
        if attempted_translations else
        'configured' if any(item['status'] == 'configured' for item in translations) else 'unavailable')

    # Retrieve recent verifiable invocation telemetry
    db = get_db()
    recent_invocations = []
    try:
        inv_rows = db.execute('''
            SELECT trace_id, provider, operation, status, model, checked_at, latency_ms, fallback_used, transport
            FROM provider_invocations
            ORDER BY checked_at DESC LIMIT 5
        ''').fetchall()
        for r in inv_rows:
            recent_invocations.append({
                'trace_id': r['trace_id'],
                'provider': r['provider'],
                'operation': r['operation'],
                'status': r['status'],
                'model': r['model'],
                'checked_at': r['checked_at'],
                'latency_ms': r['latency_ms'],
                'fallback_used': bool(r['fallback_used']),
                'transport': r['transport'],
            })
    except Exception:
        pass

    inference_events = [dict(item['last_invocation'], status=item['status'])
        for provider in ('google_ai', 'google_translation', 'google_stt', 'google_vertex', 'google_dialogflow')
        for item in services[provider]['operations'].values() if item['last_invocation']]
    latest_evidence = max(inference_events, key=lambda item: item['checked_at'], default=None)
    ai_client = current_app.extensions.get('google_ai_client')
    gemini_health = ai_client.get_health() if (ai_client and hasattr(ai_client, 'get_health')) else None

    result.update(success=True,
        mode='google_ai_' + services['google_ai']['status'],
        stt_mode='google_stt_' + services['google_stt']['status'],
        preferred_asr_provider='google',
        cloud_run_service_configured=bool(current_app.config.get('CLOUD_RUN_SERVICE_URL')),
        cloud_run_service_url=current_app.config.get('CLOUD_RUN_SERVICE_URL', ''),
        translation_configured=any(services[p]['configured'] for p in ('google_translation', 'google_ai')),
        translation_status=translation_status,
        recent_invocations=recent_invocations,
        latest_inference=latest_evidence,
        gemini_health=gemini_health,
        supported_features=['multilingual_translation','request_classification','policy_brief_generation',
            'voice_input_stt','dialogflow_cx_guided_conversation','bigquery_analytics','vertex_prediction'],
        feature_status={
            'multilingual_translation': translation_status,
            'request_classification': operation('google_ai','classify_request'),
            'policy_brief_generation': operation('google_ai','generate_policy_brief'),
            'voice_input_stt': operation('google_stt','transcribe_bytes'),
            'dialogflow_cx_guided_conversation': operation('google_dialogflow','detect_intent'),
            'bigquery_analytics': operation('google_bigquery','aggregate_requests'),
            'vertex_prediction': operation('google_vertex','predict_stress')},
        status_integrity='Configured clients are not proof of inference. Verification uses persisted operation results with an expiry.')
    for provider, details in services.items():
        result[provider + '_configured'] = details['configured']
    for short in ('bigquery','vertex','dialogflow','tts','maps'):
        result[short + '_mode'] = services['google_' + short]['status']
    return jsonify(result)
 
 
@api_bp.route('/api/ai/verify', methods=['GET', 'POST'])
def ai_verify():
    from ..services.provider_evidence import status, verify_all_live_providers
    try:
        verify_all_live_providers(current_app)
    except Exception as err:
        current_app.logger.warning('verify_all_live_providers failed: %s', err)
    res = status()
    res['success'] = True
    return jsonify(res)



@api_bp.route('/api/v1/language/asr/metrics', methods=['GET'])
@require_roles('admin', 'auditor')
def language_asr_metrics_secure():
    limit = min(max(int(request.args.get('limit', 500) or 500), 1), 5000)
    summary = _get_asr_metrics_summary(limit=limit)
    write_audit_log(
        actor=g.current_user['name'],
        action='language_asr_metrics_viewed',
        resource_type='language_asr',
        details={
            'limit': limit,
            'total_events': int(summary.get('total_events') or 0),
            'providers': len(summary.get('providers') or []),
        },
    )
    return jsonify({'success': True, 'metrics': summary, 'meta': {'limit': limit}})


@api_bp.route('/api/v1/language/asr/control', methods=['GET'])
@require_roles('admin', 'auditor')
def language_asr_control_status_secure():
    payload = {
        'role': str((g.current_user or {}).get('role') or ''),
        'override': _get_asr_override_state(),
        'circuit_breaker': {
            'enabled': bool(current_app.config.get('LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED', True)),
            'fail_threshold': int(current_app.config.get('LANGUAGE_ASR_CIRCUIT_FAIL_THRESHOLD', 3) or 3),
            'open_seconds': int(current_app.config.get('LANGUAGE_ASR_CIRCUIT_OPEN_SECONDS', 60) or 60),
            'states': [_read_asr_circuit('google')],
        },
    }
    write_audit_log(
        actor=g.current_user['name'],
        action='language_asr_control_viewed',
        resource_type='language_asr',
        details={'role': g.current_user['role']},
    )
    return jsonify({'success': True, 'control': payload})


@api_bp.route('/api/v1/language/asr/provider-override', methods=['POST'])
@require_roles('admin')
def language_asr_provider_override_secure():
    data = request.get_json(silent=True) or {}
    mode = str(data.get('mode') or 'auto').strip().lower()
    forced_provider = str(data.get('forced_provider') or '').strip().lower()
    bypass_circuit = bool(data.get('bypass_circuit', False))

    if mode not in {'auto', 'force'}:
        return jsonify({'success': False, 'error': 'mode must be auto or force'}), 400
    if mode == 'force' and forced_provider not in {'google', 'simulation'}:
        return jsonify({'success': False, 'error': 'forced_provider must be one of google, simulation when mode=force'}), 400

    state = _set_asr_override_state(mode=mode, forced_provider=forced_provider, bypass_circuit=bypass_circuit, updated_by=g.current_user['name'])
    write_audit_log(
        actor=g.current_user['name'],
        action='language_asr_override_updated',
        resource_type='language_asr',
        details={'mode': state.get('mode'), 'forced_provider': state.get('forced_provider'), 'bypass_circuit': bool(state.get('bypass_circuit'))},
    )
    return jsonify({'success': True, 'override': state})


@api_bp.route('/api/v1/language/asr/circuit/reset', methods=['POST'])
@require_roles('admin')
def language_asr_circuit_reset_secure():
    data = request.get_json(silent=True) or {}
    provider = str(data.get('provider') or '').strip().lower()
    if provider and provider not in {'google'}:
        return jsonify({'success': False, 'error': 'provider must be google, or empty for all'}), 400

    reset = _reset_asr_circuit_state(provider=provider)
    write_audit_log(
        actor=g.current_user['name'],
        action='language_asr_circuit_reset',
        resource_type='language_asr',
        details={'provider': provider or None, 'reset': reset},
    )
    return jsonify({'success': True, 'reset': reset})





def _build_asr_audit_export_metadata(actor: str, export_format: str, filters: dict, count: int):
    exported_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    canonical_filters = {
        'format': str(export_format or 'json'),
        'action': str(filters.get('action') or ''),
        'actor': str(filters.get('actor') or ''),
        'date_from': str(filters.get('date_from') or ''),
        'date_to': str(filters.get('date_to') or ''),
        'limit': int(filters.get('limit') or 0),
    }
    payload = {
        'exported_by': str(actor or ''),
        'exported_at': exported_at,
        'filters': canonical_filters,
        'count': int(count or 0),
    }
    signing_secret = str(current_app.config.get('ASR_AUDIT_EXPORT_SIGNING_SECRET') or current_app.config.get('SECRET_KEY') or '').strip()
    canonical_json = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    digest = hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()
    signature = hmac.new(signing_secret.encode('utf-8'), canonical_json.encode('utf-8'), hashlib.sha256).hexdigest() if signing_secret else ''
    payload['digest_sha256'] = digest
    payload['signature_hmac_sha256'] = signature
    return payload



def _verify_asr_audit_export_metadata(export_metadata: dict):
    meta = export_metadata if isinstance(export_metadata, dict) else {}
    signing_secret = str(current_app.config.get('ASR_AUDIT_EXPORT_SIGNING_SECRET') or current_app.config.get('SECRET_KEY') or '').strip()

    canonical_payload = {
        'exported_by': str(meta.get('exported_by') or ''),
        'exported_at': str(meta.get('exported_at') or ''),
        'filters': meta.get('filters') if isinstance(meta.get('filters'), dict) else {},
        'count': int(meta.get('count') or 0),
    }
    canonical_json = json.dumps(canonical_payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    computed_digest = hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()
    computed_signature = hmac.new(signing_secret.encode('utf-8'), canonical_json.encode('utf-8'), hashlib.sha256).hexdigest() if signing_secret else ''

    provided_digest = str(meta.get('digest_sha256') or '')
    provided_signature = str(meta.get('signature_hmac_sha256') or '')

    digest_valid = bool(provided_digest) and hmac.compare_digest(provided_digest, computed_digest)
    signature_valid = bool(provided_signature) and bool(computed_signature) and hmac.compare_digest(provided_signature, computed_signature)

    return {
        'valid': bool(digest_valid and signature_valid),
        'digest_valid': bool(digest_valid),
        'signature_valid': bool(signature_valid),
        'computed_digest_sha256': computed_digest,
        'computed_signature_hmac_sha256': computed_signature,
        'canonical_payload': canonical_payload,
    }

@api_bp.route('/api/v1/language/asr/control/audit-trail', methods=['GET'])
@require_roles('admin', 'auditor')
def language_asr_control_audit_trail_secure():
    limit = min(max(int(request.args.get('limit', 20) or 20), 1), 500)
    export_format = str(request.args.get('format') or 'json').strip().lower()
    action_filter = str(request.args.get('action') or '').strip().lower()
    actor_filter = str(request.args.get('actor') or '').strip()
    date_from = str(request.args.get('date_from') or '').strip()
    date_to = str(request.args.get('date_to') or '').strip()

    allowed_actions = [
        'language_asr_override_updated',
        'language_asr_circuit_reset',
        'language_asr_control_viewed',
    ]

    clauses = [f"action IN ({','.join(['?'] * len(allowed_actions))})"]
    params = list(allowed_actions)

    if action_filter:
        if action_filter not in allowed_actions:
            return jsonify({'success': False, 'error': 'invalid action filter'}), 400
        clauses.append('action = ?')
        params.append(action_filter)

    if actor_filter:
        clauses.append('actor = ?')
        params.append(actor_filter)

    if date_from:
        clauses.append('created_at >= ?')
        params.append(date_from)

    if date_to:
        clauses.append('created_at <= ?')
        params.append(date_to)

    db = get_db()
    rows = db.execute(
        f'''
        SELECT actor, action, resource_type, resource_id, details_json, ip_address, created_at
        FROM audit_logs
        WHERE {' AND '.join(clauses)}
        ORDER BY id DESC
        LIMIT ?
        ''',
        tuple(params + [limit]),
    ).fetchall()

    items = []
    for row in rows:
        obj = dict(row)
        try:
            obj['details'] = json.loads(obj.get('details_json') or '{}')
        except Exception:
            obj['details'] = {}
        obj.pop('details_json', None)
        items.append(obj)

    write_audit_log(
        actor=g.current_user['name'],
        action='language_asr_control_audit_trail_viewed',
        resource_type='language_asr',
        details={
            'limit': limit,
            'count': len(items),
            'role': g.current_user['role'],
            'format': export_format,
            'action': action_filter or None,
            'actor': actor_filter or None,
            'date_from': date_from or None,
            'date_to': date_to or None,
        },
    )

    filters_payload = {
        'action': action_filter,
        'actor': actor_filter,
        'date_from': date_from,
        'date_to': date_to,
        'limit': limit,
    }
    export_meta = _build_asr_audit_export_metadata(
        actor=g.current_user['name'],
        export_format=export_format,
        filters=filters_payload,
        count=len(items),
    )

    if export_format == 'csv':
        import io
        import csv

        buffer = io.StringIO()
        buffer.write(f"# exported_by,{export_meta.get('exported_by', '')}\n")
        buffer.write(f"# exported_at,{export_meta.get('exported_at', '')}\n")
        buffer.write(f"# digest_sha256,{export_meta.get('digest_sha256', '')}\n")
        buffer.write(f"# signature_hmac_sha256,{export_meta.get('signature_hmac_sha256', '')}\n")
        buffer.write(f"# filters,{json.dumps(export_meta.get('filters') or {}, ensure_ascii=False)}\n")

        writer = csv.DictWriter(buffer, fieldnames=['actor', 'action', 'resource_type', 'resource_id', 'ip_address', 'created_at', 'details'])
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    'actor': item.get('actor'),
                    'action': item.get('action'),
                    'resource_type': item.get('resource_type'),
                    'resource_id': item.get('resource_id') or '',
                    'ip_address': item.get('ip_address') or '',
                    'created_at': item.get('created_at') or '',
                    'details': json.dumps(item.get('details') or {}, ensure_ascii=False),
                }
            )

        return current_app.response_class(
            buffer.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=asr_control_audit_trail.csv'},
        )

    return jsonify({'success': True, 'items': items, 'meta': {'limit': limit, 'count': len(items), 'format': export_format}, 'export_metadata': export_meta})




@api_bp.route('/api/v1/language/asr/control/audit-trail/verify', methods=['POST'])
@require_roles('admin', 'auditor')
def language_asr_control_audit_trail_verify_secure():
    data = request.get_json(silent=True) or {}
    export_metadata = data.get('export_metadata')

    if not isinstance(export_metadata, dict):
        return jsonify({'success': False, 'error': 'export_metadata object is required'}), 400

    result = _verify_asr_audit_export_metadata(export_metadata)

    write_audit_log(
        actor=g.current_user['name'],
        action='language_asr_control_audit_trail_verified',
        resource_type='language_asr',
        details={
            'valid': bool(result.get('valid')),
            'digest_valid': bool(result.get('digest_valid')),
            'signature_valid': bool(result.get('signature_valid')),
            'role': g.current_user['role'],
        },
    )

    return jsonify({'success': True, 'verification': result})


@api_bp.route('/api/tts/synthesize', methods=['POST'])
def tts_synthesize():
    data = request.get_json(silent=True) or {}
    text = str(data.get('text') or '').strip()
    language = str(data.get('language') or 'en').strip().lower() or 'en'

    if not text:
        return jsonify({'success': False, 'error': 'text is required', 'fallback': 'browser_tts'}), 400

    locale = LANG_TO_LOCALE.get(language, language if '-' in language else 'en-IN')
    speaking_rate = float(data.get('speaking_rate', 1.0) or 1.0)
    pitch = float(data.get('pitch', 0.0) or 0.0)

    tts_client = current_app.extensions.get('google_tts_client')
    if not tts_client:
        return jsonify({'success': False, 'error': 'google cloud tts unavailable', 'fallback': 'browser_tts'}), 503

    try:
        payload = tts_client.synthesize(
            text=text,
            language_code=locale,
            speaking_rate=speaking_rate,
            pitch=pitch,
        )
        return jsonify({'success': True, 'tts': payload})
    except Exception as err:
        return jsonify({'success': False, 'error': str(err), 'fallback': 'browser_tts'}), 503

@api_bp.route('/api/dialogflow/config', methods=['GET'])
def assistant_configuration():
    from ..services.citizen_assistant import configuration
    return configuration()


@api_bp.route('/api/dialogflow/session', methods=['POST'])
def dialogflow_session_turn():
    from ..services.citizen_assistant import session_turn
    return session_turn()

@api_bp.route('/api/db/status', methods=['GET'])
def db_status():
    from ..services.runtime_readiness import persistence
    db = get_db()
    # Never expose a database URL: it may contain a username and password.
    return jsonify(success=True, **{**persistence(current_app.config), 'backend': db.backend})


@api_bp.route('/api/v1/geo/ward-suggest', methods=['POST'])
def suggest_ward_from_geo():
    data = request.get_json(silent=True) or {}
    district = str(data.get('district') or '').strip()
    if not district:
        return jsonify({'success': False, 'error': 'district is required'}), 400

    geo = resolve_geo_ward_context(
        district=district,
        text=(data.get('text') or ''),
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
    return jsonify({'success': True, **geo})
@api_bp.route('/api/submit', methods=['POST'])
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


@api_bp.route('/api/attachments/ingest-stub', methods=['POST'])
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


def _apply_sla_policy_if_needed(request_id, actor='sla-engine'):
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


@api_bp.route('/api/v1/requests/<request_id>/track', methods=['GET'])
@api_bp.route('/api/requests/<request_id>/track', methods=['GET'])
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


@api_bp.route('/api/transcribe-voice', methods=['POST'])
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


@api_bp.route('/api/submit-voice', methods=['POST'])
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
def _ingest_text_request(channel, text, language, district, sender='anonymous', endpoint=''):
    raw_text = str(text or '').strip()
    language = (language or 'en').strip().lower() or 'en'
    district = (district or '').strip()
    idem_key = (request.headers.get('X-Idempotency-Key') or '').strip()

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


@api_bp.route('/api/channels/whatsapp/webhook', methods=['GET', 'POST'])
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


@api_bp.route('/api/channels/telegram/webhook', methods=['POST'])
def telegram_webhook():
    if not _verify_telegram_secret():
        return jsonify({'success': False, 'error': 'invalid telegram secret'}), 401
    has_custom_secret = bool((request.headers.get('X-Telegram-Bot-Api-Secret-Token') or '').strip())
    if not has_custom_secret and not _require_webhook_token():
        return jsonify({'success': False, 'error': 'invalid webhook token'}), 401

    data = request.get_json(silent=True) or {}
    msg = data.get('message') or {}
    text = msg.get('text') or data.get('text')
    payload, error = _ingest_text_request(
        channel='Telegram',
        text=text,
        language=(data.get('language') or 'en'),
        district=(data.get('district')),
        sender=str((msg.get('from') or {}).get('id', 'telegram-user')),
        endpoint='/api/channels/telegram/webhook',
    )
    if error:
        return jsonify({'success': False, 'error': error[0]}), error[1]

    bot_token = (current_app.config.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chat_id = (msg.get('chat') or {}).get('id') or (msg.get('from') or {}).get('id')
    if bot_token and chat_id and payload.get('success'):
        req_id = payload.get('request_id', '')
        cat = (payload.get('classification') or {}).get('category', 'General')
        urg = (payload.get('classification') or {}).get('urgency', 'Routine')
        dept = (payload.get('routing') or {}).get('routed_department', 'Municipal Administration')
        reply_text = (
            f"[Registered] Citizen Request Registered\n\n"
            f"Request ID: `{req_id}`\n"
            f"Category: {cat}\n"
            f"Urgency: {urg}\n"
            f"Routed To: {dept}\n\n"
            f"Thank you for reporting to VisBharat!"
        )
        try:
            requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": reply_text,
                    "parse_mode": "Markdown"
                },
                timeout=5
            )
        except Exception:
            pass

    return jsonify(payload)


@api_bp.route('/api/channels/<channel>/session/<session_key>', methods=['GET'])
def get_channel_session_endpoint(channel, session_key):
    from ..db import get_channel_session
    session = get_channel_session(channel, session_key)
    return jsonify({'success': True, 'session': session or {}})


@api_bp.route('/api/channels/<channel>/session/<session_key>', methods=['POST', 'PUT'])
def save_channel_session_endpoint(channel, session_key):
    from ..db import save_channel_session
    data = request.get_json(silent=True) or {}
    save_channel_session(channel, session_key, data)
    return jsonify({'success': True})


@api_bp.route('/api/channels/<channel>/session/<session_key>', methods=['DELETE'])
def delete_channel_session_endpoint(channel, session_key):
    from ..db import delete_channel_session
    delete_channel_session(channel, session_key)
    return jsonify({'success': True})





@api_bp.route('/api/channels/sms/keyword', methods=['POST'])
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


@api_bp.route('/api/channels/ivr/missed-call', methods=['POST'])
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
@api_bp.route('/api/channels/sms/webhook', methods=['POST'])
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


@api_bp.route('/api/channels/email/webhook', methods=['POST'])
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


@api_bp.route('/api/channels/ivr/webhook', methods=['POST'])
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


@api_bp.route('/api/channels/ivr/callbacks/<callback_id>', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def ivr_callback_status(callback_id):
    item = get_callback_status(callback_id)
    if not item:
        return jsonify({'success': False, 'error': 'callback not found'}), 404
    return jsonify({'success': True, 'callback': item})


@api_bp.route('/api/channels/ivr/callbacks/process', methods=['POST'])
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

@api_bp.route('/api/channels/ivr/callbacks/metrics', methods=['GET'])
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

@api_bp.route('/api/channels/ivr/callbacks/webhook', methods=['POST'])
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


@api_bp.route('/api/channels/ivr/callbacks/alerts', methods=['GET'])
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

@api_bp.route('/api/channels/ivr/callbacks/alerts/history', methods=['GET'])
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


@api_bp.route('/api/channels/ivr/callbacks/alerts/history/export', methods=['GET'])
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


@api_bp.route('/api/complaints', methods=['GET'])
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


@api_bp.route('/api/stats', methods=['GET'])
def stats():
    from ..services.analyst_workbench import stats as scoped_stats, scope_from
    data=scoped_stats(scope_from(request.args))
    return jsonify(success=True,stats=data,categories=data['categories'],daily_trend=data['daily_trend'],source='operational_database')


@api_bp.route('/api/districts', methods=['GET'])
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


@api_bp.route('/api/states', methods=['GET'])
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


@api_bp.route('/api/translate', methods=['POST'])
def translate_text():
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    source = data.get('source_lang', 'en')
    target = data.get('target_lang', 'en')
    return jsonify({'success': True, 'result': _run_translation(text, source, target), 'ai_mode': _get_ai_mode()})


@api_bp.route('/api/classify', methods=['POST'])
def classify_text():
    data = request.get_json(silent=True) or {}
    text = data.get('text', '')
    language = data.get('language', 'en')
    result = _run_classification(text, language)
    return jsonify({'success': True, 'result': result, 'ai_mode': _get_ai_mode()})


@api_bp.route('/api/hotspots', methods=['GET'])
@api_bp.route('/api/v1/demand/heatmap', methods=['GET'])
def hotspots():
    from ..services.analyst_compat import geo
    return jsonify(geo(request.args))


@api_bp.route('/api/v1/geo/layers', methods=['GET'])
def geo_layers():
    from ..services.analyst_compat import geo
    return jsonify(geo(request.args))


@api_bp.route('/api/priority-projects', methods=['GET'])
def priority_projects():
    from ..services.analyst_compat import priorities
    return jsonify(priorities(request.args))



@api_bp.route('/api/policy-brief/<district>', methods=['GET'])
def policy_brief(district):
    import html
    from ..services.analyst_workbench import stats as scoped_stats,scope_from
    scope=scope_from(request.args);scope['district']=district
    data=scoped_stats(scope)
    summary=f"{data['total_complaints']} requests; {data['emergency_count']} emergencies in the selected scope."
    brief=f"<h4>{html.escape(district)} decision evidence</h4><p>{html.escape(summary)}</p><p>Open Budget Scenarios for a project-specific, cited decision brief. No unsupported benefit estimate is generated.</p>"
    return jsonify(success=True,brief=brief,summary=summary,citations=[{'source':'operational_database','scope':scope,'as_of':data['metadata']['as_of']}],ai_mode='deterministic_evidence_summary')



@api_bp.route('/api/security/webhook-status', methods=['GET'])
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
@api_bp.route('/api/v1/auth/me', methods=['GET'])
@require_auth
def auth_me():
    return jsonify({'success': True, 'user': g.current_user})


@api_bp.route('/api/v1/requests', methods=['GET'])
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


@api_bp.route('/api/requests/<request_id>/timeline', methods=['GET'])
def request_timeline_public(request_id):
    _apply_sla_policy_if_needed(request_id)
    request_row, timeline = _fetch_request_timeline(request_id)
    if not request_row:
        return jsonify({'success': False, 'error': 'request not found'}), 404
    return jsonify({'success': True, 'request': request_row, 'timeline': timeline})


@api_bp.route('/api/requests/<request_id>/closure-feedback', methods=['POST'])
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


@api_bp.route('/api/v1/requests/<request_id>/lifecycle/update', methods=['POST'])
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




@api_bp.route('/api/v1/requests/<request_id>/cosign-status', methods=['GET'])
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


@api_bp.route('/api/v1/requests/<request_id>/cosign', methods=['POST'])
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

@api_bp.route('/api/v1/sla/sweep', methods=['POST'])
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

@api_bp.route('/api/v1/notifications/deliveries', methods=['GET'])
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

@api_bp.route('/api/v1/notifications/metrics', methods=['GET'])
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



@api_bp.route('/api/v1/notifications/connectors/health', methods=['GET'])
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


@api_bp.route('/api/v1/notifications/receipt-events', methods=['GET'])
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


@api_bp.route('/api/v1/notifications/slo-dashboard', methods=['GET'])
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



@api_bp.route('/api/v1/notifications/ops-overlay', methods=['GET'])
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

@api_bp.route('/api/v1/notifications/receipts', methods=['POST'])
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


@api_bp.route('/api/notifications/receipts/webhook', methods=['POST'])
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
@api_bp.route('/api/v1/audit-logs', methods=['GET'])
@api_bp.route('/api/v1/audit/logs', methods=['GET'])
@require_roles('admin', 'auditor')
def list_audit_logs_secure():
    from ..services import auditor_workbench as audit
    result=audit.event_page(audit.scope_from(request.args),request.args)
    records=result.pop('items')
    # Preserve one established legacy field without repeating every record three times.
    return jsonify(success=True,audit_logs=records,**result)


@api_bp.route('/api/v1/audit/export', methods=['GET'])
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


@api_bp.route('/api/v1/security/alerts', methods=['GET'])
@require_roles('admin', 'auditor')
def get_security_alerts_secure():
    from ..services import auditor_workbench as audit
    result=audit.security_feed(audit.scope_from(request.args),audit.page_limit(request.args.get('limit')))
    return jsonify(success=True,**result)


@api_bp.route('/api/v1/users', methods=['GET'])
@api_bp.route('/api/v1/admin/users', methods=['GET'])
@require_roles('admin')
def list_users():
    db = get_db()
    rows = db.execute('SELECT id, name, role, created_at FROM users ORDER BY id ASC').fetchall()
    return jsonify({'success': True, 'users': [dict(r) for r in rows]})


@api_bp.route('/api/v1/users', methods=['POST'])
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







@api_bp.route('/api/v1/users/<int:user_id>/rotate-token', methods=['POST'])
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











@api_bp.route('/api/v1/pipeline/jobs', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def list_pipeline_jobs_secure():
    limit = min(int(request.args.get('limit', 100)), 500)
    rows = list_pipeline_jobs(limit=limit)
    return jsonify({'success': True, 'jobs': rows})


@api_bp.route('/api/v1/pipeline/jobs/<job_id>', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def get_pipeline_job_secure(job_id):
    row = get_pipeline_job(job_id)
    if row is None:
        return jsonify({'success': False, 'error': 'job not found'}), 404
    return jsonify({'success': True, 'job': row})


@api_bp.route('/api/v1/pipeline/workers', methods=['GET'])
@require_roles('admin', 'auditor')
def list_pipeline_workers_secure():
    limit = min(int(request.args.get('limit', 100)), 500)
    rows = list_worker_heartbeats(limit=limit)
    return jsonify({'success': True, 'workers': rows})


@api_bp.route('/api/v1/pipeline/jobs/<job_id>/retry', methods=['POST'])
@require_roles('admin')
def retry_pipeline_job_secure(job_id):
    result, err = retry_pipeline_job(job_id)
    if err:
        code = 404 if err == 'job not found' else 400
        return jsonify({'success': False, 'error': err}), code
    return jsonify({'success': True, 'job': result})


@api_bp.route('/api/v1/pipeline/jobs/<job_id>/cancel', methods=['POST'])
@require_roles('admin')
def cancel_pipeline_job_secure(job_id):
    result, err = cancel_pipeline_job(job_id)
    if err:
        code = 404 if err == 'job not found' else 400
        return jsonify({'success': False, 'error': err}), code
    return jsonify({'success': True, 'job': result})


@api_bp.route('/api/v1/pipeline/metrics', methods=['GET'])
@require_roles('admin', 'auditor')
def get_pipeline_metrics_secure():
    return jsonify({'success': True, 'metrics': get_pipeline_metrics()})



@api_bp.route('/api/v1/intelligence/demand-velocity', methods=['GET'])
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


@api_bp.route('/api/v1/intelligence/silence-map', methods=['GET'])
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


@api_bp.route('/api/v1/layer4/status', methods=['GET'])
@api_bp.route('/api/v1/layer4/fusion/sources', methods=['GET'])
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


@api_bp.route('/api/v1/layer4/fusion', methods=['GET'])
@api_bp.route('/api/v1/policy/fusion-join', methods=['GET'])
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


@api_bp.route('/api/v1/intelligence/gap-analysis', methods=['GET'])
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


@api_bp.route('/api/v1/intelligence/hotspot-predictions', methods=['GET'])
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





@api_bp.route('/api/v1/intelligence/bigquery-analytics', methods=['GET'])
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


@api_bp.route('/api/v1/intelligence/vertex-predictions', methods=['GET'])
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



@api_bp.route('/api/v1/demand/clusters/recompute', methods=['POST'])
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


@api_bp.route('/api/v1/demand/clusters', methods=['GET'])
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

@api_bp.route('/api/v1/demand/clusters/merge', methods=['POST'])
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


@api_bp.route('/api/v1/demand/clusters/split', methods=['POST'])
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


@api_bp.route('/api/v1/demand/clusters/low-confidence', methods=['GET'])
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


@api_bp.route('/api/v1/demand/clusters/rescore-low-confidence', methods=['POST'])
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

@api_bp.route('/api/v1/demand/clusters/member-override', methods=['POST'])
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

@api_bp.route('/api/v1/demand/clusters/member-overrides/history', methods=['GET'])
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


@api_bp.route('/api/v1/demand/clusters/member-overrides/sweep-stale', methods=['POST'])
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

@api_bp.route('/api/v1/demand/clusters/member-overrides/history/export', methods=['GET'])
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


@api_bp.route('/api/v1/demand/clusters/member-overrides/metrics', methods=['GET'])
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


@api_bp.route('/api/v1/demand/clusters/member-overrides/alerts', methods=['GET'])
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


@api_bp.route('/api/v1/demand/clusters/member-overrides/backlog-summary', methods=['GET'])
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


@api_bp.route('/api/v1/federation/schema', methods=['GET'])
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


@api_bp.route('/api/v1/federation/publish', methods=['POST'])
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


@api_bp.route('/api/v1/federation/batch-publish', methods=['POST'])
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

@api_bp.route('/api/v1/policy/priority-rankings', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_priority_rankings_secure():
    from ..services.analyst_compat import rankings
    return jsonify(rankings(request.args))


@api_bp.route('/api/v1/policy/scoring-weights', methods=['GET'])
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


@api_bp.route('/api/v1/policy/scoring-weights', methods=['PUT'])
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


@api_bp.route('/api/v1/policy/scoring-profiles', methods=['GET'])
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


@api_bp.route('/api/v1/policy/scoring-profiles/activate', methods=['POST'])
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


@api_bp.route('/api/v1/policy/scoring-profiles/rollback', methods=['POST'])
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



@api_bp.route('/api/v1/policy/alignment-map', methods=['GET'])
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


@api_bp.route('/api/v1/policy/rag-brief', methods=['GET'])
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


@api_bp.route('/api/v1/policy/impact-brief', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_impact_brief_secure():
    from ..services.analyst_compat import rankings
    data=rankings(request.args)
    data['impact_metrics']={'funding_coverage_ratio':data['funded_count']/max(data['total_ranked'],1),'population_covered_estimate':None,'human_capital_npv_earnings_lakh':None}
    data['auto_brief']={'summary':'Draft scenario only. Benefits require catchment, baseline and engineering review.','human_capital_roi':data['outcomes'],
                        'recommendations':[{'project_id':p['project_id'],'title':p['title'],'review':'Verify engineering, catchment and scheme eligibility'} for p in data['items'] if p['funded_in_draft_plan']]}
    return jsonify(data)


@api_bp.route('/api/v1/policy/human-capital-roi', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def policy_human_capital_roi_secure():
    return jsonify(success=True,human_capital_roi={'status':'not_estimated','npv_earnings_uplift_lakh':None,'human_capital_irr':None,'reason':'No validated intervention cost, baseline, catchment or earnings cash-flow model.'})


@api_bp.route('/api/v1/analyst/district-responsiveness', methods=['GET'])
@require_roles('admin', 'analyst', 'auditor')
def analyst_district_responsiveness_secure():
    from ..services.analyst_workbench import responsiveness,scope_from
    data=responsiveness(scope_from(request.args))
    return jsonify(success=True,league_table=data['items'],total_districts_evaluated=len(data['items']),metadata=data['metadata'],definitions=data['definitions'])


@api_bp.route('/api/v1/policy/impact-decay', methods=['GET'])
@api_bp.route('/api/v1/demand/decay-impact', methods=['GET'])
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


@api_bp.route('/api/v1/policy/decisions/draft', methods=['POST'])
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


@api_bp.route('/api/v1/policy/decisions', methods=['GET'])
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


@api_bp.route('/api/v1/policy/decisions/<decision_id>/approve', methods=['POST'])
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


@api_bp.route('/api/v1/policy/decisions/<decision_id>/reject', methods=['POST'])
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


@api_bp.route('/api/v1/governance/consent-ledger', methods=['GET'])
@api_bp.route('/api/v1/dpdp/consent-ledger', methods=['GET'])
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


@api_bp.route('/api/v1/governance/dpdp-controls', methods=['GET'])
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


@api_bp.route('/api/v1/governance/dpdp/evidence', methods=['GET'])
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


@api_bp.route('/api/v1/governance/dpdp/readiness', methods=['GET'])
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


@api_bp.route('/api/v1/governance/dpg-status', methods=['GET'])
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

@api_bp.route('/api/public/transparency/summary', methods=['GET'])
def public_transparency_summary():
    db = get_db()
    min_group = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', 3)), 1)

    total_row = db.execute('SELECT COUNT(*) AS c, MAX(created_at) AS latest_created_at FROM citizen_requests').fetchone()
    district_rows = db.execute(
        '''
        SELECT district, COUNT(*) AS request_count
        FROM citizen_requests
        GROUP BY district
        '''
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
            },
        }
    )


@api_bp.route('/api/public/transparency/districts', methods=['GET'])
def public_transparency_districts():
    try:
        limit = int(request.args.get('limit', 100))
    except ValueError:
        return jsonify({'success': False, 'error': 'limit must be an integer'}), 400

    max_limit = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MAX_LIMIT', 200)), 1)
    limit = min(max(limit, 1), max_limit)
    min_group = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', 3)), 1)

    db = get_db()
    rows = db.execute(
        '''
        SELECT district, state, COUNT(*) AS request_count,
               SUM(CASE WHEN urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count,
               SUM(CASE WHEN urgency IN ('Emergency', 'Urgent') THEN 1 ELSE 0 END) AS high_priority_count,
               COUNT(DISTINCT category) AS category_diversity
        FROM citizen_requests
        GROUP BY district, state
        HAVING COUNT(*) >= ?
        ORDER BY request_count DESC, emergency_count DESC, district ASC
        LIMIT ?
        ''',
        (min_group, limit),
    ).fetchall()

    total_groups_row = db.execute(
        '''
        SELECT COUNT(*) AS c
        FROM (
            SELECT district
            FROM citizen_requests
            GROUP BY district
        ) grouped
        '''
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


@api_bp.route('/api/public/transparency/categories', methods=['GET'])
def public_transparency_categories():
    min_group = max(int(current_app.config.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', 3)), 1)

    db = get_db()
    rows = db.execute(
        '''
        SELECT category, COUNT(*) AS request_count,
               SUM(CASE WHEN urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count,
               COUNT(DISTINCT district) AS district_coverage
        FROM citizen_requests
        GROUP BY category
        HAVING COUNT(*) >= ?
        ORDER BY request_count DESC, category ASC
        ''',
        (min_group,),
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


@api_bp.route('/api/v1/lens/cluster/<cluster_id>', methods=['GET'])
@api_bp.route('/api/v1/lens/<role>/cluster/<cluster_id>', methods=['GET'])
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


@api_bp.route('/api/v1/transparency/verify-chain', methods=['GET'])
def verify_transparency_chain():
    result = verify_chain_integrity(limit=500)
    return jsonify({'success': True, **result})


@api_bp.route('/api/v1/auditor/project-controls', methods=['GET'])
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


@api_bp.route('/api/v1/auditor/did-proof', methods=['GET'])
@require_roles('admin', 'auditor', 'analyst')
def get_auditor_did_proof_secure():
    from ..services.causal_matching import compute_did_causal_impact
    return jsonify(success=True,did_proof=compute_did_causal_impact(district=request.args.get('district'),decision_id=request.args.get('decision_id')))



@api_bp.route('/api/v1/auditor/ai-telemetry', methods=['GET'])
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

@api_bp.route('/api/v1/futures/markets', methods=['GET'])
def get_prediction_markets():
    return jsonify(success=True,projects=[],count=0,status='research_only',notice='Fixture probabilities retired from policy decisions. No model success probabilities asserted.')


@api_bp.route('/api/v1/futures/stake', methods=['POST'])
def post_prediction_stake():
    return jsonify(success=False,error='Research staking is retired; use a documented expert review.'),410


@api_bp.route('/api/v1/futures/super-prediction', methods=['GET'])
def get_super_prediction_detail():
    return jsonify(success=False,error='Uncalibrated fixture predictions are retired.'),410


# ===== RCT-AS-A-SERVICE — STEPPED-WEDGE ADAPTIVE POLICY LAB ENDPOINTS =====
from ..services.rct_experimentation import (
    list_rct_experiments,
    compute_late_causal_effect,
)

@api_bp.route('/api/v1/rct/experiments', methods=['GET'])
def get_rct_experiments():
    return jsonify(success=True,count=0,experiments=[],status='design_only',notice='No live trial outcomes. Preregister a reviewed evaluation before collecting trial data.')


@api_bp.route('/api/v1/rct/telemetry', methods=['GET'])
def get_rct_telemetry():
    return jsonify(success=True,status='not_evaluated',high_freq_telemetry={},late_causal_lift_pct=None,mab_status_label='No adaptive rollout decisions')


# ===== ANTI-CAPTURE SATELLITE + CITIZEN TRIANGULATION ENGINE ENDPOINTS =====
from ..services.anti_capture_triangulation import (
    list_anti_capture_projects,
    compute_triangulation_divergence,
)


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
@api_bp.route('/api/v1/anti-capture/feed', methods=['GET'])
@require_roles('admin','auditor')
def get_anti_capture_feed():
    from ..services import auditor_workbench as audit
    scope=audit.scope_from(request.args)
    if scope.get('project_id'):
        result=audit.financial_review(scope['project_id'],scope)
        return jsonify(success=True,projects=[result],count=1)
    result=audit.projects(scope,limit=audit.page_limit(request.args.get('limit')))
    return jsonify(success=True,projects=result['items'],count=result['total'],notice='Select a project to inspect documented financial and site evidence. No automated fraud or financial decisions.')


@api_bp.route('/api/v1/anti-capture/satellite-signals', methods=['POST'])
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


@api_bp.route('/api/v1/anti-capture/satellite-signals/live', methods=['POST'])
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


@api_bp.route('/api/v1/anti-capture/satellite-signals/bulk', methods=['POST'])
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


@api_bp.route('/api/v1/anti-capture/satellite-signals/bulk/template', methods=['GET'])
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


@api_bp.route('/api/v1/anti-capture/satellite-signals', methods=['GET'])
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

@api_bp.route('/api/v1/anti-capture/simulate', methods=['POST'])
@require_roles('admin','auditor')
def simulate_anti_capture_divergence():
    from ..services.anti_capture_triangulation import compute_triangulation_divergence
    data=request.get_json(silent=True) or {}
    result=compute_triangulation_divergence(str(data.get('project_id') or 'illustrative'),data.get('pfms_disbursed_pct',90),
        data.get('satellite_progress_pct',25),data.get('citizen_complaint_pct',85),data.get('gemini_defect_score',.85))
    return jsonify(success=True,triangulation_result=result)


@api_bp.route('/api/v1/anti-capture/dispatch-audit', methods=['POST'])
@require_roles('admin','auditor')
def dispatch_third_party_audit():
    from ..services import auditor_actions, auditor_workbench as audit
    data=request.get_json(silent=True) or {}
    result=auditor_actions.create_case(data,audit.scope_from(request.args))
    return jsonify(success=True,case=result,dispatch_status='REVIEW_CASE_CREATED',disbursement_state='NO_FINANCIAL_ACTION'),201


@api_bp.route('/api/v1/policy/sps-rankings', methods=['GET'])
def get_social_priority_score_rankings():
    from ..services.analyst_compat import priorities
    return jsonify(priorities(request.args))


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


@api_bp.route('/api/v1/ops/reliability-dashboard', methods=['GET'])
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


@api_bp.route('/api/v1/ops/alerts/evaluate', methods=['GET'])
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


@api_bp.route('/api/v1/ops/alerts/route', methods=['POST'])
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


@api_bp.route('/api/v1/ops/webhooks/verify-sample', methods=['POST'])
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


@api_bp.route('/api/v1/ops/routing/status', methods=['GET'])
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


@api_bp.route('/api/v1/ops/report/daily', methods=['GET'])
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


@api_bp.route('/api/v1/ops/cloud-ingestion-health', methods=['GET'])
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


@api_bp.route('/api/emergency/acknowledge', methods=['POST'])
@api_bp.route('/emergency/acknowledge', methods=['POST'])
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


@api_bp.route('/api/emergency/dispatch-status/<request_id>', methods=['GET'])
@api_bp.route('/emergency/dispatch-status/<request_id>', methods=['GET'])
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


@api_bp.route('/api/emergency/check-escalations', methods=['POST'])
@api_bp.route('/emergency/check-escalations', methods=['POST'])
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


@api_bp.errorhandler(ValueError)
def analyst_compatible_validation_error(error):
    return jsonify(success=False,error=str(error)),400
