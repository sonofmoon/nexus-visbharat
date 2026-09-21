import os
import unittest

from visbharat import create_app
from visbharat.db import init_db


class TestDatabaseUrlRuntime(unittest.TestCase):
    def test_sqlite_database_url_is_used_when_set(self):
        db_path = os.path.join(os.getcwd(), 'tests', 'test_runtime_url.db')
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass

        app = create_app()
        app.config.update(
            TESTING=True,
            DATABASE_PATH=os.path.join(os.getcwd(), 'tests', 'should_not_be_used.db'),
            DATABASE_URL='sqlite:///tests/test_runtime_url.db',
        )

        with app.app_context():
            init_db()

        self.assertTrue(os.path.exists(db_path))

        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass
        fallback = os.path.join(os.getcwd(), 'tests', 'should_not_be_used.db')
        if os.path.exists(fallback):
            try:
                os.remove(fallback)
            except OSError:
                pass



    def test_db_status_reports_active_backend(self):
        db_path = os.path.join(os.getcwd(), 'tests', 'test_runtime_url_status.db')
        if os.path.exists(db_path):
            os.remove(db_path)

        app = create_app()
        app.config.update(
            TESTING=True,
            DATABASE_PATH=os.path.join(os.getcwd(), 'tests', 'should_not_be_used_status.db'),
            DATABASE_URL='sqlite:///tests/test_runtime_url_status.db',
        )

        with app.app_context():
            init_db()

        client = app.test_client()
        response = client.get('/api/db/status')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertTrue(payload['success'])
        self.assertEqual(payload['backend'], 'sqlite')
        self.assertTrue(payload['database_url_configured'])

        if os.path.exists(db_path):
            os.remove(db_path)
        fallback = os.path.join(os.getcwd(), 'tests', 'should_not_be_used_status.db')
        if os.path.exists(fallback):
            os.remove(fallback)

if __name__ == '__main__':
    unittest.main()


