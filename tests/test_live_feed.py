import json
import os
import unittest

from visbharat import create_app
from visbharat.db import get_db, init_db, seed_default_users


class LiveFeedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_live_feed.db')
        if os.path.exists(cls.db_path):
            os.remove(cls.db_path)
        cls.app = create_app()
        cls.app.config.update(TESTING=True, DATABASE_PATH=cls.db_path, DATABASE_URL='', DEMO_MODE=False)
        with cls.app.app_context():
            init_db()
            seed_default_users()
            db = get_db()
            db.execute('DELETE FROM citizen_requests')
            db.execute(
                '''INSERT INTO citizen_requests (
                    request_id, source_channel, input_language, district, state, lat, lng,
                    original_text, translated_text, category, urgency, sentiment, status,
                    ai_metadata_json, created_at, ward
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                ('NVB-FEED-001', 'Web Form', 'ta', 'Ariyalur', 'Tamil Nadu', 10.0, 78.0,
                 'Water supply is irregular', 'Water supply is irregular', 'Water Supply', 'Routine',
                 'Neutral', 'Pending', '{}', '2026-09-25T10:00:00Z', 'Ward 01'),
            )
            db.execute(
                '''INSERT INTO citizen_requests (
                    request_id, source_channel, input_language, district, state, lat, lng,
                    original_text, translated_text, category, urgency, sentiment, status,
                    ai_metadata_json, created_at, ward
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                ('NVB-FEED-002', 'Email', 'en', 'Ariyalur', 'Tamil Nadu', 10.0, 78.0,
                 'Your NVB request has been received. Ticket ID: NVB-FEED-002. '
                 'The request is now queued for municipal review. Do not reply with passwords, Aadhaar numbers, bank details, or other sensitive information.',
                 'Your NVB request has been received. Ticket ID: NVB-FEED-002. '
                 'The request is now queued for municipal review. Do not reply with passwords, Aadhaar numbers, bank details, or other sensitive information.',
                 'Other', 'Routine', 'Neutral', 'Pending', '{}', '2026-09-25T10:01:00Z', 'Ward 01'),
            )
            db.commit()
        cls.client = cls.app.test_client()

    def test_feed_filters_and_suppresses_system_confirmation(self):
        response = self.client.get('/api/complaints?date_from=2026-09-25&language=ta&channel=Web%20Form')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['count'], 1)
        self.assertEqual(payload['complaints'][0]['request_id'], 'NVB-FEED-001')
        self.assertEqual(payload['suppressed_system_count'], 0)

        unfiltered = self.client.get('/api/complaints').get_json()
        self.assertEqual(unfiltered['count'], 1)
        self.assertEqual(unfiltered['suppressed_system_count'], 1)

    def test_feed_can_include_system_records_for_audit(self):
        payload = self.client.get('/api/complaints?include_system=1').get_json()
        self.assertEqual(payload['count'], 2)
        system_item = next(item for item in payload['complaints'] if item['request_id'] == 'NVB-FEED-002')
        self.assertTrue(system_item['is_system_generated'])

    def test_live_feed_stream_exposes_sse_contract(self):
        response = self.client.get('/api/v1/live-feed/stream?since_at=2026-09-25T10:01:00Z&since_id=NVB-FEED-002', buffered=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/event-stream')
        first_chunk = next(response.response).decode('utf-8')
        self.assertIn('event: connected', first_chunk)
        response.close()


if __name__ == '__main__':
    unittest.main()
