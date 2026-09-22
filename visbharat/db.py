import os
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

from flask import current_app, g

from .security import hash_api_token, token_last4


SQLITE_SCHEMA_SQL = '''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    api_token TEXT UNIQUE NOT NULL,
    api_token_hash TEXT,
    token_last4 TEXT,
    token_rotated_at TEXT,
    role TEXT NOT NULL DEFAULT 'analyst',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citizen_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT UNIQUE NOT NULL,
    source_channel TEXT NOT NULL,
    input_language TEXT NOT NULL,
    district TEXT NOT NULL,
    state TEXT NOT NULL,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    original_text TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    category TEXT NOT NULL,
    urgency TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    status TEXT NOT NULL,
    submitted_by TEXT,
    ward TEXT,
    service_type TEXT,
    routed_department TEXT,
    sla_due_at TEXT,
    sla_breached_at TEXT,
    sla_escalation_level INTEGER NOT NULL DEFAULT 0,
    replicated_bigquery INTEGER NOT NULL DEFAULT 0,
    replicated_pubsub INTEGER NOT NULL DEFAULT 0,
    replication_attempt_count INTEGER NOT NULL DEFAULT 0,
    next_replication_attempt_at TEXT,
    replication_locked_by TEXT,
    replication_locked_at TEXT,
    replication_error TEXT,
    replicated_at TEXT,
    emergency_dispatch_status TEXT NOT NULL DEFAULT 'none',
    dispatched_to TEXT,
    dispatched_at TEXT,
    acknowledgement_token TEXT,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    acknowledgement_notes TEXT,
    fallback_escalated_at TEXT,
    fallback_target TEXT,
    ai_metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    details_json TEXT,
    ip_address TEXT,
    prev_hash TEXT,
    current_hash TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_nonces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nonce_key TEXT UNIQUE NOT NULL,
    seen_at_epoch INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    job_type TEXT NOT NULL,
    channel TEXT,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    next_attempt_at_epoch INTEGER NOT NULL,
    last_error TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(idempotency_key)
);

CREATE TABLE IF NOT EXISTS policy_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT UNIQUE NOT NULL,
    district TEXT NOT NULL,
    priority_score REAL NOT NULL,
    estimated_project_cost_lakh REAL NOT NULL,
    total_budget_lakh REAL NOT NULL,
    status TEXT NOT NULL,
    notes TEXT,
    source_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    rejected_by TEXT,
    rejected_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
'''


POSTGRES_SCHEMA_SQL = '''
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    api_token TEXT UNIQUE NOT NULL,
    api_token_hash TEXT,
    token_last4 TEXT,
    token_rotated_at TEXT,
    role TEXT NOT NULL DEFAULT 'analyst',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS citizen_requests (
    id SERIAL PRIMARY KEY,
    request_id TEXT UNIQUE NOT NULL,
    source_channel TEXT NOT NULL,
    input_language TEXT NOT NULL,
    district TEXT NOT NULL,
    state TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    original_text TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    category TEXT NOT NULL,
    urgency TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    status TEXT NOT NULL,
    submitted_by TEXT,
    ward TEXT,
    service_type TEXT,
    routed_department TEXT,
    sla_due_at TEXT,
    sla_breached_at TEXT,
    sla_escalation_level INTEGER NOT NULL DEFAULT 0,
    replicated_bigquery INTEGER NOT NULL DEFAULT 0,
    replicated_pubsub INTEGER NOT NULL DEFAULT 0,
    replication_attempt_count INTEGER NOT NULL DEFAULT 0,
    next_replication_attempt_at TEXT,
    replication_locked_by TEXT,
    replication_locked_at TEXT,
    replication_error TEXT,
    replicated_at TEXT,
    emergency_dispatch_status TEXT NOT NULL DEFAULT 'none',
    dispatched_to TEXT,
    dispatched_at TEXT,
    acknowledgement_token TEXT,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    acknowledgement_notes TEXT,
    fallback_escalated_at TEXT,
    fallback_target TEXT,
    ai_metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    details_json TEXT,
    ip_address TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_nonces (
    id SERIAL PRIMARY KEY,
    nonce_key TEXT UNIQUE NOT NULL,
    seen_at_epoch BIGINT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id SERIAL PRIMARY KEY,
    job_id TEXT UNIQUE NOT NULL,
    job_type TEXT NOT NULL,
    channel TEXT,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    next_attempt_at_epoch BIGINT NOT NULL,
    last_error TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(idempotency_key)
);

CREATE TABLE IF NOT EXISTS policy_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT UNIQUE NOT NULL,
    district TEXT NOT NULL,
    priority_score REAL NOT NULL,
    estimated_project_cost_lakh REAL NOT NULL,
    total_budget_lakh REAL NOT NULL,
    status TEXT NOT NULL,
    notes TEXT,
    source_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    rejected_by TEXT,
    rejected_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
'''


class DbCursorAdapter:
    def __init__(self, cursor, backend):
        self._cursor = cursor
        self._backend = backend

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return row

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def lastrowid(self):
        return getattr(self._cursor, 'lastrowid', None)


class DbConnectionAdapter:
    def __init__(self, conn, backend):
        self._conn = conn
        self.backend = backend

    def _normalize_query(self, query: str) -> str:
        if self.backend == 'postgres':
            query = query.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'BIGSERIAL PRIMARY KEY')
            return query.replace('?', '%s')
        return query

    def execute(self, query, params=()):
        # psycopg parses percent signs when parameters are supplied. Preserve
        # literal LIKE wildcards before converting our qmark placeholders.
        normalized = self._normalize_query(query.replace('%', '%%') if self.backend == 'postgres' and params else query)
        if self.backend == 'postgres':
            cur = self._conn.cursor()
            cur.execute(normalized, params if params else None)
            return DbCursorAdapter(cur, self.backend)
        cur = self._conn.execute(normalized, params)
        return DbCursorAdapter(cur, self.backend)

    def executescript(self, script):
        if self.backend == 'postgres':
            cur = self._conn.cursor()
            for statement in [s.strip() for s in script.split(';') if s.strip()]:
                cur.execute(self._normalize_query(statement))
            return
        self._conn.executescript(script)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _sqlite_path_from_url(url: str):
    parsed = urlparse(url)
    raw_path = unquote(parsed.path or '')
    if parsed.netloc and parsed.netloc not in ('', 'localhost'):
        raw_path = f"//{parsed.netloc}{raw_path}"

    if raw_path.startswith('/') and len(raw_path) > 2 and raw_path[2] == ':':
        raw_path = raw_path[1:]

    if os.name == 'nt' and raw_path.startswith('/') and not (len(raw_path) > 2 and raw_path[2] == ':'):
        raw_path = raw_path[1:]
    elif os.name != 'nt' and not url.startswith('sqlite:////') and raw_path.startswith('/'):
        if not os.path.exists(os.path.dirname(raw_path) or '/'):
            raw_path = raw_path.lstrip('/')

    return raw_path


def _connect_sqlite(path: str):
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return DbConnectionAdapter(conn, 'sqlite')


def _connect_postgres(url: str):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as err:
        raise RuntimeError('PostgreSQL requested via DATABASE_URL, but psycopg is not installed') from err

    conn = psycopg.connect(url, row_factory=dict_row)
    return DbConnectionAdapter(conn, 'postgres')


def _open_connection():
    database_url = (current_app.config.get('DATABASE_URL') or '').strip()
    if database_url:
        lower = database_url.lower()
        if lower.startswith('postgresql://') or lower.startswith('postgres://'):
            return _connect_postgres(database_url)
        if lower.startswith('sqlite:///'):
            return _connect_sqlite(_sqlite_path_from_url(database_url))
        raise RuntimeError('Unsupported DATABASE_URL scheme; refusing SQLite fallback')

    database_path = current_app.config['DATABASE_PATH']
    return _connect_sqlite(database_path)


def get_db():
    if 'db' not in g:
        g.db = _open_connection()
    return g.db


def close_db(error=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def ensure_closure_feedback_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_lifecycle_events (
                id SERIAL PRIMARY KEY,
                request_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                actor TEXT,
                channel TEXT,
                reason TEXT,
                sla_due_at TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS closure_feedback (
                id SERIAL PRIMARY KEY,
                request_id TEXT UNIQUE NOT NULL,
                rating INTEGER NOT NULL,
                feedback_text TEXT,
                submitted_by TEXT,
                reopen_triggered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_idempotency_keys (
                id SERIAL PRIMARY KEY,
                endpoint TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CONSTRAINT uq_req_idem UNIQUE(endpoint, idempotency_key)
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                actor TEXT,
                channel TEXT,
                reason TEXT,
                sla_due_at TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS closure_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                rating INTEGER NOT NULL,
                feedback_text TEXT,
                submitted_by TEXT,
                reopen_triggered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_idempotency_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(endpoint, idempotency_key)
            )
            '''
        )
    db.commit()
    db.commit()


def init_db():
    db = get_db()
    schema = POSTGRES_SCHEMA_SQL if db.backend == 'postgres' else SQLITE_SCHEMA_SQL
    db.executescript(schema)
    db.commit()
    ensure_request_lifecycle_tables()
    ensure_closure_feedback_table()
    ensure_transparency_log_columns()
    ensure_outbox_replication_columns()
    ensure_emergency_dispatch_columns()
    ensure_cloud_ingestion_dead_letters_table()
    ensure_citizen_request_sla_columns()
    ensure_citizen_request_routing_columns()
    ensure_demand_cluster_tables()
    ensure_consent_ledger_table()
    from .services.auditor_schema import migrate
    migrate()
    from .services.pilot_schema import migrate as migrate_pilot
    migrate_pilot(db)
    from .services.citizen_assistant import migrate as migrate_assistant
    migrate_assistant(db)
    from .services.provider_evidence import migrate as migrate_provider_evidence
    migrate_provider_evidence(db)


def ensure_transparency_log_columns():
    db = get_db()
    if db.backend == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'audit_logs'"
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute('PRAGMA table_info(audit_logs)').fetchall()
        }

    if 'prev_hash' not in cols:
        db.execute('ALTER TABLE audit_logs ADD COLUMN prev_hash TEXT')
    if 'current_hash' not in cols:
        db.execute('ALTER TABLE audit_logs ADD COLUMN current_hash TEXT')
    db.commit()


def ensure_outbox_replication_columns():
    db = get_db()
    if db.backend == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                '''
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                ''',
                ('citizen_requests',),
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute('PRAGMA table_info(citizen_requests)').fetchall()
        }

    if 'replicated_bigquery' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN replicated_bigquery INTEGER NOT NULL DEFAULT 0')
    if 'replicated_pubsub' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN replicated_pubsub INTEGER NOT NULL DEFAULT 0')
    if 'replication_attempt_count' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN replication_attempt_count INTEGER NOT NULL DEFAULT 0')
    if 'next_replication_attempt_at' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN next_replication_attempt_at TEXT')
    if 'replication_locked_by' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN replication_locked_by TEXT')
    if 'replication_locked_at' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN replication_locked_at TEXT')
    if 'replication_error' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN replication_error TEXT')
    if 'replicated_at' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN replicated_at TEXT')
    db.commit()


def ensure_emergency_dispatch_columns():
    db = get_db()
    if db.backend == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                '''
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                ''',
                ('citizen_requests',),
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute('PRAGMA table_info(citizen_requests)').fetchall()
        }

    if 'emergency_dispatch_status' not in cols:
        db.execute("ALTER TABLE citizen_requests ADD COLUMN emergency_dispatch_status TEXT NOT NULL DEFAULT 'none'")
    if 'dispatched_to' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN dispatched_to TEXT')
    if 'dispatched_at' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN dispatched_at TEXT')
    if 'acknowledgement_token' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN acknowledgement_token TEXT')
    if 'acknowledged_at' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN acknowledged_at TEXT')
    if 'acknowledged_by' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN acknowledged_by TEXT')
    if 'acknowledgement_notes' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN acknowledgement_notes TEXT')
    if 'fallback_escalated_at' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN fallback_escalated_at TEXT')
    if 'fallback_target' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN fallback_target TEXT')
    db.commit()


def ensure_user_token_columns():
    db = get_db()

    if db.backend == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                '''
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                ''',
                ('users',),
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute('PRAGMA table_info(users)').fetchall()
        }

    if 'api_token_hash' not in cols:
        db.execute('ALTER TABLE users ADD COLUMN api_token_hash TEXT')
    if 'token_last4' not in cols:
        db.execute('ALTER TABLE users ADD COLUMN token_last4 TEXT')
    if 'token_rotated_at' not in cols:
        db.execute('ALTER TABLE users ADD COLUMN token_rotated_at TEXT')

    rows = db.execute(
        "SELECT id, api_token FROM users WHERE api_token_hash IS NULL OR api_token_hash = ''"
    ).fetchall()
    for row in rows:
        db.execute(
            'UPDATE users SET api_token_hash = ?, token_last4 = ? WHERE id = ?',
            (hash_api_token(row['api_token']), token_last4(row['api_token']), row['id'])
        )
    db.commit()


def ensure_webhook_nonce_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS webhook_nonces (
                id SERIAL PRIMARY KEY,
                nonce_key TEXT UNIQUE NOT NULL,
                seen_at_epoch BIGINT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS webhook_nonces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nonce_key TEXT UNIQUE NOT NULL,
                seen_at_epoch INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()



def ensure_processing_jobs_columns():
    db = get_db()
    if db.backend == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                '''
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                ''',
                ('processing_jobs',),
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute('PRAGMA table_info(processing_jobs)').fetchall()
        }

    if 'idempotency_key' not in cols:
        db.execute('ALTER TABLE processing_jobs ADD COLUMN idempotency_key TEXT')
    db.commit()



def ensure_citizen_request_sla_columns():
    db = get_db()
    if db.backend == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                '''
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                ''',
                ('citizen_requests',),
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute('PRAGMA table_info(citizen_requests)').fetchall()
        }

    if 'sla_due_at' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN sla_due_at TEXT')
    if 'sla_breached_at' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN sla_breached_at TEXT')
    if 'sla_escalation_level' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN sla_escalation_level INTEGER NOT NULL DEFAULT 0')
    db.commit()
def ensure_citizen_request_routing_columns():
    db = get_db()
    if db.backend == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                '''
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = ?
                ''',
                ('citizen_requests',),
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute('PRAGMA table_info(citizen_requests)').fetchall()
        }

    if 'ward' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN ward TEXT')
    if 'service_type' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN service_type TEXT')
    if 'routed_department' not in cols:
        db.execute('ALTER TABLE citizen_requests ADD COLUMN routed_department TEXT')
    db.commit()


def seed_citizen_requests_from_csv():
    import csv
    import json
    db = get_db()
    project_root = os.path.dirname(current_app.root_path)
    csv_path = os.path.join(project_root, 'static', 'data', 'complaints.csv')
    if not os.path.exists(csv_path):
        return

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            req_id = r.get('id') or r.get('request_id')
            if not req_id:
                continue
            meta_json = r.get('ai_metadata_json') or json.dumps({'seeded_from_csv': True})
            now = r.get('date') or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            if db.backend == 'postgres':
                db.execute(
                    '''
                    INSERT INTO citizen_requests (
                        request_id, source_channel, input_language, district, state,
                        lat, lng, original_text, translated_text, category,
                        urgency, sentiment, status, ai_metadata_json, created_at,
                        ward, service_type, routed_department, sla_due_at, sla_breached_at, sla_escalation_level, submitted_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (request_id) DO NOTHING
                    ''',
                    (
                        req_id, r.get('source', 'Web Form'), r.get('language', 'en'),
                        r.get('district', ''), r.get('state', ''),
                        float(r.get('lat', 0) or 0), float(r.get('lng', 0) or 0),
                        r.get('original_text', ''), r.get('translated_text', ''),
                        r.get('category', ''), r.get('urgency', 'Routine'),
                        r.get('sentiment', 'Neutral'), r.get('status', 'New'),
                        meta_json, r.get('created_at') or now,
                        r.get('ward') or None, r.get('service_type') or None, r.get('routed_department') or None,
                        r.get('sla_due_at') or None, r.get('sla_breached_at') or None, int(r.get('sla_escalation_level') or 0), r.get('submitted_by') or None
                    )
                )
            else:
                db.execute(
                    '''
                    INSERT OR IGNORE INTO citizen_requests (
                        request_id, source_channel, input_language, district, state,
                        lat, lng, original_text, translated_text, category,
                        urgency, sentiment, status, ai_metadata_json, created_at,
                        ward, service_type, routed_department, sla_due_at, sla_breached_at, sla_escalation_level, submitted_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        req_id, r.get('source', 'Web Form'), r.get('language', 'en'),
                        r.get('district', ''), r.get('state', ''),
                        float(r.get('lat', 0) or 0), float(r.get('lng', 0) or 0),
                        r.get('original_text', ''), r.get('translated_text', ''),
                        r.get('category', ''), r.get('urgency', 'Routine'),
                        r.get('sentiment', 'Neutral'), r.get('status', 'New'),
                        meta_json, r.get('created_at') or now,
                        r.get('ward') or None, r.get('service_type') or None, r.get('routed_department') or None,
                        r.get('sla_due_at') or None, r.get('sla_breached_at') or None, int(r.get('sla_escalation_level') or 0), r.get('submitted_by') or None
                    )
                )
    db.commit()


def seed_default_users():
    db = get_db()
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    defaults = [
        ('Platform Admin', current_app.config['ADMIN_API_TOKEN'], 'admin'),
        ('Policy Analyst', current_app.config['ANALYST_API_TOKEN'], 'analyst'),
        ('Independent Auditor', current_app.config['AUDITOR_API_TOKEN'], 'auditor'),
        ('Agentic Copilot API', current_app.config.get('AGENTIC_API_KEY', ''), 'admin'),
    ]

    for name, token, role in defaults:
        if not token:
            continue
        if db.backend == 'postgres':
            db.execute(
                '''
                INSERT INTO users (name, api_token, api_token_hash, token_last4, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (api_token) DO NOTHING
                ''',
                (name, token, hash_api_token(token), token_last4(token), role, now)
            )
        else:
            db.execute(
                '''
                INSERT OR IGNORE INTO users (name, api_token, api_token_hash, token_last4, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                (name, token, hash_api_token(token), token_last4(token), role, now)
            )
    db.commit()
    if current_app.config.get('SEED_DEMO_DATA', True):
        seed_citizen_requests_from_csv()





def ensure_notification_deliveries_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_deliveries (
                id SERIAL PRIMARY KEY,
                delivery_id TEXT UNIQUE NOT NULL,
                request_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                channel TEXT,
                target_json TEXT,
                payload_json TEXT,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 0,
                external_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT UNIQUE NOT NULL,
                request_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                channel TEXT,
                target_json TEXT,
                payload_json TEXT,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 0,
                external_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
    db.commit()
def ensure_policy_decisions_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS policy_decisions (
                id SERIAL PRIMARY KEY,
                decision_id TEXT UNIQUE NOT NULL,
                district TEXT NOT NULL,
                priority_score DOUBLE PRECISION NOT NULL,
                estimated_project_cost_lakh DOUBLE PRECISION NOT NULL,
                total_budget_lakh DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                source_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                approved_by TEXT,
                approved_at TEXT,
                rejected_by TEXT,
                rejected_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS policy_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT UNIQUE NOT NULL,
                district TEXT NOT NULL,
                priority_score REAL NOT NULL,
                estimated_project_cost_lakh REAL NOT NULL,
                total_budget_lakh REAL NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                source_json TEXT NOT NULL,
                created_by TEXT NOT NULL,
                approved_by TEXT,
                approved_at TEXT,
                rejected_by TEXT,
                rejected_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_request_lifecycle_tables():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_lifecycle_events (
                id SERIAL PRIMARY KEY,
                request_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                actor TEXT,
                channel TEXT,
                reason TEXT,
                sla_due_at TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS closure_feedback (
                id SERIAL PRIMARY KEY,
                request_id TEXT UNIQUE NOT NULL,
                rating INTEGER NOT NULL,
                feedback_text TEXT,
                submitted_by TEXT,
                reopen_triggered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_idempotency_keys (
                id SERIAL PRIMARY KEY,
                endpoint TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CONSTRAINT uq_req_idem UNIQUE(endpoint, idempotency_key)
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_lifecycle_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                actor TEXT,
                channel TEXT,
                reason TEXT,
                sla_due_at TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS closure_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                rating INTEGER NOT NULL,
                feedback_text TEXT,
                submitted_by TEXT,
                reopen_triggered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_idempotency_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                idempotency_key TEXT UNIQUE NOT NULL,
                request_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()






def ensure_request_cosign_tables():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_tokens (
                id SERIAL PRIMARY KEY,
                request_id TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                token_status TEXT NOT NULL DEFAULT 'active',
                issued_at TEXT NOT NULL,
                expires_at TEXT,
                used_at TEXT,
                meta_json TEXT
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_cosigns (
                id SERIAL PRIMARY KEY,
                request_id TEXT NOT NULL,
                token TEXT NOT NULL,
                supporter_ref TEXT,
                supporter_name TEXT,
                channel TEXT,
                verified INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(request_id, token, supporter_ref)
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                token_status TEXT NOT NULL DEFAULT 'active',
                issued_at TEXT NOT NULL,
                expires_at TEXT,
                used_at TEXT,
                meta_json TEXT
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS request_cosigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                token TEXT NOT NULL,
                supporter_ref TEXT,
                supporter_name TEXT,
                channel TEXT,
                verified INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(request_id, token, supporter_ref)
            )
            '''
        )
    db.commit()

def ensure_federation_external_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS federation_external_events (
                id SERIAL PRIMARY KEY,
                source TEXT NOT NULL,
                external_event_id TEXT NOT NULL,
                source_key TEXT UNIQUE NOT NULL,
                request_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS federation_external_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_event_id TEXT NOT NULL,
                source_key TEXT UNIQUE NOT NULL,
                request_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
    db.commit()

def ensure_ivr_callback_tables():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_jobs (
                id SERIAL PRIMARY KEY,
                callback_id TEXT UNIQUE NOT NULL,
                phone TEXT NOT NULL,
                district TEXT,
                language TEXT,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 2,
                next_retry_at TEXT,
                ingestion_job_id TEXT,
                last_error TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_dead_letters (
                id SERIAL PRIMARY KEY,
                callback_id TEXT NOT NULL,
                phone TEXT,
                district TEXT,
                language TEXT,
                attempt_count INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_events (
                id SERIAL PRIMARY KEY,
                event_key TEXT UNIQUE NOT NULL,
                callback_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                external_event_id TEXT,
                status TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                callback_id TEXT UNIQUE NOT NULL,
                phone TEXT NOT NULL,
                district TEXT,
                language TEXT,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 2,
                next_retry_at TEXT,
                ingestion_job_id TEXT,
                last_error TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                callback_id TEXT NOT NULL,
                phone TEXT,
                district TEXT,
                language TEXT,
                attempt_count INTEGER NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE NOT NULL,
                callback_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                external_event_id TEXT,
                status TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()
def ensure_demand_cluster_tables():
    from .services.clustering import ensure_demand_cluster_tables as _ensure
    _ensure()


def ensure_policy_scoring_weights_table():
    from .services.scoring import ensure_scoring_weights_table as _ensure
    _ensure()

def ensure_notification_dead_letters_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_dead_letters (
                id SERIAL PRIMARY KEY,
                delivery_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                payload_json TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                payload_json TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()

def ensure_notification_connector_health_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_connector_health (
                id SERIAL PRIMARY KEY,
                provider TEXT UNIQUE NOT NULL,
                active_key_slot TEXT NOT NULL DEFAULT 'primary',
                failure_count INTEGER NOT NULL DEFAULT 0,
                open_until_epoch BIGINT NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_connector_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT UNIQUE NOT NULL,
                active_key_slot TEXT NOT NULL DEFAULT 'primary',
                failure_count INTEGER NOT NULL DEFAULT 0,
                open_until_epoch INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_notification_receipt_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_receipt_events (
                id SERIAL PRIMARY KEY,
                event_key TEXT UNIQUE NOT NULL,
                delivery_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_receipt_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE NOT NULL,
                delivery_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_notification_slo_alert_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_slo_alert_events (
                id SERIAL PRIMARY KEY,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_slo_alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_ivr_callback_alert_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_alert_events (
                id SERIAL PRIMARY KEY,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()

def ensure_notification_dead_letters_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_dead_letters (
                id SERIAL PRIMARY KEY,
                delivery_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                payload_json TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                payload_json TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()

def ensure_notification_connector_health_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_connector_health (
                id SERIAL PRIMARY KEY,
                provider TEXT UNIQUE NOT NULL,
                active_key_slot TEXT NOT NULL DEFAULT 'primary',
                failure_count INTEGER NOT NULL DEFAULT 0,
                open_until_epoch BIGINT NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_connector_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT UNIQUE NOT NULL,
                active_key_slot TEXT NOT NULL DEFAULT 'primary',
                failure_count INTEGER NOT NULL DEFAULT 0,
                open_until_epoch INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_notification_receipt_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_receipt_events (
                id SERIAL PRIMARY KEY,
                event_key TEXT UNIQUE NOT NULL,
                delivery_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_receipt_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE NOT NULL,
                delivery_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_notification_slo_alert_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_slo_alert_events (
                id SERIAL PRIMARY KEY,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_slo_alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_ivr_callback_alert_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_alert_events (
                id SERIAL PRIMARY KEY,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()
def ensure_cloud_ingestion_dead_letters_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS cloud_ingestion_dead_letters (
                id SERIAL PRIMARY KEY,
                request_id TEXT UNIQUE NOT NULL,
                district TEXT,
                state TEXT,
                source_channel TEXT,
                payload_json TEXT NOT NULL,
                error_message TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                last_retry_at TEXT
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS cloud_ingestion_dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                district TEXT,
                state TEXT,
                source_channel TEXT,
                payload_json TEXT NOT NULL,
                error_message TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                last_retry_at TEXT
            )
            '''
        )
    db.commit()


def ensure_notification_dead_letters_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_dead_letters (
                id SERIAL PRIMARY KEY,
                delivery_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                payload_json TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                payload_json TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()

def ensure_notification_connector_health_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_connector_health (
                id SERIAL PRIMARY KEY,
                provider TEXT UNIQUE NOT NULL,
                active_key_slot TEXT NOT NULL DEFAULT 'primary',
                failure_count INTEGER NOT NULL DEFAULT 0,
                open_until_epoch BIGINT NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_connector_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT UNIQUE NOT NULL,
                active_key_slot TEXT NOT NULL DEFAULT 'primary',
                failure_count INTEGER NOT NULL DEFAULT 0,
                open_until_epoch INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_notification_receipt_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_receipt_events (
                id SERIAL PRIMARY KEY,
                event_key TEXT UNIQUE NOT NULL,
                delivery_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_receipt_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE NOT NULL,
                delivery_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_notification_slo_alert_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_slo_alert_events (
                id SERIAL PRIMARY KEY,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS notification_slo_alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_ivr_callback_alert_events_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_alert_events (
                id SERIAL PRIMARY KEY,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ivr_callback_alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key TEXT UNIQUE NOT NULL,
                alert_type TEXT NOT NULL,
                severity TEXT,
                observed_json TEXT,
                notification_delivery_id TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()

def ensure_consent_ledger_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS consent_ledger (
                id SERIAL PRIMARY KEY,
                event_id TEXT UNIQUE NOT NULL,
                request_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                language TEXT,
                consent_scope TEXT NOT NULL,
                consent_granted INTEGER NOT NULL,
                legal_basis TEXT,
                actor TEXT,
                subject_ref TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS consent_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                request_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                language TEXT,
                consent_scope TEXT NOT NULL,
                consent_granted INTEGER NOT NULL,
                legal_basis TEXT,
                actor TEXT,
                subject_ref TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def ensure_project_controls_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS project_controls (
                id SERIAL PRIMARY KEY,
                decision_id TEXT UNIQUE NOT NULL,
                project_name TEXT NOT NULL,
                district TEXT NOT NULL,
                category TEXT NOT NULL,
                sanction_timestamp TEXT NOT NULL,
                treated_geofence_id TEXT NOT NULL,
                control_geofence_ids TEXT NOT NULL,
                matching_distance_metric TEXT NOT NULL DEFAULT 'Standardized Mahalanobis',
                covariate_balance_smd DOUBLE PRECISION NOT NULL DEFAULT 0.042,
                parallel_trend_pvalue DOUBLE PRECISION NOT NULL DEFAULT 0.88,
                control_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS project_controls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT UNIQUE NOT NULL,
                project_name TEXT NOT NULL,
                district TEXT NOT NULL,
                category TEXT NOT NULL,
                sanction_timestamp TEXT NOT NULL,
                treated_geofence_id TEXT NOT NULL,
                control_geofence_ids TEXT NOT NULL,
                matching_distance_metric TEXT NOT NULL DEFAULT 'Standardized Mahalanobis',
                covariate_balance_smd REAL NOT NULL DEFAULT 0.042,
                parallel_trend_pvalue REAL NOT NULL DEFAULT 0.88,
                control_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
    db.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
    if not app.config.get('AUTO_MIGRATE', app.config.get('DEMO_MODE', True)):
        return
    migrate_application(app)


def migrate_application(app):
    with app.app_context():
        init_db()
        from .services.provider_evidence import migrate as migrate_provider_evidence
        migrate_provider_evidence(get_db())
        ensure_user_token_columns()
        if app.config.get('DEMO_MODE', True):
            seed_default_users()
        ensure_user_token_columns()
        ensure_webhook_nonce_table()
        ensure_processing_jobs_columns()
        ensure_policy_decisions_table()
        ensure_project_controls_table()
        ensure_citizen_request_routing_columns()
        ensure_citizen_request_sla_columns()
        ensure_request_lifecycle_tables()
        ensure_notification_deliveries_table()
        ensure_notification_dead_letters_table()
        ensure_cloud_ingestion_dead_letters_table()
        ensure_emergency_dispatch_columns()
        ensure_notification_connector_health_table()
        ensure_notification_receipt_events_table()
        ensure_notification_slo_alert_events_table()
        ensure_demand_cluster_tables()
        ensure_policy_scoring_weights_table()
        ensure_federation_external_events_table()
        ensure_database_indexes()


def ensure_database_indexes():
    db = get_db()
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_cr_source_state_dist ON citizen_requests(source_channel, state, district)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cr_state_dist_status ON citizen_requests(state, district, status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cr_sla_status ON citizen_requests(sla_due_at, status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cr_analyst_scope ON citizen_requests(state, district, category, urgency, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cr_analyst_date ON citizen_requests(created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cr_analyst_dimensions ON citizen_requests(state, district, category, urgency, input_language, source_channel, status, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_cr_analyst_candidates ON citizen_requests(state, district, ward, category, urgency, request_id, routed_department, status)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_lifecycle_request_stage_time ON request_lifecycle_events(request_id, to_status, created_at)")
        db.commit()
    except Exception:
        pass
















