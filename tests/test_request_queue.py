import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from visbharat.blueprints.api import api_bp
from visbharat.db import close_db, get_db, init_db
from visbharat.security import hash_api_token


class TestRequestQueue(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DATABASE_URL='', DATABASE_PATH=str(Path(self.temp.name) / 'queue.db'))
        self.app.teardown_appcontext(close_db)
        self.app.register_blueprint(api_bp)
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer queue-test-token'}
        with self.app.app_context():
            init_db()
            db = get_db()
            db.execute('INSERT INTO users (name, api_token, api_token_hash, role, created_at) VALUES (?, ?, ?, ?, ?)',
                       ('Test officer', 'queue-test-token', hash_api_token('queue-test-token'), 'admin', '2026-01-01'))
            self.insert('NVB-OLD-TARGET', district='Karur', category='Water Supply', urgency='Emergency',
                        routed_department='Water Board', sla_due_at='2000-01-01')
            self.insert('NVB-CLOSED', status='Closed', sla_due_at='2000-01-01')
            self.insert('NVB-RESOLVED', status='Resolved', sla_due_at='2000-01-01')
            self.insert('NVB-FUTURE', sla_due_at='2099-01-01')
            self.insert('NVB-LITERAL%_!', sla_due_at='')
            for index in range(60):
                self.insert(f'NVB-NEW-{index:03}', district='Chennai', status='In Progress')
            db.commit()

    @staticmethod
    def insert(ticket, **changes):
        values = dict(request_id=ticket, source_channel='web', input_language='en', district='Madurai',
                      state='Tamil Nadu', lat=10, lng=78, original_text='Test request', translated_text='Test request',
                      category='Road', urgency='Routine', sentiment='Neutral', status='Pending',
                      routed_department='Public Works', sla_due_at=None, ai_metadata_json='{}', created_at='2026-01-01')
        values.update(changes)
        get_db().execute(f"INSERT INTO citizen_requests ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})", list(values.values()))

    def query(self, **params):
        response = self.client.get('/api/v1/requests', query_string=params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_search_and_combined_filters_find_ticket_beyond_first_page(self):
        first = self.query(limit=50)
        self.assertNotIn('NVB-OLD-TARGET', [row['request_id'] for row in first['requests']])
        result = self.query(ticket='old-target', state='Tamil Nadu', district='Karur', category='Water Supply',
                            routed_department='Water Board', urgency='Emergency', status='active', limit=50)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['requests'][0]['request_id'], 'NVB-OLD-TARGET')
        self.assertEqual(self.query(ticket='old-target', category='Road')['total'], 0)

    def test_active_completed_and_pagination(self):
        first = self.query(status='active', limit=50)
        second = self.query(status='active', limit=50, offset=50)
        self.assertEqual(first['total'], 63)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        ids = [row['request_id'] for row in first['requests'] + second['requests']]
        self.assertEqual(len(set(ids)), 63)
        self.assertNotIn('NVB-CLOSED', ids)
        self.assertNotIn('NVB-RESOLVED', ids)
        self.assertEqual(self.query(status='Resolved')['requests'][0]['request_id'], 'NVB-RESOLVED')
        self.assertEqual(self.query()['total'], 65)  # Existing unfiltered API behavior remains available.

    def test_overdue_uses_deadline_and_excludes_finished_or_undated(self):
        with self.app.app_context():
            self.insert('NVB-OFFSET-PAST', sla_due_at='2026-09-19T06:00:00+05:30')
            self.insert('NVB-OFFSET-FUTURE', sla_due_at='2026-09-18T23:00:00-05:00')
            self.insert('NVB-INVALID-DATE', sla_due_at='not-a-date')
            get_db().commit()
        with patch('visbharat.services.request_queue.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 9, 19, 1, tzinfo=timezone.utc)
            result = self.query(overdue='true')
        self.assertEqual({row['request_id'] for row in result['requests']}, {'NVB-OLD-TARGET', 'NVB-OFFSET-PAST'})
        self.assertTrue(all(row['sla_overdue'] for row in result['requests']))
        self.assertEqual(self.query(overdue='true', status='Closed')['total'], 0)

    def test_search_escapes_wildcards_and_injection(self):
        self.assertEqual(self.query(ticket='%_!')['total'], 1)
        self.assertEqual(self.query(ticket="' OR 1=1 --")['total'], 0)
        self.assertEqual(self.query(state="Tamil Nadu' OR 1=1 --")['total'], 0)

    def test_options_come_from_all_requests_and_survive_empty_results(self):
        result = self.query(ticket='no-match', include_options='1')
        self.assertEqual(result['total'], 0)
        self.assertIn('Karur', result['filter_options']['geography']['Tamil Nadu'])
        self.assertIn('Water Board', result['filter_options']['routed_department'])
        self.assertIn('Emergency', result['filter_options']['urgency'])

    def test_page_clamps_after_stage_change(self):
        first = self.query(status='active', limit=62, offset=62)
        self.assertEqual(len(first['requests']), 1)
        with self.app.app_context():
            get_db().execute('UPDATE citizen_requests SET status = ? WHERE request_id = ?', ('Resolved', first['requests'][0]['request_id']))
            get_db().commit()
        refreshed = self.query(status='active', limit=62, offset=62)
        self.assertEqual(refreshed['offset'], 0)
        self.assertEqual(refreshed['total'], 62)

    def test_invalid_pagination_and_filter_values(self):
        for params in ({'limit': 'bad'}, {'limit': 0}, {'limit': -1}, {'offset': -1}, {'offset': 'bad'}, {'overdue': 'maybe'}):
            with self.subTest(params=params):
                response = self.client.get('/api/v1/requests', query_string=params, headers=self.headers)
                self.assertEqual(response.status_code, 400)

    def test_list_requires_valid_authentication(self):
        self.assertEqual(self.client.get('/api/v1/requests').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/requests', headers={'Authorization': 'Bearer invalid'}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
