from functools import wraps
from flask import request, jsonify, g

ROLE_CLEARANCE = {
    'public': 1,
    'analyst': 2,
    'auditor': 3,
    'admin': 4,
}

def parse_claims(req=None):
    """
    Extract role claims, geofence scope, and clearance level from request headers / tokens.
    """
    r = req or request
    user_role = getattr(g, 'current_user', {}).get('role') if hasattr(g, 'current_user') and g.current_user else None
    role = r.headers.get('X-NVB-Role') or r.args.get('role') or user_role or 'public'
    role = role.lower().strip()
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
    """
    Decorator enforcing minimum clearance level.
    """
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
