"""Filter and paginate the operational request database before returning rows."""
from datetime import datetime, timezone

from ..db import get_db


def list_execution_requests(args):
    try:
        limit = min(int(args.get('limit', 100)), 500)
        offset = int(args.get('offset', 0))
    except (TypeError, ValueError):
        raise ValueError('limit and offset must be integers')
    if limit < 1 or offset < 0:
        raise ValueError('limit must be positive and offset must be non-negative')
    overdue = args.get('overdue', '').strip().lower()
    if overdue not in ('', '0', '1', 'false', 'true'):
        raise ValueError('overdue must be true or false')

    db = get_db()
    conditions, params = [], []
    from .pilot import resolve_scope, citizen_clause
    assigned = resolve_scope({'state': args.get('state', ''), 'district': args.get('district', '')}, args)
    cohort, cohort_params = citizen_clause(assigned, 'citizen_requests')
    security_conditions, security_params = [], []
    if cohort:
        security_conditions.append(cohort.removeprefix(' AND '))
        security_params.extend(cohort_params)
        for field in ('state', 'district'):
            if assigned.get(field):
                security_conditions.append(f'{field} = ?')
                security_params.append(assigned[field])
    conditions.extend(security_conditions)
    params.extend(security_params)
    for column in ('state', 'district', 'category', 'urgency', 'routed_department'):
        value = args.get(column, '').strip()
        if value:
            conditions.append(f'{column} = ?')
            params.append(value)

    status = args.get('status', '').strip()
    active_sql = "LOWER(TRIM(status)) NOT IN ('resolved', 'closed')"
    if status.lower() == 'active':
        conditions.append(active_sql)
    elif status:
        conditions.append('LOWER(TRIM(status)) = ?')
        params.append(status.lower())

    ticket = args.get('ticket', '').strip()
    if ticket:
        # Treat search text literally, including SQL wildcard characters.
        escaped = ticket.lower().replace('!', '!!').replace('%', '!%').replace('_', '!_')
        conditions.append("LOWER(request_id) LIKE ? ESCAPE '!'")
        params.append(f'%{escaped}%')

    now = datetime.now(timezone.utc)
    if overdue in ('1', 'true'):
        conditions.append(active_sql)
        if db.backend == 'postgres':
            conditions.append("CAST(NULLIF(sla_due_at, '') AS TIMESTAMPTZ) < CAST(? AS TIMESTAMPTZ)")
        else:
            conditions.append('julianday(sla_due_at) < julianday(?)')
        params.append(now.isoformat())

    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    total = db.execute('SELECT COUNT(*) AS total FROM citizen_requests' + where, params).fetchone()['total']
    # A stage update can remove the last row on a page. Return the last valid page.
    offset = min(offset, ((total - 1) // limit) * limit) if total else 0
    rows = db.execute('''
        SELECT request_id, source_channel, input_language, district, state, lat, lng,
               original_text, translated_text, category, urgency, sentiment, status,
               routed_department, sla_due_at, sla_breached_at, sla_escalation_level, created_at
        FROM citizen_requests
    ''' + where + ' ORDER BY id DESC LIMIT ? OFFSET ?', [*params, limit, offset]).fetchall()
    requests = []
    for row in rows:
        item = dict(row)
        try:
            due = datetime.fromisoformat(str(item['sla_due_at']).replace('Z', '+00:00'))
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            item['sla_overdue'] = item['status'].strip().lower() not in ('resolved', 'closed') and due < now
        except (TypeError, ValueError):
            item['sla_overdue'] = False
        requests.append(item)

    payload = {'success': True, 'requests': requests, 'total': total, 'limit': limit,
               'offset': offset, 'has_more': offset + len(requests) < total}
    if args.get('include_options') == '1':
        geography = {}
        secured = ' AND '.join(security_conditions) or '1=1'
        for row in db.execute('SELECT DISTINCT state, district FROM citizen_requests WHERE '+secured+' ORDER BY state, district', security_params).fetchall():
            if row['state']:
                districts = geography.setdefault(row['state'], [])
                if row['district']:
                    districts.append(row['district'])
        options = {'geography': geography}
        for column in ('category', 'routed_department', 'urgency', 'status'):
            options[column] = [row[column] for row in db.execute(
                f"SELECT DISTINCT {column} FROM citizen_requests WHERE {secured} AND {column} IS NOT NULL AND {column} <> '' ORDER BY {column}", security_params
            ).fetchall()]
        payload['filter_options'] = options
    return payload
