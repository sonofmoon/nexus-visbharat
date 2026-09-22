import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
from visbharat import create_app
from visbharat.db import get_db, close_db
from visbharat.services import provider_evidence as evidence
from visbharat.services.runtime_readiness import validate
from tests import test_analyst_workbench as analyst_fixture


class ProviderEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Flask(__name__)
        self.app.config.update(DATABASE_URL='', DATABASE_PATH=str(Path(self.tmp.name)/'evidence.db'))
        self.app.teardown_appcontext(close_db)
        with self.app.app_context(): evidence.migrate(get_db())

    def client(self, fn):
        client=SimpleNamespace(classify_request=fn)
        self.app.extensions['google_ai_client']=client
        return evidence.instrument(client,'google_ai')

    def test_configured_is_not_verified_and_success_persists_across_client_restart(self):
        client=self.client(lambda *a: {'model':'provider-returned-version','provider_mode':'google_ai_live','fallback_used':False})
        with self.app.app_context():
            self.assertEqual(evidence.status()['services']['google_ai']['status'],'configured')
            result=client.classify_request('private citizen text')
            self.assertTrue(result['provider_evidence']['persisted'])
            self.assertEqual(result['provider_evidence']['model'],'provider-returned-version')
        self.client(lambda: None)
        with self.app.app_context():
            data=evidence.status()['services']['google_ai']
            self.assertEqual(data['status'],'verified')
            self.assertNotIn('private citizen text',json.dumps(data))
            self.assertEqual(data['operations']['translate_text']['status'],'configured')

    def test_failure_overrides_success_without_exposing_error_or_prompt(self):
        client=self.client(lambda: {'model':'served','provider_mode':'google_ai_live'})
        with self.app.app_context(): client.classify_request()
        def fail(): raise RuntimeError('private token in provider error')
        client=self.client(fail)
        with self.app.app_context():
            with self.assertRaises(RuntimeError): client.classify_request()
            result=evidence.status()['services']['google_ai']
            self.assertEqual(result['operations']['classify_request']['status'],'unavailable')
            self.assertNotIn('private token',json.dumps(result))

    def test_fallback_and_stale_results_are_not_verified(self):
        client=self.client(lambda: {'model':'local','provider_mode':'local_fallback','fallback_used':True})
        with self.app.app_context():
            client.classify_request()
            self.assertEqual(evidence.status()['services']['google_ai']['status'],'degraded')
            get_db().execute("UPDATE provider_invocations SET checked_at='2000-01-01T00:00:00+00:00'");get_db().commit()
            self.assertEqual(evidence.status()['services']['google_ai']['status'],'configured')

    def test_empty_transcript_is_not_verified(self):
        client=SimpleNamespace(transcribe_bytes=lambda: {'transcript':'','model':'speech'})
        self.app.extensions['google_stt_client']=client
        evidence.instrument(client,'google_stt')
        with self.app.app_context():
            client.transcribe_bytes()
            self.assertNotEqual(evidence.status()['services']['google_stt']['status'],'verified')


class RuntimeReadinessTest(unittest.TestCase):
    def test_cloud_sqlite_requires_explicit_disposable_demo(self):
        with patch.dict(os.environ,{'K_SERVICE':'test-service'}):
            with self.assertRaises(RuntimeError):validate({'DATABASE_URL':'','DEMO_MODE':True})
            with self.assertRaises(RuntimeError):validate({'DATABASE_URL':'','DEMO_MODE':False,'ALLOW_EPHEMERAL_SHOWCASE':True})
            validate({'DATABASE_URL':'','DEMO_MODE':True,'ALLOW_EPHEMERAL_SHOWCASE':True})
            validate({'DATABASE_URL':'postgresql://user:secret@host/db','DEMO_MODE':False})

    def test_unknown_database_never_falls_back_to_sqlite(self):
        with self.assertRaises(RuntimeError):validate({'DATABASE_URL':'postgresql+wrong://host/db'})

    def test_status_does_not_disclose_database_credentials_and_pages_render(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app({'TESTING':True,'DATABASE_URL':'','DATABASE_PATH':str(Path(directory)/'app.db'),
                'DEMO_MODE':True,'SEED_DEMO_DATA':False,'DISABLE_EXTERNAL_SERVICES':True,'AUTO_MIGRATE':True})
            client=app.test_client()
            with patch('visbharat.blueprints.api.get_db',return_value=SimpleNamespace(backend='postgres')):
                app.config['DATABASE_URL']='postgresql://private-user:private-password@example/db'
                data=client.get('/api/db/status').get_json()
                self.assertNotIn('private-',json.dumps(data));self.assertNotIn('target',data)
            app.config['DATABASE_URL']=''
            self.assertEqual(client.get('/readyz').status_code,200)
            self.assertEqual(client.get('/submission').status_code,200)
            data=client.get('/api/submission/readiness').get_json()
            self.assertFalse(data['evaluation']['independent_human_review'])
            self.assertEqual(data['providers']['services']['google_ai']['status'],'unavailable')


class JourneyTest(unittest.TestCase):
    def setUp(self):
        self.fixture=analyst_fixture.AnalystWorkbenchTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.client=self.fixture.client;self.headers=self.fixture.headers

    def test_journey_links_same_ticket_and_never_invents_approval(self):
        response=self.client.get('/api/v2/analyst/requests/one/journey',headers=self.headers)
        self.assertEqual(response.status_code,200,response.get_json())
        data=response.get_json()
        self.assertEqual(data['request']['request_id'],'one')
        self.assertEqual(data['clusters'][0]['cluster_id'],'SAME')
        self.assertEqual(data['data_mode'],'synthetic')
        self.assertTrue(data['planning_eligible']);self.assertEqual(data['decisions'],[])
        self.assertTrue(data['project_id'].startswith('NVB-P-'))

    def test_journey_requires_auth_and_respects_requested_scope(self):
        self.assertEqual(self.client.get('/api/v2/analyst/requests/one/journey').status_code,401)
        response=self.client.get('/api/v2/analyst/requests/one/journey?district=Hyderabad',headers=self.headers)
        self.assertEqual(response.status_code,404)

    def test_emergency_is_not_capital_evidence(self):
        data=self.client.get('/api/v2/analyst/requests/emergency/journey',headers=self.headers).get_json()
        self.assertFalse(data['planning_eligible'])
