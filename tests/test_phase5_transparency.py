import os
import unittest

from visbharat import create_app
from visbharat.db import init_db, seed_default_users


class TestPhase5Transparency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_phase5_transparency.db')
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except OSError:
                pass

        cls.app = create_app()
        cls.app.config.update(
            TESTING=True,
            DATABASE_PATH=cls.db_path,
            DATABASE_URL='',
            PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE=3,
            PUBLIC_TRANSPARENCY_MAX_LIMIT=200,
        )

        with cls.app.app_context():
            init_db()
            seed_default_users()

        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except OSError:
                pass

    def _seed_requests(self):
        # Chennai -> 3 records (published), Koraput -> 2 records (suppressed)
        payloads = [
            ('Chennai', 'Road potholes near bus stand'),
            ('Chennai', 'Water leakage at ward 12'),
            ('Chennai', 'Frequent power cuts in block A'),
            ('Koraput', 'Road access issue in village'),
            ('Koraput', 'No tap water in hamlet'),
        ]
        for district, text in payloads:
            self.client.post(
                '/api/submit',
                json={
                    'text': text,
                    'language': 'en',
                    'district': district,
                    'source': 'Web',
                },
            )

    def test_transparency_endpoints_are_public(self):
        self._seed_requests()

        summary_resp = self.client.get('/api/public/transparency/summary')
        districts_resp = self.client.get('/api/public/transparency/districts')
        categories_resp = self.client.get('/api/public/transparency/categories')

        self.assertEqual(summary_resp.status_code, 200)
        self.assertEqual(districts_resp.status_code, 200)
        self.assertEqual(categories_resp.status_code, 200)

    def test_district_groups_are_anonymized_by_min_group_size(self):
        self._seed_requests()
        response = self.client.get('/api/public/transparency/districts?limit=50')
        payload = response.get_json()

        self.assertTrue(payload['success'])
        items = payload['items']
        districts = [item['district'] for item in items]

        self.assertIn('Chennai', districts)
        self.assertNotIn('Koraput', districts)
        self.assertEqual(payload['meta']['min_group_size'], 3)
        self.assertGreaterEqual(payload['meta']['suppressed_groups'], 1)

    def test_summary_contract_and_redaction(self):
        self._seed_requests()
        response = self.client.get('/api/public/transparency/summary')
        payload = response.get_json()

        self.assertTrue(payload['success'])
        summary = payload['summary']
        self.assertIn('total_requests', summary)
        self.assertIn('published_request_count', summary)
        self.assertIn('anonymization', summary)
        self.assertNotIn('original_text', str(payload))

    def test_districts_reject_invalid_limit(self):
        response = self.client.get('/api/public/transparency/districts?limit=abc')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['error'], 'limit must be an integer')


if __name__ == '__main__':
    unittest.main()
