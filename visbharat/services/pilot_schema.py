"""Additive programme, membership, durable intake and provider-step contracts."""


def migrate(db):
    statements = [
        '''CREATE TABLE IF NOT EXISTS pilot_evidence (
            request_id TEXT NOT NULL, sha256 TEXT NOT NULL, name TEXT NOT NULL,
            mime_type TEXT NOT NULL, data_base64 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(request_id,sha256))''',
        '''CREATE TABLE IF NOT EXISTS pilot_delivery (
            pilot_id TEXT NOT NULL, decision_id TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            record_json TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
            PRIMARY KEY(pilot_id,decision_id))''',
        '''CREATE TABLE IF NOT EXISTS pilot_channel_events (
            pilot_id TEXT NOT NULL, channel TEXT NOT NULL, event_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL, request_id TEXT, created_at TEXT NOT NULL,
            PRIMARY KEY(pilot_id,channel,event_id))''',
        '''CREATE TABLE IF NOT EXISTS pilot_programmes (
            pilot_id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
            data_mode TEXT NOT NULL, config_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS pilot_locations (
            location_id TEXT PRIMARY KEY, pilot_id TEXT NOT NULL, state TEXT NOT NULL,
            district TEXT NOT NULL, local_body TEXT NOT NULL, ward TEXT NOT NULL,
            location_kind TEXT NOT NULL, official_code TEXT, verification_status TEXT NOT NULL,
            reference_uri TEXT, latitude REAL, longitude REAL)''',
        '''CREATE TABLE IF NOT EXISTS pilot_memberships (
            pilot_id TEXT NOT NULL, user_id INTEGER NOT NULL, state TEXT NOT NULL DEFAULT '',
            district TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(pilot_id,user_id))''',
        '''CREATE TABLE IF NOT EXISTS pilot_identities (
            issuer TEXT NOT NULL, subject TEXT NOT NULL, user_id INTEGER NOT NULL,
            PRIMARY KEY(issuer,subject))''',
        '''CREATE TABLE IF NOT EXISTS pilot_requests (
            request_id TEXT PRIMARY KEY, pilot_id TEXT NOT NULL, location_id TEXT NOT NULL,
            source_id TEXT, idempotency_key TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL, tracking_hash TEXT NOT NULL,
            processing_status TEXT NOT NULL, assigned_to INTEGER, version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(pilot_id,idempotency_key), UNIQUE(pilot_id,source_id))''',
        '''CREATE TABLE IF NOT EXISTS pilot_outbox (
            job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, lease_id TEXT,
            lease_until TEXT, next_attempt_at TEXT NOT NULL, last_error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(request_id,kind))''',
        '''CREATE TABLE IF NOT EXISTS pilot_provider_steps (
            request_id TEXT NOT NULL, step TEXT NOT NULL, input_sha256 TEXT NOT NULL,
            status TEXT NOT NULL, provider TEXT, model TEXT, result_json TEXT,
            latency_ms REAL, usage_json TEXT, started_at TEXT NOT NULL, completed_at TEXT,
            PRIMARY KEY(request_id,step,input_sha256))''',
        '''CREATE TABLE IF NOT EXISTS pilot_checks (
            pilot_id TEXT NOT NULL, check_key TEXT NOT NULL, status TEXT NOT NULL,
            reference_uri TEXT, notes TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT,
            PRIMARY KEY(pilot_id,check_key))''',
        'CREATE INDEX IF NOT EXISTS idx_pilot_requests_scope ON pilot_requests(pilot_id,location_id,processing_status)',
        'CREATE INDEX IF NOT EXISTS idx_pilot_outbox_due ON pilot_outbox(status,next_attempt_at,lease_until)',
        'CREATE INDEX IF NOT EXISTS idx_pilot_members_user ON pilot_memberships(user_id,active)',
    ]
    for sql in statements:
        db.execute(sql)
    db.commit()
