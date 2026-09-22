import json
import random
import re
import secrets
import time
from datetime import datetime, timezone, timedelta

from flask import current_app

from ..db import get_db
from ..security import hash_api_token
from .ai_simulation import simulate_gemini_intent_classification, simulate_translation
from .code_mix import normalize_code_mix
from .routing import resolve_ward_department_route
from .sla import resolve_sla_policy
from .clustering import assign_request_to_cluster
from .pii_scrubber import scrub_text


def _generate_request_id():
    date_str = datetime.now(timezone.utc).strftime('%Y%m%d')
    db = get_db()
    while True:
        suffix = secrets.token_hex(2).upper()
        rid = f"NVB-{date_str}{suffix}"
        row = db.execute("SELECT request_id FROM citizen_requests WHERE request_id = ? LIMIT 1", (rid,)).fetchone()
        if not row:
            return rid


def _district_geo(repo, district):
    row = repo.get_district_row(district)
    if row is None:
        return 20.5937, 78.9629, 'Unknown'
    lat = float(row['lat']) + random.uniform(-0.03, 0.03)
    lng = float(row['lng']) + random.uniform(-0.03, 0.03)
    return round(lat, 4), round(lng, 4), str(row['state'])


def _get_ai_mode():
    google_client = current_app.extensions.get('google_ai_client')
    return 'google_ai_configured' if google_client else 'simulation'


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
    if 'simulated' in model or 'fallback' in model:
        return False
    return bool(model)


def _extract_model_trace(classification=None, translation=None):
    return {
        'classification_model_id': str(((classification or {}).get('model') or '')).strip(),
        'translation_model_id': str(((translation or {}).get('model') or '')).strip(),
        'classification_provider_mode': str(((classification or {}).get('provider_mode') or '')).strip(),
        'translation_provider_mode': str(((translation or {}).get('provider_mode') or '')).strip(),
    }


def _run_translation(text, source_lang, target_lang='en'):
    if source_lang == target_lang:
        return {
            'translated_text': text,
            'source_language': source_lang,
            'target_language': target_lang,
            'model': 'VisBharat-Translation-NoOp'
        }
    google_client = current_app.extensions.get('google_ai_client')
    if google_client:
        try:
            out = google_client.translate_text(text, source_lang, target_lang)
            if _jury_live_models_required() and not _is_live_model_result(out):
                raise ValueError('live translation provider returned fallback/simulated output')
            return out
        except Exception:
            pass
    if _jury_live_models_required():
        raise ValueError('live translation model required for jury path; fallback disabled')
    return simulate_translation(text, source_lang, target_lang)


def _run_classification(text, language):
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



FAST_PATH_EMERGENCY_PATTERNS = [
    # Electrical hazards
    (re.compile(r'\b(live wire|broken wire|falling wire|hanging wire|sparking|electric shock|transformer (blast|fire|explosion)|electrocution)\b', re.IGNORECASE), 'Electricity', 'High-voltage electrical hazard'),
    (re.compile(r'(மின்சாரக் கம்பி|மின்கம்பி அறுந்து|கரண்ட் கம்பி|மின் கம்பி அறுந்து|தீப்பொறி|மின் அதிர்ச்சி)'), 'Electricity', 'High-voltage electrical hazard (Tamil)'),
    (re.compile(r'(విద్యుత్ తీగ|కరెంట్ తీగ|ట్రాన్స్‌ఫార్మర్ పేలుడు)'), 'Electricity', 'High-voltage electrical hazard (Telugu)'),
    (re.compile(r'(बिजली का तार|करंट लग|ट्रांसफार्मर ब्लास्ट)'), 'Electricity', 'High-voltage electrical hazard (Hindi)'),

    # Gas / Fire / Explosion
    (re.compile(r'\b(gas leak|cylinder (blast|leak|explosion)|pipeline (leak|blast)|fire outbreak|massive fire|chemical leak)\b', re.IGNORECASE), 'Sanitation', 'Gas / Fire hazard'),
    (re.compile(r'(கேஸ் கசிவு|சிலிண்டர் வெடிப்பு|தீ விபத்து|நச்சு வாயு)'), 'Sanitation', 'Gas / Fire hazard (Tamil)'),
    (re.compile(r'(గ్యాస్ లీకేజీ|సిలిండర్ పేలుడు|అగ్ని ప్రమాదం)'), 'Sanitation', 'Gas / Fire hazard (Telugu)'),
    (re.compile(r'(गैस रिसाव|सिलेंडर ब्लास्ट|आग लग)'), 'Sanitation', 'Gas / Fire hazard (Hindi)'),

    # Structural collapse / Breach
    (re.compile(r'\b(bridge collaps(e|ed)|building collaps(e|ed)|wall collaps(e|ed)|dam breach|canal breach|flash flood|drowning)\b', re.IGNORECASE), 'Road', 'Structural collapse / breach'),
    (re.compile(r'(பாலம் இடிந்து|கட்டிடம் இடிந்து|சுவர் இடிந்து|வெள்ளப்பெருக்கு)'), 'Road', 'Structural collapse (Tamil)'),
    (re.compile(r'(వంతెన కూలిపోయింది|భవనం కూలిపోయింది|గోడ కూలిపోయింది)'), 'Road', 'Structural collapse (Telugu)'),
    (re.compile(r'(पुल गिर गया|मकान गिर गया|दीवार ढह गई)'), 'Road', 'Structural collapse (Hindi)'),

    # Severe water poisoning / Contamination
    (re.compile(r'\b(poisoned water|toxic water|cholera outbreak|contamination in drinking water)\b', re.IGNORECASE), 'Water Supply', 'Severe water contamination'),
    (re.compile(r'(விஷ நீர்|குடிநீரில் சாக்கடை கலப்பு|குடிநீர் நச்சு)'), 'Water Supply', 'Severe water contamination (Tamil)'),
    (re.compile(r'(విషపూరిత నీరు|తాగునీటిలో కాలుష్యం)'), 'Water Supply', 'Severe water contamination (Telugu)'),
]


def fast_path_emergency_triage(text: str, language: str = 'en') -> dict | None:
    if not text:
        return None
    t0 = time.perf_counter()
    raw = str(text).strip()
    for pattern, category, label in FAST_PATH_EMERGENCY_PATTERNS:
        if pattern.search(raw):
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            return {
                'category': category,
                'urgency': 'Emergency',
                'sentiment': 'Very Negative',
                'confidence': 0.99,
                'requires_human_review': False,
                'fast_path_triggered': True,
                'emergency_hazard': label,
                'triage_latency_ms': max(elapsed_ms, 0.001),
                'provider_mode': 'fast_path_deterministic',
                'model': 'VisBharat-Deterministic-Safety-FastPath-v1',
            }
    return None


def _compute_payload_idempotency_key(payload: dict):
    normalized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return str(abs(hash(normalized)))


def process_ingestion_payload(payload: dict):
    text_result = scrub_text(payload.get('text') or '')
    text = str(text_result.get('scrubbed') or '').strip()
    language = (payload.get('language') or 'en').strip().lower() or 'en'
    district = (payload.get('district') or '').strip()
    channel = (payload.get('channel') or 'Unknown').strip() or 'Unknown'
    sender = str(scrub_text(payload.get('sender') or 'anonymous').get('scrubbed') or 'anonymous').strip() or 'anonymous'

    if not text:
        raise ValueError('text is required')

    repo = current_app.extensions['reference_repo']
    if not district:
        district = str(repo.df_districts.iloc[0]['district'])

    code_mix = normalize_code_mix(text, language)
    normalized_text = str(code_mix.get('normalized_text') or text).strip() or text

    # 1. Fast-Path Deterministic Triage (<5ms) BEFORE any LLM/translation call
    # Life-safety emergencies in Tamil, Telugu, Hindi, and English must never wait on network/LLM roundtrips
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
        translation = _run_translation(normalized_text, language)
        classification = _run_classification(normalized_text, language)

    lat, lng, state = _district_geo(repo, district)
    route = resolve_ward_department_route(
        category=classification['category'],
        district=district,
        state=state,
        text=text,
        ward=(payload.get('ward') or ''),
        matrix=current_app.config.get('WARD_ROUTING_MATRIX', {}),
        category_to_service=current_app.config.get('CATEGORY_TO_SERVICE', {}),
        service_to_department=current_app.config.get('SERVICE_TO_DEPARTMENT', {}),
    )
    sla_policy = resolve_sla_policy(
        urgency=classification['urgency'],
        category=classification['category'],
        channel=channel,
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
        'stt_mode': 'simulation',
        'ingestion_channel': channel,
        'translation': translation,
        'code_mix_normalization': code_mix,
        'classification': classification,
        'fast_path_triage': fast_path,
        'model_trace': _extract_model_trace(classification=classification, translation=translation),
        'routing': route,
        'sla_policy': sla_policy,
        'pipeline': payload.get('pipeline', {}),
        'pii_scrub': {'changed': bool(text_result.get('changed')), 'risk_level': text_result.get('risk_level'), 'rules_triggered': text_result.get('rules_triggered', [])},
    }

    is_emergency = (classification.get('urgency') == 'Emergency')
    emergency_status = 'dispatched' if is_emergency else 'none'
    if is_emergency:
        dept = route.get('department') or 'District Disaster Management Authority (DDMA)'
        dispatched_to = f"{dept} & District Disaster Management Authority (DDMA)" if 'Disaster' not in dept else dept
    else:
        dispatched_to = None
    dispatched_at = now if is_emergency else None
    # Store only a digest.  The one-time raw capability is returned to the
    # trusted caller, never persisted in the request metadata or database.
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
            channel,
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
            sender,
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
    db.commit()

    # 1. Direct Real-Time BigQuery Streaming Ingestion
    bigquery_client = current_app.extensions.get('google_bigquery_client')
    ingest_payload = {
        'request_id': request_id,
        'district': district,
        'state': state,
        'urgency': classification.get('urgency'),
        'category': classification.get('category'),
        'source_channel': channel,
        'created_at': now,
        'input_language': language,
        'original_text': text,
        'translated_text': translation.get('translated_text'),
        'sentiment': classification.get('sentiment'),
        'status': 'New',
        'lat': lat,
        'lng': lng,
    }
    replicated_bq = 0
    replicated_ps = 0
    rep_errors = []

    if bigquery_client:
        try:
            bigquery_client.insert_request(ingest_payload)
            replicated_bq = 1
        except Exception as bq_err:
            current_app.logger.warning(f"BigQuery real-time streaming ingestion fallback: {bq_err}")
            rep_errors.append(f"BigQuery: {bq_err}")
    else:
        rep_errors.append("BigQuery: client unconfigured")

    # 2. Event-Driven GCP Pub/Sub -> Cloud Function -> BigQuery Pipeline
    pubsub_client = current_app.extensions.get('google_pubsub_client')
    if pubsub_client:
        try:
            msg_id = pubsub_client.publish_request(ingest_payload)
            current_app.logger.info(f"Published complaint {request_id} to Pub/Sub topic {pubsub_client.topic_id}, msg_id={msg_id}")
            replicated_ps = 1
        except Exception as ps_err:
            current_app.logger.warning(f"GCP Pub/Sub event publishing fallback: {ps_err}")
            rep_errors.append(f"PubSub: {ps_err}")
    else:
        rep_errors.append("PubSub: client unconfigured")

    rep_err_str = "; ".join(rep_errors) if rep_errors else None
    rep_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z') if (replicated_bq or replicated_ps) else None

    try:
        db.execute(
            '''
            UPDATE citizen_requests
            SET replicated_bigquery = ?,
                replicated_pubsub = ?,
                replication_error = ?,
                replicated_at = ?
            WHERE request_id = ?
            ''',
            (replicated_bq, replicated_ps, rep_err_str, rep_at, request_id),
        )
        db.commit()
    except Exception as db_rep_err:
        current_app.logger.error(f"Failed to update outbox replication status for {request_id}: {db_rep_err}")

    cluster = assign_request_to_cluster(
        request_id=request_id,
        state=state,
        category=classification.get('category'),
        text=(translation.get('translated_text') or text),
    )

    return {
        'request_id': request_id,
        'channel': channel,
        'classification': classification,
        'model_trace': _extract_model_trace(classification=classification, translation=translation),
        'routing': route,
        'cluster': cluster,
        'sla': {'due_at': sla_due_at, 'breached_at': None, 'escalation_level': 0, 'policy_mode': sla_policy.get('policy_mode'), 'tier1_target': sla_policy.get('tier1_target', {}), 'tier2_delay_hours': sla_policy.get('tier2_delay_hours'), 'tier2_target': sla_policy.get('tier2_target', {})},
        'district': district,
        'emergency_dispatch': ai_metadata.get('emergency_dispatch'),
        # This capability is intentionally available only to the trusted
        # service caller. Public HTTP responses must never echo it.
        'emergency_acknowledgement_token': ack_token,
    }


def find_job_by_idempotency_key(idempotency_key: str):
    if not idempotency_key:
        return None
    db = get_db()
    row = db.execute(
        '''
        SELECT job_id, status
        FROM processing_jobs
        WHERE idempotency_key = ?
        ORDER BY id DESC
        LIMIT 1
        ''',
        (idempotency_key,),
    ).fetchone()
    return dict(row) if row else None


def enqueue_ingestion_job(payload: dict, idempotency_key: str = ''):
    db = get_db()
    now = int(time.time())
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    job_id = f"JOB-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{random.randint(100000, 999999)}"
    idempotency_key = (idempotency_key or '').strip() or _compute_payload_idempotency_key(payload)

    existing = find_job_by_idempotency_key(idempotency_key)
    if existing:
        return existing['job_id']

    db.execute(
        '''
        INSERT INTO processing_jobs (
            job_id, job_type, channel, payload_json, idempotency_key, status,
            attempt_count, max_retries, next_attempt_at_epoch,
            last_error, result_json, created_at, updated_at, started_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            job_id,
            'ingestion',
            (payload.get('channel') or 'Unknown'),
            json.dumps(payload),
            idempotency_key,
            'queued',
            0,
            int(current_app.config.get('PIPELINE_MAX_RETRIES', 5)),
            now,
            None,
            None,
            now_iso,
            now_iso,
            None,
            None,
        ),
    )
    db.commit()
    return job_id


def get_pipeline_job(job_id: str):
    db = get_db()
    row = db.execute(
        '''
        SELECT job_id, job_type, channel, idempotency_key, status, attempt_count, max_retries,
               next_attempt_at_epoch, last_error, result_json, created_at,
               updated_at, started_at, completed_at
        FROM processing_jobs WHERE job_id = ?
        ''',
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    obj = dict(row)
    if obj.get('result_json'):
        try:
            obj['result'] = json.loads(obj['result_json'])
        except Exception:
            obj['result'] = None
    else:
        obj['result'] = None
    return obj


def list_pipeline_jobs(limit=100):
    db = get_db()
    rows = db.execute(
        '''
        SELECT job_id, job_type, channel, idempotency_key, status, attempt_count, max_retries,
               next_attempt_at_epoch, last_error, result_json, created_at,
               updated_at, started_at, completed_at
        FROM processing_jobs
        ORDER BY id DESC
        LIMIT ?
        ''',
        (min(int(limit), 500),),
    ).fetchall()
    return [dict(r) for r in rows]


def retry_pipeline_job(job_id: str):
    db = get_db()
    row = db.execute('SELECT job_id, status FROM processing_jobs WHERE job_id = ?', (job_id,)).fetchone()
    if row is None:
        return None, 'job not found'
    if row['status'] != 'dead_letter':
        return None, 'only dead_letter jobs can be retried'

    now = int(time.time())
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    db.execute(
        "UPDATE processing_jobs SET status = 'retry', next_attempt_at_epoch = ?, updated_at = ?, last_error = NULL WHERE job_id = ?",
        (now, now_iso, job_id),
    )
    db.commit()
    return {'job_id': job_id, 'status': 'retry'}, None


def cancel_pipeline_job(job_id: str):
    db = get_db()
    row = db.execute('SELECT job_id, status FROM processing_jobs WHERE job_id = ?', (job_id,)).fetchone()
    if row is None:
        return None, 'job not found'
    if row['status'] in ('succeeded', 'dead_letter', 'canceled'):
        return None, f"cannot cancel job in status {row['status']}"

    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    db.execute(
        "UPDATE processing_jobs SET status = 'canceled', updated_at = ?, completed_at = ? WHERE job_id = ?",
        (now_iso, now_iso, job_id),
    )
    db.commit()
    return {'job_id': job_id, 'status': 'canceled'}, None


def get_pipeline_metrics():
    db = get_db()
    status_rows = db.execute(
        '''
        SELECT status, COUNT(*) AS c
        FROM processing_jobs
        GROUP BY status
        '''
    ).fetchall()

    attempts_row = db.execute(
        'SELECT AVG(attempt_count) AS avg_attempts, MAX(attempt_count) AS max_attempts FROM processing_jobs'
    ).fetchone()

    by_status = {r['status']: int(r['c']) for r in status_rows}
    return {
        'total_jobs': int(sum(by_status.values())),
        'by_status': by_status,
        'avg_attempts': float(attempts_row['avg_attempts'] or 0.0),
        'max_attempts': int(attempts_row['max_attempts'] or 0),
    }


def run_pipeline_once():
    db = get_db()
    now = int(time.time())
    row = db.execute(
        '''
        SELECT id, job_id, payload_json, attempt_count, max_retries
        FROM processing_jobs
        WHERE status IN ('queued', 'retry') AND next_attempt_at_epoch <= ?
        ORDER BY id ASC
        LIMIT 1
        ''',
        (now,),
    ).fetchone()
    if row is None:
        return None

    job_id = row['job_id']
    attempt = int(row['attempt_count']) + 1
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    db.execute(
        "UPDATE processing_jobs SET status = 'processing', attempt_count = ?, started_at = ?, updated_at = ? WHERE job_id = ?",
        (attempt, now_iso, now_iso, job_id),
    )
    db.commit()

    try:
        payload = json.loads(row['payload_json'])
        result = process_ingestion_payload(payload)
        done_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        db.execute(
            "UPDATE processing_jobs SET status = 'succeeded', result_json = ?, completed_at = ?, updated_at = ? WHERE job_id = ?",
            (json.dumps(result), done_at, done_at, job_id),
        )
        db.commit()
        return {'job_id': job_id, 'status': 'succeeded', 'result': result}
    except Exception as err:
        max_retries = int(row['max_retries'])
        backoff = int(current_app.config.get('PIPELINE_RETRY_BACKOFF_SECONDS', 30))
        next_epoch = int(time.time()) + backoff
        status = 'dead_letter' if attempt >= max_retries else 'retry'
        db.execute(
            "UPDATE processing_jobs SET status = ?, last_error = ?, next_attempt_at_epoch = ?, updated_at = ? WHERE job_id = ?",
            (status, str(err), next_epoch, datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'), job_id),
        )
        db.commit()
        return {'job_id': job_id, 'status': status, 'error': str(err)}


def ensure_worker_heartbeat_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS processing_worker_heartbeats (
                id SERIAL PRIMARY KEY,
                worker_id TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                pid INTEGER,
                last_seen_at_epoch BIGINT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_error TEXT,
                metadata_json TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS processing_worker_heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_id TEXT UNIQUE NOT NULL,
                status TEXT NOT NULL,
                pid INTEGER,
                last_seen_at_epoch INTEGER NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_error TEXT,
                metadata_json TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def upsert_worker_heartbeat(worker_id: str, status: str, pid=None, last_error=None, metadata=None):
    db = get_db()
    ensure_worker_heartbeat_table()

    now_epoch = int(time.time())
    now_iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    meta_json = json.dumps(metadata or {})

    row = db.execute('SELECT worker_id FROM processing_worker_heartbeats WHERE worker_id = ?', (worker_id,)).fetchone()
    if row is None:
        db.execute(
            '''
            INSERT INTO processing_worker_heartbeats (
                worker_id, status, pid, last_seen_at_epoch, last_seen_at, last_error, metadata_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (worker_id, status, pid, now_epoch, now_iso, last_error, meta_json, now_iso),
        )
    else:
        db.execute(
            '''
            UPDATE processing_worker_heartbeats
            SET status = ?, pid = ?, last_seen_at_epoch = ?, last_seen_at = ?,
                last_error = ?, metadata_json = ?, updated_at = ?
            WHERE worker_id = ?
            ''',
            (status, pid, now_epoch, now_iso, last_error, meta_json, now_iso, worker_id),
        )
    db.commit()


def list_worker_heartbeats(limit=100):
    ensure_worker_heartbeat_table()
    db = get_db()
    rows = db.execute(
        '''
        SELECT worker_id, status, pid, last_seen_at_epoch, last_seen_at, last_error, metadata_json, updated_at
        FROM processing_worker_heartbeats
        ORDER BY updated_at DESC
        LIMIT ?
        ''',
        (min(int(limit), 500),),
    ).fetchall()

    out = []
    for row in rows:
        obj = dict(row)
        try:
            obj['metadata'] = json.loads(obj.get('metadata_json') or '{}')
        except Exception:
            obj['metadata'] = {}
        obj.pop('metadata_json', None)
        out.append(obj)
    return out







def replicate_pending_outbox(limit: int = 50, worker_id: str | None = None, max_retries: int = 5) -> dict:
    """
    Enterprise Transactional Outbox Worker:
    - Atomically acquires worker leases with lease timeout (60s).
    - Checks exponential backoff eligibility (next_replication_attempt_at <= now).
    - Attempts BigQuery streaming ingestion and Pub/Sub event publishing.
    - Routes permanently failing records to Durable DLQ (cloud_ingestion_dead_letters) after max_retries.
    - Updates attempt count and next exponential backoff timestamp.
    """
    db = get_db()
    worker = str(worker_id or f"outbox-worker-{secrets.token_hex(4)}")
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat().replace('+00:00', 'Z')
    lease_expires_iso = (now_dt + timedelta(seconds=60)).isoformat().replace('+00:00', 'Z')

    # 1. Lease eligible candidates atomically
    rows = db.execute(
        '''
        SELECT request_id, district, state, category, urgency, source_channel,
               input_language, original_text, translated_text, sentiment, lat, lng, created_at,
               replicated_bigquery, replicated_pubsub, replication_attempt_count
        FROM citizen_requests
        WHERE (replicated_bigquery = 0 OR replicated_pubsub = 0)
          AND (next_replication_attempt_at IS NULL OR next_replication_attempt_at <= ?)
          AND (replication_locked_at IS NULL OR replication_locked_at < ?)
          AND (replication_attempt_count < ?)
        ORDER BY id ASC
        LIMIT ?
        ''',
        (now_iso, now_iso, max_retries, min(int(limit), 200)),
    ).fetchall()

    if not rows:
        return {
            'worker_id': worker,
            'inspected': 0,
            'fully_replicated': 0,
            'dlq_exhausted': 0,
            'errors': [],
        }

    candidate_ids = [r['request_id'] for r in rows]
    for cid in candidate_ids:
        db.execute(
            '''
            UPDATE citizen_requests
            SET replication_locked_by = ?,
                replication_locked_at = ?
            WHERE request_id = ?
            ''',
            (worker, lease_expires_iso, cid),
        )
    db.commit()

    bigquery_client = current_app.extensions.get('google_bigquery_client')
    pubsub_client = current_app.extensions.get('google_pubsub_client')

    replicated_count = 0
    dlq_count = 0
    errors = []

    for row in rows:
        req_id = row['request_id']
        attempt = int(row['replication_attempt_count'] or 0) + 1
        payload = {
            'request_id': req_id,
            'district': row['district'],
            'state': row['state'],
            'urgency': row['urgency'],
            'category': row['category'],
            'source_channel': row['source_channel'],
            'created_at': row['created_at'],
            'input_language': row['input_language'],
            'original_text': row['original_text'],
            'translated_text': row['translated_text'],
            'sentiment': row['sentiment'],
            'status': 'New',
            'lat': row['lat'],
            'lng': row['lng'],
        }

        replicated_bq = row['replicated_bigquery']
        replicated_ps = row['replicated_pubsub']
        item_errors = []

        if not replicated_bq:
            if bigquery_client:
                try:
                    bigquery_client.insert_request(payload)
                    replicated_bq = 1
                except Exception as bq_err:
                    item_errors.append(f"BigQuery: {bq_err}")
            else:
                item_errors.append("BigQuery: client unconfigured")

        if not replicated_ps:
            if pubsub_client:
                try:
                    pubsub_client.publish_request(payload)
                    replicated_ps = 1
                except Exception as ps_err:
                    item_errors.append(f"PubSub: {ps_err}")
            else:
                item_errors.append("PubSub: client unconfigured")

        err_str = "; ".join(item_errors) if item_errors else None

        if replicated_bq and replicated_ps:
            db.execute(
                '''
                UPDATE citizen_requests
                SET replicated_bigquery = 1,
                    replicated_pubsub = 1,
                    replication_error = NULL,
                    replicated_at = ?,
                    replication_locked_by = NULL,
                    replication_locked_at = NULL
                WHERE request_id = ?
                ''',
                (now_iso, req_id),
            )
            replicated_count += 1
        else:
            if attempt >= max_retries:
                # Move to durable DLQ table
                try:
                    db.execute(
                        '''
                        INSERT OR IGNORE INTO cloud_ingestion_dead_letters (
                            request_id, district, state, source_channel, payload_json, error_message, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (
                            req_id,
                            row['district'],
                            row['state'],
                            row['source_channel'],
                            json.dumps(payload),
                            err_str,
                            'dlq_exhausted',
                            now_iso,
                        ),
                    )
                except Exception as dlq_err:
                    current_app.logger.error(f"Failed to insert into DLQ for {req_id}: {dlq_err}")

                dlq_msg = f"DLQ_EXHAUSTED: Exceeded {max_retries} attempts. {err_str}"
                db.execute(
                    '''
                    UPDATE citizen_requests
                    SET replication_attempt_count = ?,
                        replication_error = ?,
                        next_replication_attempt_at = NULL,
                        replication_locked_by = NULL,
                        replication_locked_at = NULL
                    WHERE request_id = ?
                    ''',
                    (attempt, dlq_msg, req_id),
                )
                dlq_count += 1
            else:
                # Exponential backoff (e.g. attempt 1 = 10s, attempt 2 = 20s, attempt 3 = 40s...)
                backoff_seconds = min(300, (2 ** attempt) * 5)
                next_attempt = (now_dt + timedelta(seconds=backoff_seconds)).isoformat().replace('+00:00', 'Z')
                db.execute(
                    '''
                    UPDATE citizen_requests
                    SET replication_attempt_count = ?,
                        replication_error = ?,
                        next_replication_attempt_at = ?,
                        replication_locked_by = NULL,
                        replication_locked_at = NULL
                    WHERE request_id = ?
                    ''',
                    (attempt, err_str, next_attempt, req_id),
                )

            if item_errors:
                errors.append(f"{req_id} (attempt {attempt}): {err_str}")

    db.commit()
    return {
        'worker_id': worker,
        'inspected': len(rows),
        'fully_replicated': replicated_count,
        'dlq_exhausted': dlq_count,
        'errors': errors[:10],
    }


def get_outbox_replication_metrics() -> dict:
    """
    Returns aggregate outbox metrics, worker leases, DLQ backlog, and SLO age alerting.
    """
    db = get_db()
    now_dt = datetime.now(timezone.utc)
    total_row = db.execute('SELECT COUNT(*) as count FROM citizen_requests').fetchone()
    total = total_row['count'] if total_row else 0

    bq_row = db.execute('SELECT COUNT(*) as count FROM citizen_requests WHERE replicated_bigquery = 1').fetchone()
    bq_count = bq_row['count'] if bq_row else 0

    ps_row = db.execute('SELECT COUNT(*) as count FROM citizen_requests WHERE replicated_pubsub = 1').fetchone()
    ps_count = ps_row['count'] if ps_row else 0

    last_error_row = db.execute(
        'SELECT replication_error FROM citizen_requests WHERE replication_error IS NOT NULL ORDER BY id DESC LIMIT 1'
    ).fetchone()
    last_error = last_error_row['replication_error'] if last_error_row else None

    # DLQ count
    dlq_row = db.execute("SELECT COUNT(*) as count FROM cloud_ingestion_dead_letters WHERE status = 'dlq_exhausted'").fetchone()
    dlq_count = dlq_row['count'] if dlq_row else 0

    # Oldest pending age & SLO calculation (SLO: 300 seconds / 5 mins)
    oldest_pending_row = db.execute(
        '''
        SELECT created_at FROM citizen_requests
        WHERE (replicated_bigquery = 0 OR replicated_pubsub = 0)
        ORDER BY created_at ASC
        LIMIT 1
        '''
    ).fetchone()

    oldest_age_seconds = 0.0
    slo_target_seconds = 300.0
    slo_breached = False
    slo_alert = None

    if oldest_pending_row and oldest_pending_row['created_at']:
        try:
            created_raw = oldest_pending_row['created_at'].replace('Z', '+00:00')
            created_dt = datetime.fromisoformat(created_raw)
            oldest_age_seconds = max(0.0, (now_dt - created_dt).total_seconds())
            if oldest_age_seconds > slo_target_seconds:
                slo_breached = True
                slo_alert = (
                    f"Outbox Replication SLO Alert: Oldest unreplicated request is {oldest_age_seconds:.1f}s old "
                    f"(exceeds {slo_target_seconds:.0f}s threshold). Review Cloud Pub/Sub and BigQuery connectivity."
                )
        except Exception:
            pass

    pending_bq = total - bq_count
    pending_ps = total - ps_count
    status = 'healthy' if (pending_bq == 0 and pending_ps == 0) else ('lagging' if (bq_count > 0 or ps_count > 0) else 'queued_outbox')

    return {
        'total_requests': total,
        'replicated_bigquery': bq_count,
        'pending_bigquery': pending_bq,
        'replicated_pubsub': ps_count,
        'pending_pubsub': pending_ps,
        'replication_status': status,
        'dlq_exhausted_count': dlq_count,
        'oldest_pending_age_seconds': round(oldest_age_seconds, 2),
        'slo_target_seconds': slo_target_seconds,
        'slo_breached': slo_breached,
        'slo_alert': slo_alert,
        'last_error': last_error,
    }
