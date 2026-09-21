import os
import sqlite3
import unittest

from visbharat import create_app
from visbharat.db import get_db, init_db, seed_default_users


class TestDataIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(os.path.join(os.getcwd(), 'tests'), exist_ok=True)
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_runtime_integrity.db')
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

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except OSError:
                pass

    def test_users_api_token_unique_constraint(self):
        with self.app.app_context():
            db = get_db()
            now = '2026-01-01T00:00:00'

            db.execute(
                'INSERT INTO users (name, api_token, role, created_at) VALUES (?, ?, ?, ?)',
                ('User One', 'dup-token', 'analyst', now),
            )
            db.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    'INSERT INTO users (name, api_token, role, created_at) VALUES (?, ?, ?, ?)',
                    ('User Two', 'dup-token', 'auditor', now),
                )
                db.commit()

    def test_citizen_request_not_null_constraints(self):
        with self.app.app_context():
            db = get_db()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute(
                    '''
                    INSERT INTO citizen_requests (
                        request_id, source_channel, input_language, district, state, lat, lng,
                        original_text, translated_text, category, urgency, sentiment, status,
                        submitted_by, ai_metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        'REQ-1',
                        'Web',
                        'en',
                        'Test District',
                        'Test State',
                        0.0,
                        0.0,
                        'raw text',
                        'translated text',
                        None,
                        'Urgent',
                        'Negative',
                        'New',
                        'anonymous',
                        '{}',
                        '2026-01-01T00:00:00',
                    ),
                )
                db.commit()


if __name__ == '__main__':
    unittest.main()

