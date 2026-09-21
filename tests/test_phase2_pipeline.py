import os
import time
import unittest

from visbharat import create_app
from visbharat.db import init_db, seed_default_users
from visbharat.services.pipeline_queue import run_pipeline_once


class TestPhase2Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_phase2_pipeline.db')
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
            ASYNC_PIPELINE_ENABLED=True,
            WEBHOOK_SHARED_TOKEN='phase2-token',
            WEBHOOK_REQUIRE_REPLAY_PROTECTION=True,
            WEBHOOK_REPLAY_STORE='db',
            WEBHOOK_ALLOWED_IPS='',
            TWILIO_AUTH_TOKEN='',
            TELEGRAM_WEBHOOK_SECRET='',
            META_APP_SECRET='',
        )

        with cls.app.app_context():
            init_db()
            seed_default_users()

        cls.client = cls.app.test_client()
        cls.admin_token = cls.app.config['ADMIN_API_TOKEN']
        cls.analyst_token = cls.app.config['ANALYST_API_TOKEN']

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

    def test_async_enqueue_and_worker_process(self):
        ts = int(time.time())
        headers = {
            'X-Webhook-Token': 'phase2-token',
            'X-Webhook-Timestamp': str(ts),
            'X-Webhook-Nonce': 'phase2-job-1',
        }

        enqueue_resp = self.client.post(
            '/api/channels/sms/webhook',
            headers=headers,
            data={
                'body': 'Drainage blockage near bus stand',
                'district': 'Chennai',
                'language': 'en',
                'from': '+911234567890',
            },
        )
        self.assertEqual(enqueue_resp.status_code, 200)
        payload = enqueue_resp.get_json()
        self.assertEqual(payload['status'], 'queued')
        self.assertIn('job_id', payload)
        job_id = payload['job_id']

        job_status_before = self.client.get(
            f'/api/v1/pipeline/jobs/{job_id}',
            headers=self._auth(self.analyst_token),
        )
        self.assertEqual(job_status_before.status_code, 200)

        with self.app.app_context():
            result = run_pipeline_once()
            self.assertIsNotNone(result)
            self.assertEqual(result['job_id'], job_id)
            self.assertEqual(result['status'], 'succeeded')

        job_status_after = self.client.get(
            f'/api/v1/pipeline/jobs/{job_id}',
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(job_status_after.status_code, 200)
        after_payload = job_status_after.get_json()['job']
        self.assertEqual(after_payload['status'], 'succeeded')
        self.assertIsNotNone(after_payload.get('result'))

        list_resp = self.client.get('/api/v1/pipeline/jobs', headers=self._auth(self.admin_token))
        self.assertEqual(list_resp.status_code, 200)
        self.assertTrue(any(j['job_id'] == job_id for j in list_resp.get_json()['jobs']))



    def test_idempotent_enqueue_reuses_job(self):
        ts = int(time.time())
        headers = {
            'X-Webhook-Token': 'phase2-token',
            'X-Webhook-Timestamp': str(ts),
            'X-Webhook-Nonce': 'phase2-idem-1',
            'X-Idempotency-Key': 'idem-key-001',
        }
        payload = {
            'body': 'Same issue payload',
            'district': 'Chennai',
            'language': 'en',
            'from': '+911111111111',
        }

        r1 = self.client.post('/api/channels/sms/webhook', headers=headers, data=payload)
        self.assertEqual(r1.status_code, 200)
        j1 = r1.get_json()['job_id']

        headers2 = dict(headers)
        headers2['X-Webhook-Nonce'] = 'phase2-idem-2'
        headers2['X-Webhook-Timestamp'] = str(ts + 1)
        r2 = self.client.post('/api/channels/sms/webhook', headers=headers2, data=payload)
        self.assertEqual(r2.status_code, 200)
        body2 = r2.get_json()
        self.assertEqual(body2['job_id'], j1)
        self.assertTrue(body2.get('idempotent_reused'))

    def test_pipeline_metrics_and_admin_controls(self):
        jobs_resp = self.client.get('/api/v1/pipeline/jobs', headers=self._auth(self.admin_token))
        self.assertEqual(jobs_resp.status_code, 200)
        jobs = jobs_resp.get_json()['jobs']
        self.assertTrue(len(jobs) >= 1)

        metrics_resp = self.client.get('/api/v1/pipeline/metrics', headers=self._auth(self.admin_token))
        self.assertEqual(metrics_resp.status_code, 200)
        metrics = metrics_resp.get_json()['metrics']
        self.assertIn('total_jobs', metrics)
        self.assertIn('by_status', metrics)

        # create a queued job then cancel it
        ts = int(time.time())
        q = self.client.post(
            '/api/channels/sms/webhook',
            headers={
                'X-Webhook-Token': 'phase2-token',
                'X-Webhook-Timestamp': str(ts),
                'X-Webhook-Nonce': 'phase2-cancel-1',
            },
            data={'body': 'cancel me', 'district': 'Chennai', 'language': 'en', 'from': '+911222222222'},
        )
        self.assertEqual(q.status_code, 200)
        job_id = q.get_json()['job_id']

        cancel_resp = self.client.post(f'/api/v1/pipeline/jobs/{job_id}/cancel', headers=self._auth(self.admin_token))
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertEqual(cancel_resp.get_json()['job']['status'], 'canceled')

        retry_resp = self.client.post(f'/api/v1/pipeline/jobs/{job_id}/retry', headers=self._auth(self.admin_token))
        self.assertEqual(retry_resp.status_code, 400)

if __name__ == '__main__':
    unittest.main()

