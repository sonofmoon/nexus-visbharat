import json
import os
from datetime import datetime, timezone

from visbharat import create_app
from visbharat.db import init_db, seed_default_users, get_db


REPORT_PATH = os.path.join('docs', 'reports', 'Phase3_GoLive_Readiness_Report.md')


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

    def status(value):
        return 'PASS' if value else 'FAIL'

    lines = [
        '# Phase 3 Go-Live Readiness Report (Intelligence + Policy)',
        '',
        f"Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        '',
        '## Scope',
        'This production completion pass validates the Phase 3 intelligence + policy endpoints:',
        '- `GET /api/v1/intelligence/gap-analysis`',
        '- `GET /api/v1/intelligence/hotspot-predictions`',
        '- `GET /api/v1/intelligence/bigquery-analytics`',
        '- `GET /api/v1/intelligence/vertex-predictions`',
        '- `GET /api/v1/policy/priority-rankings`',
        '- `GET /api/v1/policy/impact-brief`',
        '',
        'Burn-in runner:',
        '- `scripts/burnin_phase3_policy.py`',
        '',
        '## Environment',
        '- Runtime: local Flask app context + SQLite burn-in DB',
        f"- Burn-in DB: `{report['db_path']}`",
        '- Auth model: RBAC bearer token (`admin`, `analyst`, `auditor`)',
        '',
        '## Checklist Results',
        '',
        '| Check | Result | Evidence |',
        '|---|---|---|',
        f"| Auth enforcement on secure endpoints | {status(checks['auth_enforcement']['pass'])} | all unauthenticated endpoint probes returned `401` |",
        f"| Intelligence endpoint contracts | {status(checks['intelligence_contracts']['pass'])} | statuses: gap `{checks['intelligence_contracts']['gap_status']}`, hotspot `{checks['intelligence_contracts']['hotspot_status']}`, bq `{checks['intelligence_contracts']['bigquery_status']}`, vertex `{checks['intelligence_contracts']['vertex_status']}` |",
        f"| Policy endpoint contracts | {status(checks['policy_contracts']['pass'])} | rankings `{checks['policy_contracts']['priority_rankings_status']}`, impact `{checks['policy_contracts']['impact_brief_status']}` |",
        f"| Budget sensitivity behavior | {status(checks['budget_sensitivity']['pass'])} | funded count higher budget >= lower budget (`{checks['budget_sensitivity']['high_budget_funded_count']} >= {checks['budget_sensitivity']['low_budget_funded_count']}`) |",
        f"| Repeated-call stability | {status(checks['repeated_call_stability']['pass'])} | `{checks['repeated_call_stability']['iterations']}` iterations, failures `{checks['repeated_call_stability']['failed_iterations']}` |",
        f"| Audit trail generation | {status(checks['audit_trail']['pass'])} | gap `{checks['audit_trail']['gap_events']}`, hotspot `{checks['audit_trail']['hotspot_events']}`, bq `{checks['audit_trail']['bigquery_events']}`, vertex `{checks['audit_trail']['vertex_events']}`, policy rank `{checks['audit_trail']['priority_rankings_events']}`, policy brief `{checks['audit_trail']['impact_brief_events']}` |",
        '',
        '## Raw Burn-in Summary',
        f"- `all_passed`: `{str(summary['all_passed']).lower()}`",
        f"- `passed_checks`: `{summary['passed_checks']}`",
        f"- `total_checks`: `{summary['total_checks']}`",
        '',
        '## Go-Live Decision',
        '**READY (Phase 3 Intelligence + Policy Layer)**' if summary['all_passed'] else '**NOT READY (Phase 3 Intelligence + Policy Layer)**',
        '',
        'The Phase 3 surface is production-ready for controlled rollout based on current local burn-in validation.' if summary['all_passed'] else 'One or more checks failed. Review JSON output and fix before rollout.',
        '',
        '## Operational Notes',
        '- Track intelligence endpoint latency/error rates separately from policy endpoints.',
        '- Verify ongoing audit event continuity for both intelligence and policy views.',
        '- Uses timezone-aware UTC timestamps across burn-in flows.',
        '',
    ]
    return '\n'.join(lines)


def main():
    db_path = os.path.join(os.getcwd(), 'tests', 'burnin_phase3_policy.db')
    if os.path.exists(db_path):
        os.remove(db_path)

    app = create_app()
    app.config.update(
        TESTING=True,
        DATABASE_PATH=db_path,
        DATABASE_URL='',
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
        analyst_token = app.config['ANALYST_API_TOKEN']

        _seed_requests(client)

        # 1) Security / auth enforcement on all phase 3 secure routes
        unauth_gap = client.get('/api/v1/intelligence/gap-analysis')
        unauth_hotspot = client.get('/api/v1/intelligence/hotspot-predictions')
        unauth_bq = client.get('/api/v1/intelligence/bigquery-analytics')
        unauth_vertex = client.get('/api/v1/intelligence/vertex-predictions')
        unauth_rank = client.get('/api/v1/policy/priority-rankings')
        unauth_brief = client.get('/api/v1/policy/impact-brief')

        report['checks']['auth_enforcement'] = {
            'pass': all(
                resp.status_code == 401
                for resp in [unauth_gap, unauth_hotspot, unauth_bq, unauth_vertex, unauth_rank, unauth_brief]
            ),
            'gap_status': unauth_gap.status_code,
            'hotspot_status': unauth_hotspot.status_code,
            'bigquery_status': unauth_bq.status_code,
            'vertex_status': unauth_vertex.status_code,
            'priority_rankings_status': unauth_rank.status_code,
            'impact_brief_status': unauth_brief.status_code,
        }

        # 2) Intelligence contracts
        gap = client.get('/api/v1/intelligence/gap-analysis?limit=5', headers=_auth(analyst_token))
        hotspot = client.get('/api/v1/intelligence/hotspot-predictions?limit=5', headers=_auth(analyst_token))
        bq = client.get('/api/v1/intelligence/bigquery-analytics?limit=5', headers=_auth(analyst_token))
        vertex = client.get('/api/v1/intelligence/vertex-predictions?limit=5', headers=_auth(analyst_token))

        gap_p = gap.get_json() if gap.is_json else {}
        hotspot_p = hotspot.get_json() if hotspot.is_json else {}
        bq_p = bq.get_json() if bq.is_json else {}
        vertex_p = vertex.get_json() if vertex.is_json else {}

        intelligence_pass = (
            gap.status_code == 200
            and hotspot.status_code == 200
            and bq.status_code == 200
            and vertex.status_code == 200
            and isinstance(gap_p.get('items', []), list)
            and isinstance(hotspot_p.get('items', []), list)
            and all(key in bq_p for key in ['success', 'district_aggregates', 'category_aggregates', 'source_aggregates', 'daily_trend'])
            and all(key in vertex_p for key in ['success', 'mode', 'prediction_horizon', 'items'])
        )

        report['checks']['intelligence_contracts'] = {
            'pass': intelligence_pass,
            'gap_status': gap.status_code,
            'hotspot_status': hotspot.status_code,
            'bigquery_status': bq.status_code,
            'vertex_status': vertex.status_code,
            'gap_items': len(gap_p.get('items', [])) if isinstance(gap_p, dict) else 0,
            'hotspot_items': len(hotspot_p.get('items', [])) if isinstance(hotspot_p, dict) else 0,
            'vertex_items': len(vertex_p.get('items', [])) if isinstance(vertex_p, dict) else 0,
        }

        # 3) Policy contracts
        ranking = client.get(
            '/api/v1/policy/priority-rankings?limit=5&total_budget_lakh=1200',
            headers=_auth(analyst_token),
        )
        impact = client.get(
            '/api/v1/policy/impact-brief?limit=5&total_budget_lakh=1200',
            headers=_auth(admin_token),
        )

        ranking_p = ranking.get_json() if ranking.is_json else {}
        impact_p = impact.get_json() if impact.is_json else {}

        policy_pass = (
            ranking.status_code == 200
            and impact.status_code == 200
            and all(k in ranking_p for k in ['success', 'items', 'funded_count', 'budget_used_lakh'])
            and all(k in impact_p for k in ['success', 'impact_metrics', 'auto_brief', 'items'])
        )

        report['checks']['policy_contracts'] = {
            'pass': policy_pass,
            'priority_rankings_status': ranking.status_code,
            'impact_brief_status': impact.status_code,
            'priority_items': len(ranking_p.get('items', [])) if isinstance(ranking_p, dict) else 0,
            'impact_items': len(impact_p.get('items', [])) if isinstance(impact_p, dict) else 0,
        }

        # 4) Budget sensitivity check
        low = client.get(
            '/api/v1/policy/priority-rankings?limit=10&total_budget_lakh=300',
            headers=_auth(admin_token),
        ).get_json() or {}
        high = client.get(
            '/api/v1/policy/priority-rankings?limit=10&total_budget_lakh=5000',
            headers=_auth(admin_token),
        ).get_json() or {}

        low_funded = int(low.get('funded_count', 0) or 0)
        high_funded = int(high.get('funded_count', 0) or 0)

        report['checks']['budget_sensitivity'] = {
            'pass': high_funded >= low_funded,
            'low_budget_lakh': 300,
            'high_budget_lakh': 5000,
            'low_budget_funded_count': low_funded,
            'high_budget_funded_count': high_funded,
        }

        # 5) Repeated secure call stability across intelligence + policy
        total_calls = 120
        failures = 0
        for index in range(total_calls):
            budget = 1000 + (index % 7) * 100
            calls = [
                client.get('/api/v1/intelligence/gap-analysis?limit=5', headers=_auth(analyst_token)),
                client.get('/api/v1/intelligence/hotspot-predictions?limit=5', headers=_auth(analyst_token)),
                client.get('/api/v1/intelligence/bigquery-analytics?limit=5', headers=_auth(analyst_token)),
                client.get('/api/v1/intelligence/vertex-predictions?limit=5', headers=_auth(analyst_token)),
                client.get(f'/api/v1/policy/priority-rankings?limit=5&total_budget_lakh={budget}', headers=_auth(analyst_token)),
                client.get(f'/api/v1/policy/impact-brief?limit=5&total_budget_lakh={budget}', headers=_auth(analyst_token)),
            ]
            if any(resp.status_code != 200 for resp in calls):
                failures += 1

        report['checks']['repeated_call_stability'] = {
            'pass': failures == 0,
            'iterations': total_calls,
            'failed_iterations': failures,
        }

        # 6) Audit trail check for intelligence + policy endpoints
        db = get_db()
        row = db.execute(
            '''
            SELECT
              SUM(CASE WHEN action = 'intelligence_gap_analysis_viewed' THEN 1 ELSE 0 END) AS gap_events,
              SUM(CASE WHEN action = 'intelligence_hotspot_predictions_viewed' THEN 1 ELSE 0 END) AS hotspot_events,
              SUM(CASE WHEN action = 'intelligence_bigquery_analytics_viewed' THEN 1 ELSE 0 END) AS bigquery_events,
              SUM(CASE WHEN action = 'intelligence_vertex_predictions_viewed' THEN 1 ELSE 0 END) AS vertex_events,
              SUM(CASE WHEN action = 'policy_priority_rankings_viewed' THEN 1 ELSE 0 END) AS priority_rankings_events,
              SUM(CASE WHEN action = 'policy_impact_brief_viewed' THEN 1 ELSE 0 END) AS impact_brief_events
            FROM audit_logs
            '''
        ).fetchone()

        gap_events = int((row['gap_events'] or 0) if row else 0)
        hotspot_events = int((row['hotspot_events'] or 0) if row else 0)
        bigquery_events = int((row['bigquery_events'] or 0) if row else 0)
        vertex_events = int((row['vertex_events'] or 0) if row else 0)
        priority_events = int((row['priority_rankings_events'] or 0) if row else 0)
        brief_events = int((row['impact_brief_events'] or 0) if row else 0)

        report['checks']['audit_trail'] = {
            'pass': all(v > 0 for v in [gap_events, hotspot_events, bigquery_events, vertex_events, priority_events, brief_events]),
            'gap_events': gap_events,
            'hotspot_events': hotspot_events,
            'bigquery_events': bigquery_events,
            'vertex_events': vertex_events,
            'priority_rankings_events': priority_events,
            'impact_brief_events': brief_events,
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




