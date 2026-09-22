"""Additive Auditor migration. Legacy events are never retroactively signed."""
from ..db import get_db


def migrate():
    db = get_db()
    serial = 'SERIAL PRIMARY KEY' if db.backend == 'postgres' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    if db.backend == 'postgres':
        columns = {r['column_name'] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='audit_logs'").fetchall()}
    else:
        columns = {r['name'] for r in db.execute('PRAGMA table_info(audit_logs)').fetchall()}
    for name, kind in [('chain_seq', 'INTEGER'), ('event_version', 'TEXT')]:
        if name not in columns:
            try:
                db.execute(f'ALTER TABLE audit_logs ADD COLUMN {name} {kind}')
            except Exception:
                pass
    statements = [
        '''CREATE TABLE IF NOT EXISTS auditor_chain_state (
            id INTEGER PRIMARY KEY, legacy_end_id INTEGER NOT NULL, head_seq INTEGER NOT NULL,
            head_hash TEXT NOT NULL, head_id INTEGER NOT NULL, migrated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS auditor_cases (
            case_id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT, request_id TEXT,
            state TEXT, district TEXT, category TEXT, title TEXT NOT NULL, severity TEXT NOT NULL,
            status TEXT NOT NULL, owner TEXT NOT NULL, due_at TEXT NOT NULL, data_mode TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            version INTEGER NOT NULL, resolution TEXT, closure_evidence TEXT, closed_by TEXT,
            idempotency_key TEXT UNIQUE)''',
        f'''CREATE TABLE IF NOT EXISTS auditor_case_events (
            id {serial}, case_id TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
            from_status TEXT, to_status TEXT NOT NULL, notes TEXT NOT NULL, evidence TEXT,
            created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS auditor_evidence (
            evidence_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
            title TEXT NOT NULL, source_uri TEXT NOT NULL, sha256 TEXT, observed_at TEXT NOT NULL,
            data_mode TEXT NOT NULL, metadata_json TEXT NOT NULL, status TEXT NOT NULL,
            submitted_by TEXT NOT NULL, submitted_at TEXT NOT NULL, reviewed_by TEXT,
            reviewed_at TEXT, review_notes TEXT, version INTEGER NOT NULL)''',
        f'''CREATE TABLE IF NOT EXISTS auditor_detections (
            id {serial}, detector TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL,
            resource_id TEXT, occurred_at TEXT NOT NULL, case_id TEXT, rule_version TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS auditor_jobs (
            job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, scope_json TEXT NOT NULL,
            input_json TEXT NOT NULL, result_json TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            started_at TEXT, completed_at TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            idempotency_key TEXT UNIQUE)''',
        '''CREATE TABLE IF NOT EXISTS auditor_snapshots (
            snapshot_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, role TEXT NOT NULL,
            kind TEXT NOT NULL, scope_json TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS auditor_processing_restrictions (
            request_id TEXT NOT NULL, purpose TEXT NOT NULL, state TEXT NOT NULL,
            receipt_id TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(request_id,purpose))''',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_chain_seq ON audit_logs(chain_seq)',
        'CREATE INDEX IF NOT EXISTS idx_audit_resource_time ON audit_logs(resource_id,id)',
        'CREATE INDEX IF NOT EXISTS idx_audit_action_time ON audit_logs(action,id)',
        'CREATE INDEX IF NOT EXISTS idx_audit_event_time ON audit_logs(created_at,id)',
        'CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor,id)',
        'CREATE INDEX IF NOT EXISTS idx_consent_request_purpose ON consent_ledger(request_id,consent_scope,id)',
        'CREATE INDEX IF NOT EXISTS idx_consent_date ON consent_ledger(created_at,id)',
        'CREATE INDEX IF NOT EXISTS idx_auditor_case_scope ON auditor_cases(state,district,category,status,due_at)',
        'CREATE INDEX IF NOT EXISTS idx_auditor_case_project ON auditor_cases(project_id,updated_at)',
        'CREATE INDEX IF NOT EXISTS idx_auditor_evidence_project ON auditor_evidence(project_id,kind,observed_at)',
        'CREATE INDEX IF NOT EXISTS idx_auditor_detection_time ON auditor_detections(occurred_at,id)',
        'CREATE INDEX IF NOT EXISTS idx_auditor_job_status ON auditor_jobs(status,created_at)',
    ]
    for sql in statements: db.execute(sql)
    from datetime import datetime, timezone
    last = db.execute('SELECT COALESCE(MAX(id),0) AS n FROM audit_logs').fetchone()['n']
    db.execute('''INSERT INTO auditor_chain_state(id,legacy_end_id,head_seq,head_hash,head_id,migrated_at)
        VALUES(1,?,0,?,?,?) ON CONFLICT(id) DO NOTHING''',
        (last, '0'*64, last, datetime.now(timezone.utc).isoformat()))
    db.commit()
