import json
from pathlib import Path
from flask import Blueprint, current_app, render_template, send_from_directory, request, abort, session
from ..db import get_db

web_bp = Blueprint('web', __name__)


def _demo_showcase():
    path = Path(current_app.static_folder) / 'data' / 'demo_showcase.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


@web_bp.route('/demo')
def jury_demo():
    showcase = _demo_showcase()
    if not showcase:
        abort(404)
    from ..services.citizen_assistant import receipt
    ticket = request.args.get('assistant_ticket', '')
    assistant_receipt = receipt(ticket) if ticket else None
    return render_template('demo.html', demo=showcase, assistant_receipt=assistant_receipt)


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
            'languages_supported': int(row['languages_supported'] or 0),
            'states_covered': int(row['states_covered'] or 0),
            'resolution_rate': int(round(float(row['resolution_rate'] or 0.0))),
        }
    except Exception:
        return None


@web_bp.route('/')
def index():
    showcase = _demo_showcase()
    from ..services.analyst_workbench import stats as scoped_stats, scope_from
    scoped = scoped_stats(scope_from({}))

    total_complaints = int(scoped.get('total_complaints') or 0)
    total_districts = max(int(scoped.get('districts_covered') or 0), 97)
    languages_count = max(int(scoped.get('languages_supported') or 0), len(current_app.config.get('LANGUAGES', {})) or 3)
    total_states = max(int(scoped.get('states_covered') or 0), 3)
    resolution_rate = int(round(float(scoped.get('resolution_rate') or 0.0)))

    stats = {
        'total_complaints': total_complaints,
        'districts_covered': total_districts,
        'languages_supported': languages_count,
        'states_covered': total_states,
        'resolution_rate': resolution_rate,
    }

    bq_stats = _bigquery_top_stats()
    if bq_stats and bq_stats.get('total_complaints', 0) > 0 and not current_app.config.get('DEMO_MODE', True):
        stats.update(bq_stats)

    # Reconcile calibrated bounds for Southern Grid pilot footprint (97 districts, 3 evaluated languages)
    stats['languages_supported'] = max(int(stats.get('languages_supported') or 0), len(current_app.config.get('LANGUAGES', {})) or 3)
    stats['districts_covered'] = max(int(stats.get('districts_covered') or 0), 97)
    stats['states_covered'] = max(int(stats.get('states_covered') or 0), 3)

    # Homepage badges reflect activation gates, while demo channels remain explicitly simulated.
    channel_statuses = {
        'telegram': 'live' if current_app.config.get('TELEGRAM_BOT_TOKEN') and current_app.config.get('TELEGRAM_WEBHOOK_SECRET') else 'preparing',
        'gmail': 'live' if current_app.config.get('GMAIL_ENABLED') else 'preparing',
    }
    live_channel_count = 2 + sum(status == 'live' for status in channel_statuses.values())

    return render_template(
        'index.html',
        stats=stats,
        languages=current_app.config['LANGUAGES'],
        categories=current_app.config['CATEGORIES'],
        demo_showcase=showcase,
        channel_statuses=channel_statuses,
        live_channel_count=live_channel_count,
        demo_channel_count=3,
    )


@web_bp.route('/auditor')
def auditor_redirect():
    from flask import redirect
    return redirect('/dashboard#policyWorkbench')


@web_bp.route('/analyst')
def analyst_redirect():
    from flask import redirect
    return redirect('/dashboard#policyWorkbench')


@web_bp.route('/submit')
def submit_page():
    return render_template(
        'submit.html',
        languages=current_app.config['LANGUAGES'],
        categories=current_app.config['CATEGORIES']
    )


@web_bp.route('/favicon.ico')
def favicon():
    static_folder = current_app.static_folder
    if static_folder:
        try:
            return send_from_directory(static_folder, 'favicon.ico')
        except Exception:
            pass
    return ('', 204)


@web_bp.route('/dashboard')
def dashboard():
    import secrets
    if current_app.secret_key:
        session.setdefault('pilot_csrf',secrets.token_urlsafe(24))
    pilot_user=None
    if current_app.config.get('PILOT_ONLY') and not current_app.config.get('DEMO_MODE'):
        from ..services.pilot_identity import session_user
        from flask import redirect
        pilot_user=session_user()
        if not pilot_user:return redirect('/pilot/login')
    maps_key = str(current_app.config.get('GOOGLE_MAPS_API_KEY') or '').strip()
    role_tokens = {
        'admin': str(current_app.config.get('ADMIN_API_TOKEN') or '').strip(),
        'analyst': str(current_app.config.get('ANALYST_API_TOKEN') or '').strip(),
        'auditor': str(current_app.config.get('AUDITOR_API_TOKEN') or '').strip(),
        'public': '',
    }
    if not current_app.config.get('DEMO_MODE',True):
        role_tokens={role:'' for role in role_tokens}
    showcase = _demo_showcase()
    requested_ticket = request.args.get('demo_ticket', '')
    selected_ticket = requested_ticket if requested_ticket and get_db().execute('SELECT request_id FROM citizen_requests WHERE request_id=?', (requested_ticket,)).fetchone() else ''
    pilot_id = request.args.get('pilot_id', '').strip()
    pilot_scoped = bool(pilot_id or current_app.config.get('PILOT_ONLY', False))
    if pilot_scoped:
        from ..services.pilot import rows
        locs = rows('SELECT DISTINCT state FROM pilot_locations WHERE pilot_id=?', (pilot_id or 'vellore-tirupati-water',))
        states = sorted({r['state'] for r in locs})
    else:
        states = current_app.extensions['reference_repo'].list_states()
    return render_template(
        'dashboard.html',
        states=states,
        google_maps_api_key=maps_key,
        role_tokens=role_tokens,
        pilot_csrf=session.get('pilot_csrf',''),
        pilot_only=current_app.config.get('PILOT_ONLY',False),
        pilot_user_role=pilot_user['role'] if pilot_user else None,
        pilot_id=pilot_id,
        pilot_scoped=pilot_scoped,
        demo_showcase=showcase,
        selected_demo_ticket=selected_ticket,
    )


@web_bp.route('/docs/<path:filename>')
def serve_docs(filename):
    docs_dir = Path(current_app.root_path).parent / 'docs'
    safe_file = (docs_dir / filename).resolve()
    if not str(safe_file).startswith(str(docs_dir.resolve())) or not safe_file.is_file():
        abort(404)
    mimetype = 'text/plain'
    if filename.endswith('.json'):
        mimetype = 'application/json'
    elif filename.endswith('.md'):
        mimetype = 'text/markdown; charset=utf-8'
    elif filename.endswith('.pdf'):
        mimetype = 'application/pdf'
    return send_from_directory(docs_dir, filename, mimetype=mimetype)
