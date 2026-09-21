import json
import os
from datetime import datetime, timezone

from visbharat import create_app
from visbharat.db import init_db, seed_default_users, get_db


REPORT_PATH = os.path.join('docs', 'reports', 'Phase2_GoLive_Readiness_Report.md')


def _seed_requests(client):
    payloads = [
        {'text': 'Road blocked near market', 'language': 'en', 'district': 'Chennai', 'source': 'Web'},
        {'text': 'No water supply in ward 9', 'language': 'en', 'district': 'Koraput', 'source': 'Web'},
        {'text': 'Power outage in hospital area', 'language': 'en', 'district': 'Hyderabad', 'source': 'Web'},
    ]
    for payload in payloads:
        client.post('/api/submit', json=payload)


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _render_report(report):
    checks = report['checks']
    summary = report['summary']

    def status(value):
        return 'PASS' if value else 'FAIL'

    lines = [
        '# Phase 2 Go-Live Readiness Report (Processing Layer)',
        '',
        f"Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        '',
        '## Scope',
        'This burn-in validates local processing-layer completeness and pipeline ops APIs:',
        '- `GET /api/ai/status`',
        '- `POST /api/translate`',
        '- `POST /api/classify`',
        '- `POST /api/submit-voice` (strict validation path)',
        '- `POST /api/dialogflow/session`',
        '- `GET /api/v1/pipeline/jobs`',
        '- `GET /api/v1/pipeline/metrics`',
        '',
        'Burn-in runner:',
        '- `scripts/burnin_phase2_processing.py`',
        '',
        '## Environment',
        '- Runtime: local Flask app context + SQLite burn-in DB',
        f"- Burn-in DB: `{report['db_path']}`",
        '- Auth model: RBAC bearer token for pipeline secure endpoints',
        '',
        '## Checklist Results',
        '',
        '| Check | Result | Evidence |',
        '|---|---|---|',
        f"| AI status features | {status(checks['ai_status_features']['pass'])} | mode `{checks['ai_status_features']['mode']}`, feature flags present |",
        f"| Core processing contracts | {status(checks['core_processing_contracts']['pass'])} | translate `{checks['core_processing_contracts']['translate_status']}`, classify `{checks['core_processing_contracts']['classify_status']}`, dialogflow `{checks['core_processing_contracts']['dialogflow_status']}` |",
        f"| Voice strict validation | {status(checks['voice_validation_guardrail']['pass'])} | missing-audio submit-voice returns `{checks['voice_validation_guardrail']['submit_voice_status']}` |",
        f"| Pipeline secure endpoints | {status(checks['pipeline_secure_endpoints']['pass'])} | unauth jobs `{checks['pipeline_secure_endpoints']['unauth_jobs_status']}`, auth jobs `{checks['pipeline_secure_endpoints']['auth_jobs_status']}`, metrics `{checks['pipeline_secure_endpoints']['metrics_status']}` |",
        f"| Repeated processing stability | {status(checks['repeated_processing_stability']['pass'])} | `{checks['repeated_processing_stability']['iterations']}` iterations, failures `{checks['repeated_processing_stability']['failed_iterations']}` |",
        f"| Processing audit trail | {status(checks['processing_audit_trail']['pass'])} | citizen submit events `{checks['processing_audit_trail']['citizen_submit_events']}` |",
        '',
        '## Raw Burn-in Summary',
        f"- `all_passed`: `{str(summary['all_passed']).lower()}`",
        f"- `passed_checks`: `{summary['passed_checks']}`",
        f"- `total_checks`: `{summary['total_checks']}`",
        '',
        '## Go-Live Decision',
        '**READY (Phase 2 Processing Layer)**' if summary['all_passed'] else '**NOT READY (Phase 2 Processing Layer)**',
        '',
        '## Operational Notes',
        '- Processing layer is fully usable in local deterministic/simulated mode.',
        '- Maintain strict validation for voice endpoint to avoid malformed payload ingestion.',
        '- Uses timezone-aware UTC timestamps across burn-in flows.',
        '',
    ]
    return '\n'.join(lines)


def main():
    db_path = os.path.join(os.getcwd(), 'tests', 'burnin_phase2_processing.db')
    if os.path.exists(db_path):
        os.remove(db_path)

    app = create_app()
    app.config.update(TESTING=True, DATABASE_PATH=db_path, DATABASE_URL='')

    report = {
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'db_path': db_path,
        'checks': {},
        'summary': {},
    }

    with app.app_context():
        init_db()
        seed_default_users()

        client = app.test_client()
        admin_token = app.config['ADMIN_API_TOKEN']
        analyst_token = app.config['ANALYST_API_TOKEN']

        _seed_requests(client)

        # 1) AI status features completeness
        ai_status = client.get('/api/ai/status')
        ai_payload = ai_status.get_json() if ai_status.is_json else {}
        features = ai_payload.get('supported_features', []) if isinstance(ai_payload, dict) else []
        ai_pass = (
            ai_status.status_code == 200
            and 'multilingual_translation' in features
            and 'request_classification' in features
            and 'voice_input_live_stt' in features
            and 'dialogflow_cx_guided_conversation' in features
        )
        report['checks']['ai_status_features'] = {
            'pass': ai_pass,
            'status': ai_status.status_code,
            'mode': ai_payload.get('mode') if isinstance(ai_payload, dict) else None,
        }

        # 2) Core processing contracts
        translate = client.post('/api/translate', json={'text': 'No water in my area', 'source_lang': 'en', 'target_lang': 'en'})
        classify = client.post('/api/classify', json={'text': 'Road collapsed near school', 'language': 'en'})
        dialogflow = client.post('/api/dialogflow/session', json={'session_state': 'collect_issue', 'message': 'There is a serious drainage overflow in my ward', 'language': 'en', 'channel': 'web'})

        t_payload = translate.get_json() if translate.is_json else {}
        c_payload = classify.get_json() if classify.is_json else {}
        d_payload = dialogflow.get_json() if dialogflow.is_json else {}

        core_pass = (
            translate.status_code == 200
            and classify.status_code == 200
            and dialogflow.status_code == 200
            and 'result' in t_payload
            and 'result' in c_payload
            and 'session' in d_payload
        )

        report['checks']['core_processing_contracts'] = {
            'pass': core_pass,
            'translate_status': translate.status_code,
            'classify_status': classify.status_code,
            'dialogflow_status': dialogflow.status_code,
        }

        # 3) Voice validation guardrail
        submit_voice = client.post('/api/submit-voice', json={'language': 'en', 'district': 'Chennai', 'source': 'IVR'})
        report['checks']['voice_validation_guardrail'] = {
            'pass': submit_voice.status_code == 400,
            'submit_voice_status': submit_voice.status_code,
        }

        # 4) Pipeline secure endpoints
        unauth_jobs = client.get('/api/v1/pipeline/jobs')
        auth_jobs = client.get('/api/v1/pipeline/jobs', headers=_auth(analyst_token))
        metrics = client.get('/api/v1/pipeline/metrics', headers=_auth(admin_token))

        pipeline_pass = (
            unauth_jobs.status_code == 401
            and auth_jobs.status_code == 200
            and metrics.status_code == 200
        )
        report['checks']['pipeline_secure_endpoints'] = {
            'pass': pipeline_pass,
            'unauth_jobs_status': unauth_jobs.status_code,
            'auth_jobs_status': auth_jobs.status_code,
            'metrics_status': metrics.status_code,
        }

        # 5) Repeated processing stability
        iterations = 100
        failures = 0
        for _ in range(iterations):
            r1 = client.post('/api/translate', json={'text': 'road issue', 'source_lang': 'en', 'target_lang': 'en'})
            r2 = client.post('/api/classify', json={'text': 'water crisis in village', 'language': 'en'})
            r3 = client.post('/api/dialogflow/session', json={'session_state': 'collect_issue', 'message': 'Need road repair urgently', 'language': 'en', 'channel': 'web'})
            if r1.status_code != 200 or r2.status_code != 200 or r3.status_code != 200:
                failures += 1

        report['checks']['repeated_processing_stability'] = {
            'pass': failures == 0,
            'iterations': iterations,
            'failed_iterations': failures,
        }

        # 6) Processing audit trail (submit events)
        db = get_db()
        row = db.execute(
            """
            SELECT SUM(CASE WHEN action = 'citizen_request_submitted' THEN 1 ELSE 0 END) AS citizen_submit_events
            FROM audit_logs
            """
        ).fetchone()

        submit_events = int((row['citizen_submit_events'] or 0) if row else 0)
        report['checks']['processing_audit_trail'] = {
            'pass': submit_events > 0,
            'citizen_submit_events': submit_events,
        }

    checks = [item.get('pass', False) for item in report['checks'].values()]
    report['summary'] = {
        'all_passed': all(checks),
        'passed_checks': sum(1 for item in checks if item),
        'total_checks': len(checks),
    }

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as report_file:
        report_file.write(_render_report(report))

    print(json.dumps(report, indent=2))
    print(f'\nGenerated markdown report: {REPORT_PATH}')


if __name__ == '__main__':
    main()




