import hashlib
import json
import math
import random
from datetime import datetime, timezone

from flask import current_app

from ..db import get_db, ensure_consent_ledger_table


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _subject_hash(subject: str):
    value = str(subject or '').strip().lower()
    if not value:
        return ''
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]


def record_consent_event(
    request_id: str,
    channel: str,
    consent_scope: str,
    consent_granted: bool,
    language: str = 'en',
    actor: str = 'anonymous',
    legal_basis: str = 'consent',
    metadata=None,
    commit=True,
):
    rid = str(request_id or '').strip()
    if not rid:
        return None

    if not isinstance(consent_granted,bool):
        raise ValueError('consent_granted must be an explicit boolean')
    db = get_db()
    event_id = f"CONSENT-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    now = _now_iso()
    details = metadata or {}
    # Isolate unrelated anonymous submissions rather than giving everyone one ID.
    subject_ref = _subject_hash(str(details.get('subject') or rid))

    db.execute(
        '''
        INSERT INTO consent_ledger (
            event_id, request_id, channel, language, consent_scope,
            consent_granted, legal_basis, actor, subject_ref, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            event_id,
            rid,
            str(channel or '').strip() or 'web',
            str(language or 'en').strip() or 'en',
            str(consent_scope or '').strip() or 'request_processing',
            1 if bool(consent_granted) else 0,
            str(legal_basis or 'consent').strip() or 'consent',
            str(actor or 'anonymous').strip() or 'anonymous',
            subject_ref,
            json.dumps(details),
            now,
        ),
    )
    event_state=details.get('event_state') or ('granted' if consent_granted else 'declined')
    if event_state in ('withdrawn','declined','granted','other_basis'):
        db.execute('''INSERT INTO auditor_processing_restrictions(request_id,purpose,state,receipt_id,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(request_id,purpose) DO UPDATE SET state=excluded.state,
            receipt_id=excluded.receipt_id,updated_at=excluded.updated_at''',
            (rid,consent_scope,'restricted' if event_state in ('withdrawn','declined') else 'documented_basis',event_id,now))
    if commit:
        db.commit()
    return {'event_id': event_id, 'request_id': rid, 'consent_granted': bool(consent_granted)}


def list_consent_events(limit: int = 100, request_id: str = ''):
    safe_limit = min(max(int(limit or 100), 1), 500)
    ensure_consent_ledger_table()
    db = get_db()

    if str(request_id or '').strip():
        rows = db.execute(
            '''
            SELECT event_id, request_id, channel, language, consent_scope,
                   consent_granted, legal_basis, actor, subject_ref, metadata_json, created_at
            FROM consent_ledger
            WHERE request_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            ''',
            (str(request_id).strip(), safe_limit),
        ).fetchall()
    else:
        rows = db.execute(
            '''
            SELECT event_id, request_id, channel, language, consent_scope,
                   consent_granted, legal_basis, actor, subject_ref, metadata_json, created_at
            FROM consent_ledger
            ORDER BY created_at DESC
            LIMIT ?
            ''',
            (safe_limit,),
        ).fetchall()

    items = []
    for row in rows:
        metadata = {}
        try:
            metadata = json.loads(row['metadata_json'] or '{}')
        except Exception:
            metadata = {}
        items.append(
            {
                'event_id': row['event_id'],
                'request_id': row['request_id'],
                'channel': row['channel'],
                'language': row['language'],
                'consent_scope': row['consent_scope'],
                'consent_granted': bool(row['consent_granted']),
                'legal_basis': row['legal_basis'],
                'actor': row['actor'],
                'subject_ref': row['subject_ref'],
                'metadata': metadata,
                'created_at': row['created_at'],
            }
        )

    return {'items': items, 'limit': safe_limit}


def _laplace_noise(scale: float):
    u = random.random() - 0.5
    return -scale * math.copysign(math.log(1 - 2 * abs(u)), u)


def privatize_count(value: int, epsilon: float = 0.75, min_value: int = 0):
    if epsilon <= 0:
        return max(int(value or 0), min_value)
    noisy = int(round(float(value or 0) + _laplace_noise(1.0 / epsilon)))
    return max(noisy, min_value)


def should_apply_dp():
    return bool(current_app.config.get('PUBLIC_TRANSPARENCY_DP_ENABLED', False))


def dp_metadata():
    return {
        'enabled': should_apply_dp(),
        'epsilon': float(current_app.config.get('PUBLIC_TRANSPARENCY_DP_EPSILON', 0.75) or 0.75),
        'mechanism': 'laplace',
    }


def get_dpdp_controls():
    controls = current_app.config.get('DPDP_CONTROL_MATRIX', {}) or {}
    if not isinstance(controls, dict):
        controls = {}
    return {
        'version': str(current_app.config.get('DPDP_CONTROL_MAPPING_VERSION', 'dpdp-2023-v1')),
        'controls': controls,
        'evidence_path': str(current_app.config.get('DPDP_EVIDENCE_ARTIFACT_PATH', 'docs/reports/DPDP_Control_Evidence.md')),
    }


def get_dpdp_evidence():
    evidence = current_app.config.get('DPDP_EVIDENCE_ARTIFACTS', []) or []
    if not isinstance(evidence, list):
        evidence = []
    return {
        'version': str(current_app.config.get('DPDP_CONTROL_MAPPING_VERSION', 'dpdp-2023-v1')),
        'artifacts': evidence,
        'last_verified_at': str(current_app.config.get('DPDP_LAST_VERIFIED_AT', '')),
    }


def get_dpdp_readiness():
    controls = get_dpdp_controls().get('controls', {})
    implemented = 0
    partial = 0
    missing = 0
    for _, info in controls.items():
        status = str((info or {}).get('status') or '').strip().lower()
        if status == 'implemented':
            implemented += 1
        elif status == 'partial':
            partial += 1
        else:
            missing += 1
    total = max(len(controls), 1)
    score = round((implemented + (0.5 * partial)) / total, 3)
    return {
        'version': str(current_app.config.get('DPDP_CONTROL_MAPPING_VERSION', 'dpdp-2023-v1')),
        'implemented': implemented,
        'partial': partial,
        'missing': missing,
        'score': score,
        'last_verified_at': str(current_app.config.get('DPDP_LAST_VERIFIED_AT', '')),
        'controls': controls,
    }


def get_dpg_status():
    evidence_links = current_app.config.get('DPG_EVIDENCE_LINKS', []) or []
    if not isinstance(evidence_links, list):
        evidence_links = []
    return {
        'registration_status': str(current_app.config.get('DPG_REGISTRATION_STATUS', 'not_started')),
        'registry_id': str(current_app.config.get('DPG_REGISTRY_ID', '')),
        'project_url': str(current_app.config.get('DPG_PROJECT_URL', '')),
        'open_source_license': str(current_app.config.get('DPG_OPEN_SOURCE_LICENSE', '')),
        'evidence_links': evidence_links,
        'last_updated': str(current_app.config.get('DPG_STATUS_LAST_UPDATED', '')),
        'notes': str(current_app.config.get('DPG_STATUS_NOTES', '')),
    }
