import json
import os
from datetime import datetime, timezone

from visbharat import create_app
from visbharat.db import init_db, seed_default_users


REPORT_PATH = os.path.join('docs', 'reports', 'Phase5_GoLive_Readiness_Report.md')


def _seed_requests(client):
    payloads = [
        {'text': 'Road repair urgently needed in ward 7', 'language': 'en', 'district': 'Chennai', 'source': 'Web'},
        {'text': 'No drinking water in village cluster', 'language': 'en', 'district': 'Koraput', 'source': 'Web'},
        {'text': 'Power cuts impacting hospital operations', 'language': 'en', 'district': 'Hyderabad', 'source': 'Web'},
        {'text': 'Drainage overflow after rainfall', 'language': 'en', 'district': 'Chennai', 'source': 'Web'},
        {'text': 'Road bridge access broken', 'language': 'en', 'district': 'Visakhapatnam', 'source': 'Web'},
        {'text': 'Water tanker dependency is increasing', 'language': 'en', 'district': 'Tirupati', 'source': 'Web'},
        {'text': 'Street lights not working near school', 'language': 'en', 'district': 'Chennai', 'source': 'Web'},
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
        '# Phase 5 Go-Live Readiness Report (Public Transparency Portal)',
        '',
        f"Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        '',
        '## Scope',
        'This production completion pass validates the Phase 5 public transparency endpoints:',
        '- `GET /api/public/transparency/summary`',
        '- `GET /api/public/transparency/districts`',
        '- `GET /api/public/transparency/categories`',
        '',
        'Burn-in runner:',
        '- `scripts/burnin_phase5_transparency.py`',
        '',
        '## Environment',
        '- Runtime: local Flask app context + SQLite burn-in DB',
        f"- Burn-in DB: `{report['db_path']}`",
        '- Auth model: public endpoints (no bearer token required)',
        '',
        '## Checklist Results',
        '',
        '| Check | Result | Evidence |',
        '|---|---|---|',
        f"| Public accessibility | {status(checks['public_accessibility']['pass'])} | summary `{checks['public_accessibility']['summary_status']}`, districts `{checks['public_accessibility']['districts_status']}`, categories `{checks['public_accessibility']['categories_status']}` |",
        f"| Aggregation contract integrity | {status(checks['contract_integrity']['pass'])} | fields present + summary total `{checks['contract_integrity']['total_requests']}` |",
        f"| Anonymization suppression guardrail | {status(checks['anonymization_suppression']['pass'])} | published groups `{checks['anonymization_suppression']['published_groups']}`, suppressed `{checks['anonymization_suppression']['suppressed_groups']}` |",
        f"| Input validation behavior | {status(checks['input_validation']['pass'])} | invalid limit status `{checks['input_validation']['invalid_limit_status']}` |",
        f"| Repeated-call stability | {status(checks['repeated_call_stability']['pass'])} | `{checks['repeated_call_stability']['iterations']}` iterations, failures `{checks['repeated_call_stability']['failed_iterations']}` |",
        f"| Sensitive field redaction | {status(checks['sensitive_field_redaction']['pass'])} | raw text/submitted_by excluded in public payloads |",
        '',
        '## Raw Burn-in Summary',
        f"- `all_passed`: `{str(summary['all_passed']).lower()}`",
        f"- `passed_checks`: `{summary['passed_checks']}`",
        f"- `total_checks`: `{summary['total_checks']}`",
        '',
        '## Go-Live Decision',
        '**READY (Phase 5 Public Transparency Portal)**' if summary['all_passed'] else '**NOT READY (Phase 5 Public Transparency Portal)**',
        '',
        'The public transparency surface is validated for controlled rollout based on local burn-in checks.' if summary['all_passed'] else 'One or more checks failed. Review burn-in output before rollout.',
        '',
        '## Operational Notes',
        '- Keep anonymization threshold (`PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE`) >= 3 in production.',
        '- Monitor for abnormal spikes in suppressed-group ratio and endpoint latency.',
        '- Uses timezone-aware UTC timestamps across burn-in flows.',
        '',
    ]
    return '\n'.join(lines)


def main():
    db_path = os.path.join(os.getcwd(), 'tests', 'burnin_phase5_transparency.db')
    if os.path.exists(db_path):
        os.remove(db_path)

    app = create_app()
    app.config.update(
        TESTING=True,
        DATABASE_PATH=db_path,
        DATABASE_URL='',
        PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE=3,
        PUBLIC_TRANSPARENCY_MAX_LIMIT=200,
    )

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

        _seed_requests(client)

        # 1) Public accessibility
        summary_resp = client.get('/api/public/transparency/summary')
        districts_resp = client.get('/api/public/transparency/districts?limit=20')
        categories_resp = client.get('/api/public/transparency/categories')

        report['checks']['public_accessibility'] = {
            'pass': summary_resp.status_code == 200 and districts_resp.status_code == 200 and categories_resp.status_code == 200,
            'summary_status': summary_resp.status_code,
            'districts_status': districts_resp.status_code,
            'categories_status': categories_resp.status_code,
        }

        # 2) Contract integrity
        summary_payload = summary_resp.get_json() if summary_resp.is_json else {}
        districts_payload = districts_resp.get_json() if districts_resp.is_json else {}
        categories_payload = categories_resp.get_json() if categories_resp.is_json else {}

        contract_pass = (
            isinstance(summary_payload, dict)
            and isinstance(districts_payload, dict)
            and isinstance(categories_payload, dict)
            and all(key in summary_payload for key in ['success', 'summary'])
            and all(key in districts_payload for key in ['success', 'items', 'meta'])
            and all(key in categories_payload for key in ['success', 'items', 'meta'])
            and all(key in (summary_payload.get('summary') or {}) for key in ['total_requests', 'published_request_count', 'anonymization'])
        )

        report['checks']['contract_integrity'] = {
            'pass': contract_pass,
            'total_requests': int((summary_payload.get('summary') or {}).get('total_requests', 0) or 0),
        }

        # 3) Anonymization suppression
        district_items = districts_payload.get('items', []) if isinstance(districts_payload, dict) else []
        meta = districts_payload.get('meta', {}) if isinstance(districts_payload, dict) else {}

        suppression_pass = (
            all(int(item.get('request_count', 0)) >= 3 for item in district_items)
            and int(meta.get('min_group_size', 0) or 0) == 3
            and int(meta.get('suppressed_groups', 0) or 0) >= 1
        )

        report['checks']['anonymization_suppression'] = {
            'pass': suppression_pass,
            'published_groups': len(district_items),
            'suppressed_groups': int(meta.get('suppressed_groups', 0) or 0),
        }

        # 4) Input validation
        invalid_limit = client.get('/api/public/transparency/districts?limit=abc')
        report['checks']['input_validation'] = {
            'pass': invalid_limit.status_code == 400,
            'invalid_limit_status': invalid_limit.status_code,
        }

        # 5) Repeated-call stability
        iterations = 120
        failures = 0
        for index in range(iterations):
            d_limit = 5 + (index % 10)
            s = client.get('/api/public/transparency/summary')
            d = client.get(f'/api/public/transparency/districts?limit={d_limit}')
            c = client.get('/api/public/transparency/categories')
            if s.status_code != 200 or d.status_code != 200 or c.status_code != 200:
                failures += 1

        report['checks']['repeated_call_stability'] = {
            'pass': failures == 0,
            'iterations': iterations,
            'failed_iterations': failures,
        }

        # 6) Sensitive field redaction (no raw text / submitted_by leakage)
        public_blob = json.dumps(
            {
                'summary': summary_payload,
                'districts': districts_payload,
                'categories': categories_payload,
            }
        ).lower()

        redaction_pass = 'original_text' not in public_blob and 'submitted_by' not in public_blob

        # ensure secure private API still requires auth to access raw records
        private_resp = client.get('/api/v1/requests')
        redaction_pass = redaction_pass and private_resp.status_code == 401

        report['checks']['sensitive_field_redaction'] = {
            'pass': redaction_pass,
            'private_secure_status': private_resp.status_code,
            'admin_secure_probe_status': client.get('/api/v1/requests', headers=_auth(admin_token)).status_code,
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




