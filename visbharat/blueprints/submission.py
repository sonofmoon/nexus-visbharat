"""Read-only submission evidence. Detailed citizen journeys require analyst access."""
import json
from pathlib import Path
from flask import Blueprint, current_app, jsonify, render_template
from ..db import get_db
from ..services.runtime_readiness import persistence, disposable_showcase_allowed
from ..services.provider_evidence import status

submission_bp = Blueprint('submission', __name__)


@submission_bp.get('/healthz')
def health():
    return jsonify(success=True, status='running')


@submission_bp.get('/readyz')
def ready():
    try:
        get_db().execute('SELECT 1 FROM citizen_requests LIMIT 1').fetchone()
        get_db().execute('SELECT 1 FROM provider_invocations LIMIT 1').fetchone()
        storage = persistence(current_app.config)
        allowed = storage['operational_ready'] or disposable_showcase_allowed(current_app.config)
        return jsonify(success=bool(allowed), storage=storage), 200 if allowed else 503
    except Exception:
        return jsonify(success=False, status='database_not_ready'), 503


@submission_bp.get('/submission')
def submission():
    return render_template('submission.html')


@submission_bp.get('/api/submission/readiness')
def readiness():
    root = Path(current_app.root_path).parent
    def artifact(name):
        path = root / 'docs' / 'evaluation' / name
        if not path.exists(): return {}
        return json.loads(path.read_text(encoding='utf-8'))
    quality = artifact('quality.json')
    baseline = artifact('baseline-quality.json')
    repo = current_app.extensions['reference_repo']
    return jsonify(success=True, storage=persistence(current_app.config), providers=status(),
        scope={'languages': current_app.config['LANGUAGES'],
               'states': current_app.config['ALLOWED_STATE_TO_DISTRICTS'],
               'meaning': 'Configured demonstration coverage, not government adoption.'},
        dataset={'rows': len(repo.df_complaints), 'data_mode': 'synthetic_reference_corpus',
                 'provenance_url': '/static/data/demo_showcase.json',
                 'limitations': 'Prepared report volumes and outcomes are illustrative. Reference indicators require publisher and boundary validation.'},
        evaluation={'status': quality.get('status', 'not_available'), 'overall': quality.get('overall'),
                    'independent_human_review': quality.get('independent_human_review', False),
                    'not_measured': quality.get('not_measured', {}), 'limitations': quality.get('limitations', []),
                    'baseline': baseline.get('overall'), 'source': '/docs/evaluation/quality.json'},
        messaging={'status': 'delivery_demonstration_required',
                   'meaning': 'A configured connector or synthetic channel label does not establish successful message delivery.'})
