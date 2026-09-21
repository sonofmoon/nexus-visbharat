import unittest
import json
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

from visbharat import create_app
from visbharat.db import get_db, init_db
from visbharat.services.pipeline_queue import fast_path_emergency_triage, process_ingestion_payload


class TestEmergencyDispatchAndTelemetry(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            'TESTING': True,
            'ADMIN_API_TOKEN': 'visbharat-admin-token',
            'ANALYST_API_TOKEN': 'visbharat-analyst-token',
        })
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()
        init_db()

    def tearDown(self):
        self.app_context.pop()

    def test_dynamic_triage_latency_telemetry_is_measured(self):
        result = fast_path_emergency_triage("Live wire hanging on the street near school gate!")
        self.assertIsNotNone(result)
        self.assertTrue(result['fast_path_triggered'])
        latency = result.get('triage_latency_ms')
        self.assertIsInstance(latency, float)
        self.assertGreater(latency, 0.0)
        self.assertLess(latency, 5.0)  # Must be sub-5ms

    def test_emergency_intake_populates_dispatch_lifecycle(self):
        payload = {
            'text': 'மின்சாரக் கம்பி அறுந்து விழுந்துவிட்டது, உடனே வாருங்கள்',
            'language': 'ta',
            'district': 'Chennai',
            'channel': 'WhatsApp',
        }
        res = process_ingestion_payload(payload)
        req_id = res['request_id']

        db = get_db()
        row = db.execute(
            '''
            SELECT emergency_dispatch_status, dispatched_to, dispatched_at, acknowledgement_token, fallback_target
            FROM citizen_requests
            WHERE request_id = ?
            ''',
            (req_id,)
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row['emergency_dispatch_status'], 'dispatched')
        self.assertIn('Disaster Management', row['dispatched_to'])
        self.assertIsNotNone(row['dispatched_at'])
        self.assertIsNotNone(row['acknowledgement_token'])
        self.assertIn('Collectorate', row['fallback_target'])

    def test_emergency_acknowledge_endpoint_validates_token(self):
        # 1. Create emergency request
        payload = {
            'text': 'Severe gas leak cylinder explosion risk in hospital kitchen',
            'language': 'en',
            'district': 'Vellore',
            'channel': 'Web Form',
        }
        res = process_ingestion_payload(payload)
        req_id = res['request_id']

        db = get_db()
        # The database retains a digest only; the raw capability remains in
        # the trusted ingestion service result for handoff to a response desk.
        row = db.execute('SELECT acknowledgement_token FROM citizen_requests WHERE request_id = ?', (req_id,)).fetchone()
        valid_token = res['emergency_acknowledgement_token']
        self.assertNotEqual(row['acknowledgement_token'], valid_token)

        # 2. Attempt unauthorized acknowledgement
        resp_bad = self.client.post('/api/emergency/acknowledge', json={
            'request_id': req_id,
            'acknowledgement_token': 'wrong-token-abc',
        })
        self.assertEqual(resp_bad.status_code, 401)

        # A valid but non-emergency analyst credential must not be allowed to
        # halt the DDMA fallback timer.
        resp_analyst = self.client.post('/api/emergency/acknowledge', json={
            'request_id': req_id,
            'acknowledgement_token': 'wrong-token-abc',
        }, headers={'Authorization': 'Bearer visbharat-analyst-token'})
        self.assertEqual(resp_analyst.status_code, 401)

        # 3. Successful acknowledgement with valid token
        resp_good = self.client.post('/api/emergency/acknowledge', json={
            'request_id': req_id,
            'acknowledgement_token': valid_token,
            'officer_name': 'Inspector K. Velusamy',
            'officer_badge': 'DDMA-VEL-09',
            'notes': 'Unit 3 deployed and on-site',
        })
        self.assertEqual(resp_good.status_code, 200)
        data = resp_good.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['emergency_dispatch_status'], 'acknowledged')
        self.assertIn('Inspector K. Velusamy', data['acknowledged_by'])

        # 4. Verify status via GET endpoint
        resp_status_public = self.client.get(f'/api/emergency/dispatch-status/{req_id}')
        self.assertEqual(resp_status_public.status_code, 401)
        resp_status = self.client.get(
            f'/api/emergency/dispatch-status/{req_id}',
            headers={'Authorization': 'Bearer visbharat-admin-token'},
        )
        self.assertEqual(resp_status.status_code, 200)
        status_data = resp_status.get_json()
        self.assertTrue(status_data['is_acknowledged'])
        self.assertEqual(status_data['emergency_dispatch_status'], 'acknowledged')
        self.assertIsNotNone(status_data['acknowledged_at'])

    def test_emergency_15min_unacknowledged_fallback_escalation(self):
        db = get_db()
        req_id = f"NVB-TEST-ESC-{secrets.token_hex(4)}"
        # Simulate dispatch occurring 20 minutes ago
        dispatched_time = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat().replace('+00:00', 'Z')
        db.execute(
            '''
            INSERT INTO citizen_requests (
                request_id, source_channel, input_language, district, state,
                lat, lng, original_text, translated_text, category, urgency,
                sentiment, status, submitted_by, ai_metadata_json, created_at,
                emergency_dispatch_status, dispatched_to, dispatched_at, acknowledgement_token,
                fallback_target
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                req_id, 'Web Form', 'en', 'Vellore', 'Tamil Nadu',
                12.9, 79.1, 'Building collapse', 'Building collapse', 'Road', 'Emergency',
                'Very Negative', 'New', 'Tester', '{}', dispatched_time,
                'dispatched', 'District Disaster Management Authority (DDMA)', dispatched_time, 'test-token',
                'Collectorate 24/7 Crisis Hotline & District Magistrate Desk'
            )
        )
        db.commit()

        # Run escalation check
        resp_public = self.client.post('/api/emergency/check-escalations')
        self.assertEqual(resp_public.status_code, 401)
        resp = self.client.post(
            '/api/emergency/check-escalations',
            headers={'Authorization': 'Bearer visbharat-admin-token'},
        )
        self.assertEqual(resp.status_code, 200)
        res_data = resp.get_json()
        self.assertGreaterEqual(res_data['escalated_count'], 1)

        # Check DB state
        row = db.execute('SELECT emergency_dispatch_status, fallback_escalated_at FROM citizen_requests WHERE request_id = ?', (req_id,)).fetchone()
        self.assertEqual(row['emergency_dispatch_status'], 'fallback_escalated')
        self.assertIsNotNone(row['fallback_escalated_at'])

    def test_ml_evidence_dossier_and_concurrency_artifacts_exist(self):
        repo_root = Path(__file__).resolve().parent.parent
        dossier = repo_root / "docs" / "evaluation" / "MODEL_EVIDENCE_DOSSIER.md"
        benchmark = repo_root / "docs" / "evaluation" / "FASTPATH_CONCURRENCY_BENCHMARK.md"

        self.assertTrue(dossier.is_file(), "MODEL_EVIDENCE_DOSSIER.md must exist")
        self.assertTrue(benchmark.is_file(), "FASTPATH_CONCURRENCY_BENCHMARK.md must exist")

        dossier_text = dossier.read_text(encoding='utf-8')
        self.assertIn("SHA-256 Digest", dossier_text)
        self.assertIn("ROC-AUC", dossier_text)
        self.assertIn("Brier Score", dossier_text)
        self.assertIn("Population Stability Index", dossier_text)


if __name__ == '__main__':
    unittest.main()
