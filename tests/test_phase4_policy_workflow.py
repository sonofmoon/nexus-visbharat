import os
import unittest

from visbharat import create_app
from visbharat.db import init_db, seed_default_users


class TestPhase4PolicyWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_phase4_policy_workflow.db')
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except OSError:
                pass

        cls.app = create_app()
        cls.app.config.update(TESTING=True, DATABASE_PATH=cls.db_path, DATABASE_URL='')

        with cls.app.app_context():
            init_db()
            seed_default_users()

        cls.client = cls.app.test_client()
        cls.admin_token = cls.app.config['ADMIN_API_TOKEN']
        cls.analyst_token = cls.app.config['ANALYST_API_TOKEN']
        cls.auditor_token = cls.app.config['AUDITOR_API_TOKEN']

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except OSError:
                pass

    @staticmethod
    def _auth(token):
        return {'Authorization': f'Bearer {token}'}

    def _seed_some_requests(self):
        for district, text in [
            ('Chennai', 'Drainage overflow in zone 5'),
            ('Chennai', 'Water supply outage in ward 8'),
            ('Koraput', 'Road access is blocked for village'),
            ('Hyderabad', 'Transformer issue causing outage'),
        ]:
            self.client.post(
                '/api/submit',
                json={
                    'text': text,
                    'language': 'en',
                    'district': district,
                    'source': 'Web',
                },
            )

    def test_draft_creation_requires_auth(self):
        resp = self.client.post('/api/v1/policy/decisions/draft')
        self.assertEqual(resp.status_code, 401)

    def test_draft_creation_and_list_flow(self):
        self._seed_some_requests()

        create_resp = self.client.post(
            '/api/v1/policy/decisions/draft?limit=3&total_budget_lakh=1000',
            headers=self._auth(self.analyst_token),
        )
        self.assertEqual(create_resp.status_code, 200)
        create_payload = create_resp.get_json()
        self.assertTrue(create_payload['success'])
        self.assertGreater(create_payload['created_count'], 0)

        list_resp = self.client.get(
            '/api/v1/policy/decisions?status=draft',
            headers=self._auth(self.auditor_token),
        )
        self.assertEqual(list_resp.status_code, 200)
        list_payload = list_resp.get_json()
        self.assertTrue(list_payload['success'])
        self.assertGreater(len(list_payload['items']), 0)
        self.assertEqual(list_payload['items'][0]['status'], 'draft')

    def test_approve_and_reject_lifecycle(self):
        self._seed_some_requests()
        create_resp = self.client.post(
            '/api/v1/policy/decisions/draft?limit=2&total_budget_lakh=900',
            headers=self._auth(self.analyst_token),
        )
        decisions = create_resp.get_json()['decisions']
        self.assertGreaterEqual(len(decisions), 2)

        approve_id = decisions[0]['decision_id']
        reject_id = decisions[1]['decision_id']

        approve_resp = self.client.post(
            f'/api/v1/policy/decisions/{approve_id}/approve',
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(approve_resp.status_code, 200)
        self.assertEqual(approve_resp.get_json()['decision']['status'], 'approved')

        reject_resp = self.client.post(
            f'/api/v1/policy/decisions/{reject_id}/reject',
            headers=self._auth(self.admin_token),
            json={'notes': 'Deferred due to execution capacity this quarter'},
        )
        self.assertEqual(reject_resp.status_code, 200)
        rejected = reject_resp.get_json()['decision']
        self.assertEqual(rejected['status'], 'rejected')
        self.assertIn('execution capacity', rejected['notes'])

    def test_non_admin_cannot_approve_or_reject(self):
        self._seed_some_requests()
        create_resp = self.client.post(
            '/api/v1/policy/decisions/draft?limit=1',
            headers=self._auth(self.analyst_token),
        )
        decision_id = create_resp.get_json()['decisions'][0]['decision_id']

        approve_resp = self.client.post(
            f'/api/v1/policy/decisions/{decision_id}/approve',
            headers=self._auth(self.analyst_token),
        )
        self.assertEqual(approve_resp.status_code, 403)

        reject_resp = self.client.post(
            f'/api/v1/policy/decisions/{decision_id}/reject',
            headers=self._auth(self.auditor_token),
            json={'notes': 'Not allowed'},
        )
        self.assertEqual(reject_resp.status_code, 403)


if __name__ == '__main__':
    unittest.main()
