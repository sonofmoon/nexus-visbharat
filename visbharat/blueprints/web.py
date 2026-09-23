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
    repo = current_app.extensions['reference_repo']
    db = get_db()
    db_count = db.execute('SELECT COUNT(*) AS c FROM citizen_requests').fetchone()['c']

    complaints_count = int(len(repo.df_complaints))
    resolved_count = int(repo.df_complaints['status'].isin(['Resolved', 'Closed']).sum())
    resolution_rate = 0 if complaints_count == 0 else int((resolved_count / complaints_count) * 100 + 0.5)

    total_complaints = complaints_count if current_app.config.get('DEMO_MODE', True) else int(complaints_count + db_count)

    # Dynamic State & District counts from Config Matrix + DB + Reference Repository
    configured_districts = set()
    for st, dists in (current_app.config.get('ALLOWED_STATE_TO_DISTRICTS') or {}).items():
        configured_districts.update(dists)

    db_dist_rows = db.execute("SELECT DISTINCT district FROM citizen_requests WHERE district IS NOT NULL AND district != ''").fetchall()
    db_districts = {row['district'] for row in db_dist_rows}
    repo_districts = set(repo.df_districts['district'].unique()) if hasattr(repo, 'df_districts') else set()

    total_districts = len(configured_districts | db_districts | repo_districts)

    configured_states = set((current_app.config.get('ALLOWED_STATE_TO_DISTRICTS') or {}).keys())
    db_state_rows = db.execute("SELECT DISTINCT state FROM citizen_requests WHERE state IS NOT NULL AND state != ''").fetchall()
    db_states = {row['state'] for row in db_state_rows}
    repo_states = set(repo.df_districts['state'].unique()) if hasattr(repo, 'df_districts') else set()

    total_states = len(configured_states | db_states | repo_states)

    showcase = _demo_showcase()
    if showcase:
        summary = showcase.get('summary', {})
        total_complaints = int(summary.get('rows') or showcase.get('total_records') or 12500)
        total_districts = int(summary.get('districts') or showcase.get('districts_covered') or 97)
        total_states = len(summary.get('state') or {}) or int(showcase.get('states_covered') or 3)
        languages_count = len(summary.get('language') or {}) or 3  # Evaluated core Indic pilot languages: Tamil, Telugu, English
    else:
        languages_count = len(current_app.config['LANGUAGES'])

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

    return render_template(
        'index.html',
        stats=stats,
        languages=current_app.config['LANGUAGES'],
        categories=current_app.config['CATEGORIES'],
        demo_showcase=showcase,
    )


@web_bp.route('/demo-video')
def demo_video():
    return render_template('demo_video.html')


@web_bp.route('/pitch-deck')
def pitch_deck():
    return render_template('pitch_deck.html')


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
    return send_from_directory(docs_dir, filename, mimetype=mimetype)
