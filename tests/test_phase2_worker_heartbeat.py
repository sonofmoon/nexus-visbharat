import os
import unittest

from visbharat import create_app
from visbharat.db import init_db, seed_default_users
from visbharat.services.pipeline_queue import upsert_worker_heartbeat


class TestPhase2WorkerHeartbeat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_phase2_worker_heartbeat.db')
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
        )

        with cls.app.app_context():
            init_db()
            seed_default_users()
            upsert_worker_heartbeat('worker-test-1', 'running', pid=12345, metadata={'region': 'local'})

        cls.client = cls.app.test_client()
        cls.admin_token = cls.app.config['ADMIN_API_TOKEN']
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

    def test_workers_endpoint_admin_auditor_access(self):
        admin_resp = self.client.get('/api/v1/pipeline/workers', headers=self._auth(self.admin_token))
        self.assertEqual(admin_resp.status_code, 200)
        workers = admin_resp.get_json()['workers']
        self.assertTrue(any(w['worker_id'] == 'worker-test-1' for w in workers))

        auditor_resp = self.client.get('/api/v1/pipeline/workers', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
