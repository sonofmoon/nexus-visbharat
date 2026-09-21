import json
import os
from datetime import datetime, timezone

from visbharat import create_app
from visbharat.db import init_db, seed_default_users, get_db


REPORT_PATH = os.path.join('docs', 'reports', 'Phase4_GoLive_Readiness_Report.md')


def _seed_requests(client):
    payloads = [
        {'text': 'Road repair urgently needed in ward 7', 'language': 'en', 'district': 'Chennai', 'source': 'Web'},
        {'text': 'No drinking water in village cluster', 'language': 'en', 'district': 'Koraput', 'source': 'Web'},
        {'text': 'Power cuts impacting hospital operations', 'language': 'en', 'district': 'Hyderabad', 'source': 'Web'},
        {'text': 'Drainage overflow after rainfall', 'language': 'en', 'district': 'Chennai', 'source': 'Web'},
        {'text': 'Road bridge access broken', 'language': 'en', 'district': 'Visakhapatnam', 'source': 'Web'},
        {'text': 'Water tanker dependency is increasing', 'language': 'en', 'district': 'Tirupati', 'source': 'Web'},
    ]
    for payload in payloads:
        client.post('/api/submit', json=payload)


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _render_report(report):
    checks = report['checks']
    summary = report['summary']

    def status(val):
        return 'PASS' if val else 'FAIL'

    lines = [
        '# Phase 4 Go-Live Readiness Report (Policy Decision Workflow)',
        '',
        f"Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        '',
        '## Scope',
        'This production completion pass validates the Phase 4 Policy Decision Workflow endpoints:',
        '- POST /api/v1/policy/decisions/draft',
        '- GET /api/v1/policy/decisions',
        '- POST /api/v1/policy/decisions/<decision_id>/approve',
        '- POST /api/v1/policy/decisions/<decision_id>/reject',
        '',
        'Burn-in runner:',
        '- scripts/burnin_phase4_policy_workflow.py',
        '',
        '## Environment',
        '- Runtime: local Flask app context + SQLite burn-in DB',
        f"- Burn-in DB: {report['db_path']}",
        '- Auth model: RBAC bearer token (dmin, nalyst, uditor)',
        '',
        '## Checklist Results',
        '',
        '| Check | Result | Evidence |',
        '|---|---|---|',
        f"| Auth enforcement on Phase 4 endpoints | {status(checks['auth_enforcement']['pass'])} | unauthenticated calls returned {checks['auth_enforcement']['draft_status']} and {checks['auth_enforcement']['list_status']} |",
        f"| RBAC boundaries | {status(checks['rbac_boundaries']['pass'])} | analyst draft {checks['rbac_boundaries']['analyst_draft_status']}, auditor draft {checks['rbac_boundaries']['auditor_draft_status']}, analyst approve {checks['rbac_boundaries']['analyst_approve_status']} |",
        f"| Draft/list contract integrity | {status(checks['contract_integrity']['pass'])} | create status {checks['contract_integrity']['create_status']}, list status {checks['contract_integrity']['list_status']}, created {checks['contract_integrity']['created_count']} |",
        f"| Decision lifecycle transitions | {status(checks['lifecycle_transitions']['pass'])} | approve {checks['lifecycle_transitions']['approve_status']}, reject {checks['lifecycle_transitions']['reject_status']}, reapprove {checks['lifecycle_transitions']['reapprove_status']} |",
        f"| Repeated list stability | {status(checks['repeated_list_stability']['pass'])} | {checks['repeated_list_stability']['iterations']} iterations, failures {checks['repeated_list_stability']['failed_iterations']} |",
        f"| Audit trail generation | {status(checks['audit_trail']['pass'])} | created {checks['audit_trail']['draft_created_events']}, viewed {checks['audit_trail']['viewed_events']}, approved {checks['audit_trail']['approved_events']}, rejected {checks['audit_trail']['rejected_events']} |",
        '',
        '## Raw Burn-in Summary',
        f"- ll_passed: {str(summary['all_passed']).lower()}",
        f"- passed_checks: {summary['passed_checks']}",
        f"- 	otal_checks: {summary['total_checks']}",
        '',
        '## Go-Live Decision',
        '**READY (Phase 4 Policy Decision Workflow)**' if summary['all_passed'] else '**NOT READY (Phase 4 Policy Decision Workflow)**',
        '',
        'The Phase 4 policy decision workflow is validated for controlled rollout based on current local burn-in checks.' if summary['all_passed'] else 'One or more checks failed. Review JSON output and fix before rollout.',
        '',
        '## Operational Notes',
        '- Keep admin approval actions tightly restricted and token rotation enforced.',
        '- Monitor lifecycle audit-event rates and approval/rejection distribution for anomalies.',
        '- Uses timezone-aware UTC timestamps across burn-in flows.',
        '',
    ]
    return '\n'.join(lines)


def main():
    db_path = os.path.join(os.getcwd(), 'tests', 'burnin_phase4_policy.db')
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
        auditor_token = app.config['AUDITOR_API_TOKEN']

        _seed_requests(client)

        # 1) Auth enforcement
        unauth_draft = client.post('/api/v1/policy/decisions/draft')
        unauth_list = client.get('/api/v1/policy/decisions')
        report['checks']['auth_enforcement'] = {
            'pass': unauth_draft.status_code == 401 and unauth_list.status_code == 401,
            'draft_status': unauth_draft.status_code,
            'list_status': unauth_list.status_code,
        }

        # 2) RBAC boundaries
        analyst_draft = client.post('/api/v1/policy/decisions/draft?limit=2&total_budget_lakh=900', headers=_auth(analyst_token))
        analyst_payload = analyst_draft.get_json() or {}
        decision_id = (analyst_payload.get('decisions') or [{}])[0].get('decision_id')

        auditor_draft = client.post('/api/v1/policy/decisions/draft?limit=1', headers=_auth(auditor_token))
        analyst_approve = client.post(f'/api/v1/policy/decisions/{decision_id}/approve', headers=_auth(analyst_token)) if decision_id else None

        report['checks']['rbac_boundaries'] = {
            'pass': analyst_draft.status_code == 200 and auditor_draft.status_code == 403 and (analyst_approve is not None and analyst_approve.status_code == 403),
            'analyst_draft_status': analyst_draft.status_code,
            'auditor_draft_status': auditor_draft.status_code,
            'analyst_approve_status': analyst_approve.status_code if analyst_approve is not None else -1,
        }

        # 3) Contract integrity
        create_resp = client.post('/api/v1/policy/decisions/draft?limit=3&total_budget_lakh=1100', headers=_auth(analyst_token))
        list_resp = client.get('/api/v1/policy/decisions?status=draft&limit=5', headers=_auth(auditor_token))
        create_payload = create_resp.get_json() if create_resp.is_json else {}
        list_payload = list_resp.get_json() if list_resp.is_json else {}

        created_count = int(create_payload.get('created_count', 0) or 0) if isinstance(create_payload, dict) else 0
        list_items = list_payload.get('items', []) if isinstance(list_payload, dict) else []

        contract_ok = (
            create_resp.status_code == 200
            and list_resp.status_code == 200
            and isinstance(create_payload.get('decisions', []), list)
            and 'success' in create_payload
            and 'created_count' in create_payload
            and 'items' in list_payload
        )

        report['checks']['contract_integrity'] = {
            'pass': contract_ok,
            'create_status': create_resp.status_code,
            'list_status': list_resp.status_code,
            'created_count': created_count,
            'listed_count': len(list_items),
        }

        # 4) Lifecycle transitions
        lifecycle_decisions = create_payload.get('decisions', []) if isinstance(create_payload, dict) else []
        approve_id = lifecycle_decisions[0]['decision_id'] if len(lifecycle_decisions) > 0 else None
        reject_id = lifecycle_decisions[1]['decision_id'] if len(lifecycle_decisions) > 1 else None

        approve_resp = client.post(f'/api/v1/policy/decisions/{approve_id}/approve', headers=_auth(admin_token)) if approve_id else None
        reject_resp = client.post(
            f'/api/v1/policy/decisions/{reject_id}/reject',
            headers=_auth(admin_token),
            json={'notes': 'Deferred by burn-in validation pass'},
        ) if reject_id else None
        reapprove_resp = client.post(f'/api/v1/policy/decisions/{approve_id}/approve', headers=_auth(admin_token)) if approve_id else None

        lifecycle_pass = (
            approve_resp is not None and approve_resp.status_code == 200
            and reject_resp is not None and reject_resp.status_code == 200
            and reapprove_resp is not None and reapprove_resp.status_code == 400
        )

        report['checks']['lifecycle_transitions'] = {
            'pass': lifecycle_pass,
            'approve_status': approve_resp.status_code if approve_resp is not None else -1,
            'reject_status': reject_resp.status_code if reject_resp is not None else -1,
            'reapprove_status': reapprove_resp.status_code if reapprove_resp is not None else -1,
        }

        # 5) Repeated list stability
        total_calls = 100
        failures = 0
        for index in range(total_calls):
            status_filter = ['draft', 'approved', 'rejected'][index % 3]
            response = client.get(
                f'/api/v1/policy/decisions?status={status_filter}&limit=10',
                headers=_auth(analyst_token),
            )
            if response.status_code != 200:
                failures += 1

        report['checks']['repeated_list_stability'] = {
            'pass': failures == 0,
            'iterations': total_calls,
            'failed_iterations': failures,
        }

        # 6) Audit trail
        db = get_db()
        row = db.execute(
            '''
            SELECT
              SUM(CASE WHEN action = 'policy_decisions_draft_created' THEN 1 ELSE 0 END) AS draft_created_events,
              SUM(CASE WHEN action = 'policy_decisions_viewed' THEN 1 ELSE 0 END) AS viewed_events,
              SUM(CASE WHEN action = 'policy_decision_approved' THEN 1 ELSE 0 END) AS approved_events,
              SUM(CASE WHEN action = 'policy_decision_rejected' THEN 1 ELSE 0 END) AS rejected_events
            FROM audit_logs
            '''
        ).fetchone()

        draft_created_events = int((row['draft_created_events'] or 0) if row else 0)
        viewed_events = int((row['viewed_events'] or 0) if row else 0)
        approved_events = int((row['approved_events'] or 0) if row else 0)
        rejected_events = int((row['rejected_events'] or 0) if row else 0)

        report['checks']['audit_trail'] = {
            'pass': draft_created_events > 0 and viewed_events > 0 and approved_events > 0 and rejected_events > 0,
            'draft_created_events': draft_created_events,
            'viewed_events': viewed_events,
            'approved_events': approved_events,
            'rejected_events': rejected_events,
        }

    passes = [item.get('pass', False) for item in report['checks'].values()]
    report['summary'] = {
        'all_passed': all(passes),
        'passed_checks': sum(1 for value in passes if value),
        'total_checks': len(passes),
    }

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, 'w', encoding='utf-8') as report_file:
        report_file.write(_render_report(report))

    print(json.dumps(report, indent=2))
    print(f'\nGenerated markdown report: {REPORT_PATH}')


if __name__ == '__main__':
    main()




