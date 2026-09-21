from __future__ import annotations

from datetime import datetime, timezone

from ..db import get_db


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return int(default)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _normalize(value: float, max_value: float) -> float:
    mv = max(float(max_value), 1e-9)
    return _clamp((float(value) / mv) * 100.0)


def default_scoring_weights() -> dict:
    return {
        'w1_demand_density': 0.22,
        'w2_demand_velocity': 0.16,
        'w3_deprivation': 0.16,
        'w4_infrastructure_gap': 0.14,
        'w5_investment_already_made': 0.10,
        'w6_relative_cost': 0.08,
        'w7_equity_mandate': 0.14,
    }


def ensure_scoring_weights_table():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS policy_scoring_weights (
                id SERIAL PRIMARY KEY,
                profile TEXT UNIQUE NOT NULL,
                weights_json TEXT NOT NULL,
                updated_by TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS policy_scoring_weights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT UNIQUE NOT NULL,
                weights_json TEXT NOT NULL,
                updated_by TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )

    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS policy_scoring_profile_state (
                id SERIAL PRIMARY KEY,
                state_key TEXT UNIQUE NOT NULL,
                active_profile TEXT NOT NULL,
                previous_profile TEXT,
                updated_by TEXT,
                change_reason TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS policy_scoring_profile_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_key TEXT UNIQUE NOT NULL,
                active_profile TEXT NOT NULL,
                previous_profile TEXT,
                updated_by TEXT,
                change_reason TEXT,
                updated_at TEXT NOT NULL
            )
            '''
        )

    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    row = db.execute(
        'SELECT id FROM policy_scoring_profile_state WHERE state_key = ? LIMIT 1',
        ('active',),
    ).fetchone()
    if not row:
        db.execute(
            'INSERT INTO policy_scoring_profile_state (state_key, active_profile, previous_profile, updated_by, change_reason, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
            ('active', 'default', '', 'system-default', 'initial bootstrap', now),
        )
    db.commit()


def validate_scoring_weights(weights: dict) -> tuple[bool, str]:
    if not isinstance(weights, dict):
        return False, 'weights must be an object'

    defaults = default_scoring_weights()
    for key in weights.keys():
        if key not in defaults:
            return False, f'unsupported weight key: {key}'

    for key, default_val in defaults.items():
        value = weights.get(key, default_val)
        try:
            val = float(value)
        except Exception:
            return False, f'{key} must be a number'
        if val < 0:
            return False, f'{key} must be >= 0'

    return True, ''


def get_scoring_weights(profile: str = 'default') -> dict:
    import json

    ensure_scoring_weights_table()
    db = get_db()
    row = db.execute(
        '''
        SELECT profile, weights_json, updated_by, updated_at
        FROM policy_scoring_weights
        WHERE profile = ?
        LIMIT 1
        ''',
        (str(profile or 'default'),),
    ).fetchone()

    defaults = default_scoring_weights()
    if not row:
        return {
            'profile': str(profile or 'default'),
            'weights': defaults,
            'updated_by': 'system-default',
            'updated_at': None,
            'source': 'default',
        }

    try:
        stored = json.loads(str(row['weights_json'] or '{}'))
    except Exception:
        stored = {}

    merged = dict(defaults)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in merged:
                merged[key] = _safe_float(value, merged[key])

    return {
        'profile': str(row['profile'] or profile or 'default'),
        'weights': merged,
        'updated_by': str(row['updated_by'] or ''),
        'updated_at': row['updated_at'],
        'source': 'db',
    }


def upsert_scoring_weights(weights: dict, actor: str, profile: str = 'default') -> dict:
    import json

    ensure_scoring_weights_table()
    ok, error = validate_scoring_weights(weights)
    if not ok:
        raise ValueError(error)

    defaults = default_scoring_weights()
    merged = dict(defaults)
    for key, value in (weights or {}).items():
        if key in merged:
            merged[key] = _safe_float(value, merged[key])

    db = get_db()
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    profile_name = str(profile or 'default')

    existing = db.execute(
        'SELECT id FROM policy_scoring_weights WHERE profile = ? LIMIT 1',
        (profile_name,),
    ).fetchone()

    if existing:
        db.execute(
            'UPDATE policy_scoring_weights SET weights_json = ?, updated_by = ?, updated_at = ? WHERE profile = ?',
            (json.dumps(merged), str(actor or 'system'), now, profile_name),
        )
    else:
        db.execute(
            'INSERT INTO policy_scoring_weights (profile, weights_json, updated_by, updated_at) VALUES (?, ?, ?, ?)',
            (profile_name, json.dumps(merged), str(actor or 'system'), now),
        )
    db.commit()

    return {
        'profile': profile_name,
        'weights': merged,
        'updated_by': str(actor or 'system'),
        'updated_at': now,
        'source': 'db',
    }


def list_scoring_profiles() -> list[str]:
    ensure_scoring_weights_table()
    db = get_db()
    rows = db.execute(
        'SELECT profile FROM policy_scoring_weights ORDER BY profile ASC'
    ).fetchall()
    profiles = [str(row['profile']) for row in rows]
    if 'default' not in profiles:
        profiles.insert(0, 'default')
    return profiles


def get_active_scoring_profile() -> dict:
    ensure_scoring_weights_table()
    db = get_db()
    row = db.execute(
        '''
        SELECT active_profile, previous_profile, updated_by, change_reason, updated_at
        FROM policy_scoring_profile_state
        WHERE state_key = ?
        LIMIT 1
        ''',
        ('active',),
    ).fetchone()
    if not row:
        return {
            'active_profile': 'default',
            'previous_profile': '',
            'updated_by': 'system-default',
            'change_reason': 'initial bootstrap',
            'updated_at': None,
        }
    return {
        'active_profile': str(row['active_profile'] or 'default'),
        'previous_profile': str(row['previous_profile'] or ''),
        'updated_by': str(row['updated_by'] or ''),
        'change_reason': str(row['change_reason'] or ''),
        'updated_at': row['updated_at'],
    }


def activate_scoring_profile(profile: str, actor: str, change_reason: str) -> dict:
    ensure_scoring_weights_table()
    profile_name = str(profile or '').strip()
    reason = str(change_reason or '').strip()
    if not profile_name:
        raise ValueError('profile is required')
    if not reason:
        raise ValueError('change_reason is required')

    profiles = set(list_scoring_profiles())
    if profile_name not in profiles:
        raise ValueError('profile does not exist')

    db = get_db()
    current = get_active_scoring_profile()
    current_active = str(current.get('active_profile') or 'default')
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    if current_active != profile_name:
        db.execute(
            'UPDATE policy_scoring_profile_state SET active_profile = ?, previous_profile = ?, updated_by = ?, change_reason = ?, updated_at = ? WHERE state_key = ?',
            (profile_name, current_active, str(actor or 'system'), reason, now, 'active'),
        )
        db.commit()

    return get_active_scoring_profile()


def rollback_scoring_profile(actor: str, change_reason: str) -> dict:
    ensure_scoring_weights_table()
    reason = str(change_reason or '').strip()
    if not reason:
        raise ValueError('change_reason is required')

    current = get_active_scoring_profile()
    previous = str(current.get('previous_profile') or '').strip()
    if not previous:
        raise ValueError('no previous_profile available for rollback')

    db = get_db()
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    db.execute(
        'UPDATE policy_scoring_profile_state SET active_profile = ?, previous_profile = ?, updated_by = ?, change_reason = ?, updated_at = ? WHERE state_key = ?',
        (previous, str(current.get('active_profile') or ''), str(actor or 'system'), reason, now, 'active'),
    )
    db.commit()
    return get_active_scoring_profile()


def compute_priority_score(entry: dict, scales: dict, weights: dict | None = None) -> dict:
    cfg = dict(default_scoring_weights())
    if isinstance(weights, dict):
        for key, val in weights.items():
            if key in cfg:
                cfg[key] = _safe_float(val, cfg[key])

    population = max(_safe_float(entry.get('population', 0.0), 0.0), 1.0)
    deduped_cluster_size = max(_safe_int(entry.get('deduped_cluster_size', 0), 0), 0)
    velocity_index = max(_safe_float(entry.get('velocity_index', 0.0), 0.0), 0.0)
    deprivation_index = _clamp(_safe_float(entry.get('deprivation_index', 0.0), 0.0) * 100.0)
    infrastructure_gap = _clamp(_safe_float(entry.get('infrastructure_gap', 0.0), 0.0))
    investment_lakh = max(_safe_float(entry.get('investment_already_made_lakh', 0.0), 0.0), 0.0)
    relative_cost = _safe_float(entry.get('relative_cost', 1.0), 1.0)
    equity_mandate = _clamp(_safe_float(entry.get('equity_mandate', 0.0), 0.0))

    demand_density_raw = (deduped_cluster_size / population) * 100000.0
    demand_density = _normalize(demand_density_raw, _safe_float(scales.get('max_demand_density_per_100k', 1.0), 1.0))
    demand_velocity = _normalize(velocity_index, _safe_float(scales.get('max_velocity_index', 1.0), 1.0))
    investment_penalty = _normalize(investment_lakh, _safe_float(scales.get('max_investment_lakh', 1.0), 1.0))
    relative_cost_penalty = _normalize(relative_cost, _safe_float(scales.get('max_relative_cost', 1.0), 1.0))

    score = (
        cfg['w1_demand_density'] * demand_density
        + cfg['w2_demand_velocity'] * demand_velocity
        + cfg['w3_deprivation'] * deprivation_index
        + cfg['w4_infrastructure_gap'] * infrastructure_gap
        - cfg['w5_investment_already_made'] * investment_penalty
        - cfg['w6_relative_cost'] * relative_cost_penalty
        + cfg['w7_equity_mandate'] * equity_mandate
    )

    return {
        'priority_score': round(score, 3),
        'components': {
            'demand_density': round(demand_density, 3),
            'demand_velocity': round(demand_velocity, 3),
            'deprivation': round(deprivation_index, 3),
            'infrastructure_gap': round(infrastructure_gap, 3),
            'investment_already_made_penalty': round(investment_penalty, 3),
            'relative_cost_penalty': round(relative_cost_penalty, 3),
            'equity_mandate': round(equity_mandate, 3),
        },
        'raw': {
            'demand_density_per_100k': round(demand_density_raw, 3),
            'velocity_index': round(velocity_index, 3),
            'deprivation_index': round(deprivation_index, 3),
            'infrastructure_gap': round(infrastructure_gap, 3),
            'investment_already_made_lakh': round(investment_lakh, 3),
            'relative_cost': round(relative_cost, 4),
            'equity_mandate': round(equity_mandate, 3),
        },
        'weights': {k: round(_safe_float(v, 0.0), 4) for k, v in cfg.items()},
    }
