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

@api_bp.errorhandler(ValueError)
def analyst_compatible_validation_error(error):
    return jsonify(success=False,error=str(error)),400

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


def _apply_sla_policy_if_needed(*args, **kwargs):
    from .api_intake import _apply_sla_policy_if_needed_impl
    return _apply_sla_policy_if_needed_impl(*args, **kwargs)


# Export all symbols (including private helper functions) for child blueprints
__all__ = [k for k in list(globals().keys()) if not (k.startswith('__') and k.endswith('__'))]

# ==============================================================================
# Domain Blueprint Registrations
# ==============================================================================
from .api_channels import channels_bp
from .api_intake import intake_bp, _submit_complaint
from .api_intelligence import intelligence_bp
from .api_governance import governance_bp
from .api_ops import ops_bp

api_bp.register_blueprint(channels_bp)
api_bp.register_blueprint(intake_bp)
api_bp.register_blueprint(intelligence_bp)
api_bp.register_blueprint(governance_bp)
api_bp.register_blueprint(ops_bp)

