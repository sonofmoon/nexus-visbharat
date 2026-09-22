"""Run the same behavioral contracts on a disposable PostgreSQL database in CI."""
import os
import unittest
from unittest.mock import patch
from visbharat import create_app as real_create_app
from tests import test_ministry_pilot as contract
if os.name=='nt' and os.environ.get('NVB_TEST_POSTGRES_BIN'):
    _dll_directory=os.add_dll_directory(os.environ['NVB_TEST_POSTGRES_BIN'])


@unittest.skipUnless(os.environ.get('NVB_TEST_POSTGRES_URL'), 'Disposable PostgreSQL URL not configured')
class PostgresMinistryPilotTest(contract.MinistryPilotTest):
    def setUp(self):
        import psycopg
        from psycopg import sql
        import uuid
        url=os.environ['NVB_TEST_POSTGRES_URL'];schema='pilot_test_'+uuid.uuid4().hex
        with psycopg.connect(url,autocommit=True) as db:db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        def cleanup():
            with psycopg.connect(url,autocommit=True) as db:db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        self.addCleanup(cleanup)
        # Each test gets a unique schema; never reset the public schema or existing records.
        scoped=psycopg.conninfo.make_conninfo(url,options='-c search_path='+schema)
        from visbharat.db import DbConnectionAdapter
        from psycopg.rows import dict_row
        def connect(_url):return DbConnectionAdapter(psycopg.connect(scoped,row_factory=dict_row),'postgres')
        connection_patch=patch('visbharat.db._connect_postgres',connect);connection_patch.start();self.addCleanup(connection_patch.stop)
        def factory(config):return real_create_app({**config,'DATABASE_URL':url})
        with patch.object(contract,'create_app',factory):super().setUp()

    def test_new_app_instance_reads_committed_ticket_review_and_queue(self):
        rid=self.submit().get_json()['request_id']
        self.assertEqual(self.review(rid).status_code,200)
        restarted=real_create_app({**dict(self.app.config),'AUTO_MIGRATE':False,'LOCAL_EVALUATION_WORKER':False})
        result=restarted.test_client().get('/api/v2/analyst/requests/'+rid+'/journey',headers=self.analyst)
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertEqual(result.get_json()['request']['request_id'],rid)
        with restarted.app_context():
            from visbharat.db import get_db
            self.assertEqual(get_db().execute("SELECT COUNT(*) n FROM pilot_outbox WHERE request_id=? AND kind='process_intake'",(rid,)).fetchone()['n'],1)
