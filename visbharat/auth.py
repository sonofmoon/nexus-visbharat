from functools import wraps
from flask import request, jsonify, g, current_app

from .db import get_db
from .security import hash_api_token, token_last4


def _denied(detector, message, actor='unauthenticated'):
    from .audit import security_detection
    security_detection(detector,'MEDIUM',message,actor=actor)


def _extract_token(auth_header: str):
    if not auth_header:
        return None
    parts = auth_header.split(' ', 1)
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None
    return parts[1].strip()


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Reauthenticate each invocation: Flask g can outlive a test request.
        g.current_user = None
        from .services.pilot_identity import session_user
        signed_in=session_user()
        if signed_in:
            g.current_user=signed_in
            return fn(*args, **kwargs)
        if current_app.config.get('PILOT_ONLY') and not current_app.config.get('DEMO_MODE') and not current_app.config.get('PILOT_ALLOW_API_TOKENS',False):
            return jsonify(success=False,error='Sign in with the provisioned ministry identity'),401
        token = _extract_token(request.headers.get('Authorization', ''))
        if not token:
            _denied('authentication_missing','Protected endpoint called without authentication')
            return jsonify({'success': False, 'error': 'Missing bearer token'}), 401

        db = get_db()
        token_hash = hash_api_token(token)
        row = db.execute(
            'SELECT id, name, role FROM users WHERE api_token_hash = ?',
            (token_hash,)
        ).fetchone()

        if row is None:
            legacy_row = db.execute(
                'SELECT id, name, role, api_token FROM users WHERE api_token = ?',
                (token,)
            ).fetchone()
            if legacy_row is None:
                _denied('authentication_invalid','Invalid bearer credential rejected')
                return jsonify({'success': False, 'error': 'Invalid token'}), 401

            db.execute(
                'UPDATE users SET api_token_hash = ?, token_last4 = ? WHERE id = ?',
                (token_hash, token_last4(token), legacy_row['id'])
            )
            db.commit()

            row = legacy_row

        g.current_user = {
            'id': row['id'],
            'name': row['name'],
            'role': row['role']
        }
        return fn(*args, **kwargs)

    return wrapper


def require_roles(*allowed_roles):
    def decorator(fn):
        @require_auth
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user_role = g.current_user.get('role')
            if user_role not in allowed_roles:
                _denied('role_denied','Authenticated role was denied access',g.current_user['name'])
                return jsonify({'success': False, 'error': 'Forbidden for this role'}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator


ROLE_CLEARANCE = {
    'public': 1,
    'analyst': 2,
    'auditor': 3,
    'admin': 4,
}

def parse_claims(req=None):
    r = req or request
    user_role = getattr(g, 'current_user', {}).get('role') if hasattr(g, 'current_user') and g.current_user else None
    role = r.headers.get('X-NVB-Role') or r.args.get('role') or user_role or 'public'
    role = str(role).lower().strip()
    if role not in ROLE_CLEARANCE:
        role = 'public'

    geofence_scope = r.headers.get('X-NVB-Geofence') or r.args.get('geofence') or 'ALL'
    clearance_level = ROLE_CLEARANCE.get(role, 1)

    return {
        'role': role,
        'geofence_scope': geofence_scope,
        'clearance_level': clearance_level,
    }

def require_claims(min_clearance=1):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            claims = parse_claims()
            if claims['clearance_level'] < min_clearance:
                return jsonify({'success': False, 'error': f'Insufficient clearance level. Required: {min_clearance}, Provided: {claims["clearance_level"]}'}), 403
            g.claims = claims
            return fn(*args, **kwargs)
        return wrapper
    return decorator

