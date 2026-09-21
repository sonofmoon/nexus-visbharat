import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask
from scripts.audit_demo_demands import summarize
from scripts.build_jury_demo import build, validate, iso
from visbharat.blueprints.api import api_bp
from visbharat.blueprints.web import web_bp
from visbharat.config import Config
from visbharat.db import close_db, get_db, init_db, seed_citizen_requests_from_csv
from visbharat.security import hash_api_token

ROOT = Path(__file__).resolve().parents[1]


class TestJuryDemo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.as_of = datetime(2026, 9, 19, 2, 42, 55, tzinfo=timezone.utc)
        cls.rows, cls.events, cls.cases = build(cls.as_of)
        cls.showcase = {'dataset_version': 'jury-demo-v2', 'as_of': iso(cls.as_of), 'summary': summarize(cls.rows),
                        'cases': cls.cases, 'impact_example': {'before': 32, 'after': 4, 'delivery_date': '2026-08-20'}}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__, root_path=str(Path(self.temp.name) / 'visbharat'),
                         template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
        self.app.config.from_object(Config)
        self.app.config.update(TESTING=True, DATABASE_URL='', DATABASE_PATH=str(Path(self.temp.name) / 'test.db'),
                               GOOGLE_MAPS_API_KEY='test-map-key', ADMIN_API_TOKEN='demo-test-token')
        self.app.teardown_appcontext(close_db)
        self.app.register_blueprint(api_bp)
        self.app.register_blueprint(web_bp)
        self.app.extensions['reference_repo'] = SimpleNamespace(list_states=lambda: list(self.showcase['summary']['state']))
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer demo-test-token'}
        fields = list(self.rows[0])
        with self.app.app_context():
            init_db()
            db = get_db()
            db.execute('INSERT INTO users (name, api_token, api_token_hash, role, created_at) VALUES (?, ?, ?, ?, ?)',
                       ('Test officer', 'demo-test-token', hash_api_token('demo-test-token'), 'admin', iso(self.as_of)))
            for case in self.cases:
                db.execute(f"INSERT INTO citizen_requests ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})", [case[field] for field in fields])
            sample_ids = {case['request_id'] for case in self.cases}
            event_fields = list(self.events[0])
            db._conn.executemany(f"INSERT INTO request_lifecycle_events ({', '.join(event_fields)}) VALUES ({', '.join('?' for _ in event_fields)})",
                                [[event[field] for field in event_fields] for event in self.events if event['request_id'] in sample_ids])
            db.commit()

    def test_whole_dataset_invariants(self):
        validate(self.rows, self.events, self.as_of)
        self.assertEqual(self.showcase['summary']['native_language_script_mismatches'], 0)
        self.assertGreater(len({row['original_text'] for row in self.rows}), 12000)
        self.assertTrue(all(json.loads(row['ai_metadata_json'])['is_synthetic'] for row in self.rows))

    def test_impact_windows_and_geographic_cohorts(self):
        cohort = [row for row in self.rows if json.loads(row['ai_metadata_json'])['case_key'] == 'nalgonda_water']
        delivery = datetime(2026, 8, 20, 2, 42, 55, tzinfo=timezone.utc)
        self.assertEqual(sum(datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')) < delivery for row in cohort), 32)
        self.assertEqual(len(cohort), 36)
        self.assertEqual({(row['district'], row['ward'], row['category']) for row in cohort}, {('Nalgonda', 'Ward 07', 'Water Supply')})

    def test_first_response_and_emergency_completion_are_timely(self):
        rows = {row['request_id']: row for row in self.rows}
        for event in self.events:
            row = rows[event['request_id']]
            elapsed = (datetime.fromisoformat(event['created_at'].replace('Z', '+00:00')) - datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))).total_seconds() / 3600
            if event['event_type'] == 'acknowledged':
                self.assertLessEqual(elapsed, {'Routine': 72, 'Urgent': 24, 'Emergency': 4}[row['urgency']])
            if row['urgency'] == 'Emergency' and event['event_type'] == 'resolved':
                self.assertLessEqual(elapsed, 6)

    def test_all_showcase_tickets_have_matching_trackable_lifecycles(self):
        for case in self.cases:
            with self.subTest(case=case['key']):
                response = self.client.get(f"/api/v1/requests/{case['request_id']}/track")
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload['status'], case['status'])
                self.assertEqual(payload['timeline_events'][-1]['to_status'], case['status'])
                self.assertEqual(payload['routed_department'], case['routed_department'])

    def test_walkthrough_and_execution_deep_link(self):
        with patch('visbharat.blueprints.web._demo_showcase', return_value=self.showcase):
            response = self.client.get('/demo')
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'fictional', response.data)
            for case in self.cases:
                self.assertIn(case['request_id'].encode(), response.data)
            ticket = self.cases[0]['request_id']
            dashboard = self.client.get('/dashboard', query_string={'demo_ticket': ticket})
            self.assertEqual(dashboard.status_code, 200)
            self.assertIn(b'window.NVB_DEMO_TICKET', dashboard.data)

    def test_csv_seed_preserves_metadata_and_does_not_overwrite_progress(self):
        sample = self.rows[0]
        folder = Path(self.temp.name) / 'static' / 'data'
        folder.mkdir(parents=True)
        with (folder / 'complaints.csv').open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(sample))
            writer.writeheader()
            writer.writerow(sample)
        with self.app.app_context():
            seed_citizen_requests_from_csv()
            db = get_db()
            row = db.execute('SELECT * FROM citizen_requests WHERE request_id=?', (sample['request_id'],)).fetchone()
            self.assertEqual(row['routed_department'], sample['routed_department'])
            self.assertEqual(row['created_at'], sample['created_at'])
            self.assertTrue(json.loads(row['ai_metadata_json'])['is_synthetic'])
            db.execute("UPDATE citizen_requests SET status='Closed' WHERE request_id=?", (sample['request_id'],))
            db.commit()
            seed_citizen_requests_from_csv()
            self.assertEqual(db.execute('SELECT status FROM citizen_requests WHERE request_id=?', (sample['request_id'],)).fetchone()['status'], 'Closed')
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM citizen_requests WHERE request_id=?', (sample['request_id'],)).fetchone()['n'], 1)

    def test_officer_update_syncs_bigquery_and_reports_sync_failure(self):
        ticket = next(case['request_id'] for case in self.cases if case['status'] == 'Pending')
        cloud = Mock()
        cloud.update_request_progress.return_value = True
        self.app.extensions['google_bigquery_client'] = cloud
        with patch('visbharat.blueprints.api._apply_sla_policy_if_needed'):
            response = self.client.post(f'/api/v1/requests/{ticket}/lifecycle/update', headers=self.headers,
                                        json={'action': 'acknowledged', 'reason': 'Synthetic test review'})
            self.assertEqual(response.get_json()['analytics_sync'], 'synced')
            self.assertEqual(cloud.update_request_progress.call_args.args[0]['status'], 'Acknowledged')
            cloud.update_request_progress.side_effect = RuntimeError('temporary analytics failure')
            response = self.client.post(f'/api/v1/requests/{ticket}/lifecycle/update', headers=self.headers,
                                        json={'action': 'prioritized', 'reason': 'Synthetic test planning'})
            self.assertTrue(response.get_json()['success'])
            self.assertEqual(response.get_json()['analytics_sync'], 'failed')
            self.assertEqual(response.get_json()['request']['status'], 'Prioritized')


if __name__ == '__main__':
    unittest.main()
