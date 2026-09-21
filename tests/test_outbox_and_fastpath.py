import unittest
import json
import secrets
from visbharat import create_app
from visbharat.db import get_db, init_db
from visbharat.services.pipeline_queue import (
    fast_path_emergency_triage,
    process_ingestion_payload,
    get_outbox_replication_metrics,
    replicate_pending_outbox,
)
from visbharat.config import Config


class TestOutboxAndFastPath(unittest.TestCase):
    def setUp(self):
        self.app = create_app({'TESTING': True})
        self.app_context = self.app.app_context()
        self.app_context.push()
        init_db()

    def tearDown(self):
        self.app_context.pop()

    def test_fast_path_emergency_english_live_wire(self):
        result = fast_path_emergency_triage("Live wire hanging on the street near school gate!")
        self.assertIsNotNone(result)
        self.assertEqual(result['urgency'], 'Emergency')
        self.assertEqual(result['category'], 'Electricity')
        self.assertTrue(result['fast_path_triggered'])
        self.assertLess(result['triage_latency_ms'], 10.0)

    def test_fast_path_emergency_tamil_wire(self):
        result = fast_path_emergency_triage("மின்சாரக் கம்பி அறுந்து விழுந்துவிட்டது, உடனே வாருங்கள்")
        self.assertIsNotNone(result)
        self.assertEqual(result['urgency'], 'Emergency')
        self.assertEqual(result['category'], 'Electricity')
        self.assertTrue(result['fast_path_triggered'])

    def test_fast_path_emergency_telugu_gas_leak(self):
        result = fast_path_emergency_triage("గ్యాస్ లీకేజీ జరుగుతోంది, తీవ్ర ప్రమాదం")
        self.assertIsNotNone(result)
        self.assertEqual(result['urgency'], 'Emergency')
        self.assertEqual(result['category'], 'Sanitation')
        self.assertTrue(result['fast_path_triggered'])

    def test_fast_path_routine_returns_none(self):
        result = fast_path_emergency_triage("Please clean the street drain when possible, normal dust.")
        self.assertIsNone(result)

    def test_ingestion_populates_outbox_replication_columns(self):
        payload = {
            'text': 'Severe road ditch near bustand',
            'language': 'en',
            'district': 'Vellore',
            'channel': 'Web Form',
        }
        res = process_ingestion_payload(payload)
        req_id = res['request_id']

        db = get_db()
        row = db.execute(
            'SELECT replicated_bigquery, replicated_pubsub, replication_error FROM citizen_requests WHERE request_id = ?',
            (req_id,)
        ).fetchone()

        self.assertIsNotNone(row)
        # In testing environment without live GCP credentials, outbox should record unconfigured status without throwing exception
        self.assertIn('client unconfigured', row['replication_error'])

        metrics = get_outbox_replication_metrics()
        self.assertIn('total_requests', metrics)
        self.assertIn('pending_bigquery', metrics)

    def test_tamil_fast_path_intake_without_waiting_for_translation(self):
        payload = {
            'text': 'மின்சாரக் கம்பி அறுந்து விழுந்துவிட்டது, உடனடியாக ஆபத்து',
            'language': 'ta',
            'district': 'Chennai',
            'channel': 'WhatsApp',
        }
        res = process_ingestion_payload(payload)
        self.assertEqual(res['classification']['urgency'], 'Emergency')
        self.assertEqual(res['classification']['category'], 'Electricity')
        self.assertTrue(res['classification']['fast_path_triggered'])
        # Fast path emergency must have a 2-hour SLA
        from datetime import datetime, timezone
        due = datetime.fromisoformat(res['sla']['due_at'].replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        diff_hours = (due - now).total_seconds() / 3600.0
        self.assertLessEqual(diff_hours, 2.05)
        self.assertGreaterEqual(diff_hours, 1.90)

    def test_2hr_emergency_sla_enforcement(self):
        from visbharat.services.sla import compute_sla_due_at, resolve_sla_policy
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        due_str = compute_sla_due_at('Emergency', now=now)
        due = datetime.fromisoformat(due_str.replace('Z', '+00:00'))
        self.assertAlmostEqual((due - now).total_seconds() / 3600.0, 2.0, delta=0.05)

        policy = resolve_sla_policy(
            urgency='Emergency',
            category='Electricity',
            channel='Web Form',
            routed_department='Electricity Board',
            now=now,
        )
        policy_due = datetime.fromisoformat(policy['due_at'].replace('Z', '+00:00'))
        self.assertAlmostEqual((policy_due - now).total_seconds() / 3600.0, 2.0, delta=0.05)

    def test_data_residency_forbids_foreign_regions_in_pilot_mode(self):
        import os
        from visbharat.services.google_ai import GoogleAIClient
        old_prof = os.environ.get('DEPLOYMENT_PROFILE')
        try:
            os.environ['DEPLOYMENT_PROFILE'] = 'pilot'
            os.environ['VERTEX_OPENAI_LOCATION'] = 'us-central1'
            with self.assertRaises(RuntimeError) as ctx:
                GoogleAIClient()
            self.assertIn("Data Residency Policy Violation", str(ctx.exception))
        finally:
            if old_prof is not None:
                os.environ['DEPLOYMENT_PROFILE'] = old_prof
            else:
                os.environ.pop('DEPLOYMENT_PROFILE', None)
            os.environ['VERTEX_OPENAI_LOCATION'] = 'asia-south1'

    def test_transactional_outbox_exponential_backoff_and_dlq(self):
        # Isolate test by marking existing seed rows as replicated
        db = get_db()
        db.execute('UPDATE citizen_requests SET replicated_bigquery = 1, replicated_pubsub = 1')
        db.commit()

        req_id = f"NVB-TEST-OUTBOX-{secrets.token_hex(4)}"
        db.execute(
            '''
            INSERT INTO citizen_requests (
                request_id, source_channel, input_language, district, state,
                lat, lng, original_text, translated_text, category, urgency,
                sentiment, status, submitted_by, ai_metadata_json, created_at,
                replicated_bigquery, replicated_pubsub, replication_attempt_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)
            ''',
            (req_id, 'Web Form', 'en', 'Vellore', 'Tamil Nadu', 12.9, 79.1,
             'Test complaint', 'Test complaint', 'Road', 'Routine', 'Neutral', 'New',
             'Tester', '{}', '2026-09-21T00:00:00Z')
        )
        db.commit()

        # Run outbox replication (without live GCP clients, attempt 1 should record backoff)
        rep1 = replicate_pending_outbox(limit=10, worker_id='test-worker-1', max_retries=3)
        self.assertGreaterEqual(rep1['inspected'], 1)

        row = db.execute(
            'SELECT replication_attempt_count, next_replication_attempt_at, replication_error FROM citizen_requests WHERE request_id = ?',
            (req_id,)
        ).fetchone()
        self.assertEqual(row['replication_attempt_count'], 1)
        self.assertIsNotNone(row['next_replication_attempt_at'])

        # Fast forward attempt count to 2 and expire backoff, so worker attempts attempt 3 and triggers DLQ
        db.execute('UPDATE citizen_requests SET replication_attempt_count = 2, next_replication_attempt_at = NULL WHERE request_id = ?', (req_id,))
        db.commit()

        rep3 = replicate_pending_outbox(limit=10, worker_id='test-worker-2', max_retries=3)
        self.assertGreaterEqual(rep3['dlq_exhausted'], 1)

        # Verify entry in cloud_ingestion_dead_letters
        dlq_row = db.execute(
            'SELECT status, error_message FROM cloud_ingestion_dead_letters WHERE request_id = ?',
            (req_id,)
        ).fetchone()
        self.assertIsNotNone(dlq_row)
        self.assertEqual(dlq_row['status'], 'dlq_exhausted')

        metrics = get_outbox_replication_metrics()
        self.assertGreaterEqual(metrics['dlq_exhausted_count'], 1)
        self.assertTrue(metrics['slo_breached'])
        self.assertIsNotNone(metrics['slo_alert'])


if __name__ == '__main__':
    unittest.main()
