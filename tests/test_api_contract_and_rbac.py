import hashlib
import hmac
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from visbharat import create_app
from visbharat.db import init_db, seed_default_users, ensure_request_lifecycle_tables, ensure_notification_deliveries_table, ensure_notification_dead_letters_table, ensure_notification_connector_health_table, ensure_notification_receipt_events_table, ensure_notification_slo_alert_events_table, ensure_demand_cluster_tables, ensure_federation_external_events_table, ensure_request_cosign_tables, ensure_consent_ledger_table, ensure_ivr_callback_tables, ensure_ivr_callback_alert_events_table, get_db
from visbharat.services.clustering import assign_request_to_cluster
from visbharat.services.code_mix import normalize_code_mix
from visbharat.services.google_speech import GoogleSpeechToTextClient
from visbharat.services.notifications import dispatch_sla_notification, register_notification_receipt_event


class BaseApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(os.path.join(os.getcwd(), 'tests'), exist_ok=True)
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_runtime_api.db')
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
            JURY_REQUIRE_LIVE_MODELS=False,
        )

        with cls.app.app_context():
            init_db()
            seed_default_users()
            # The distributable CSV now contains 12,500 demo rows. Contract tests
            # need only their own records so bounded SLA sweeps reach the fixture.
            get_db().execute('DELETE FROM citizen_requests')
            get_db().commit()
            ensure_request_lifecycle_tables()
            ensure_notification_deliveries_table()
            ensure_notification_dead_letters_table()
            ensure_notification_connector_health_table()
            ensure_notification_receipt_events_table()
            ensure_notification_slo_alert_events_table()
            ensure_demand_cluster_tables()
            ensure_federation_external_events_table()
            ensure_request_cosign_tables()
            ensure_consent_ledger_table()
            ensure_ivr_callback_tables()
            ensure_ivr_callback_alert_events_table()
            cls.valid_district = str(cls.app.extensions['reference_repo'].df_districts.iloc[0]['district'])

        cls.client = cls.app.test_client()
        cls.admin_token = cls.app.config['ADMIN_API_TOKEN']
        cls.analyst_token = cls.app.config['ANALYST_API_TOKEN']
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


class TestApiContracts(BaseApiTestCase):
    def test_health_contract(self):
        response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertTrue(payload['success'])
        self.assertEqual(payload['status'], 'healthy')
        self.assertIn('ai_mode', payload)
        self.assertIn('stt_mode', payload)
        self.assertIn('google_maps_configured', payload)

    def test_ai_status_contract(self):
        response = self.client.get('/api/ai/status')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertTrue(payload['success'])
        self.assertIn('mode', payload)
        self.assertIn('stt_mode', payload)
        self.assertIn('supported_features', payload)
        self.assertIn('google_stt_configured', payload)
        self.assertNotIn('bhashini_stt_configured', payload)
        self.assertNotIn('ai4bharat_stt_configured', payload)
        self.assertEqual(payload.get('preferred_asr_provider'), 'google')
        self.assertIn('cloud_run_service_configured', payload)
        self.assertIn('google_bigquery_configured', payload)
        self.assertIn('bigquery_mode', payload)
        self.assertIn('google_vertex_configured', payload)
        self.assertIn('vertex_mode', payload)
        self.assertIn('google_dialogflow_configured', payload)
        self.assertIn('dialogflow_mode', payload)
        self.assertIsInstance(payload['supported_features'], list)

    def test_language_asr_metrics_rbac_and_contract(self):
        denied = self.client.get('/api/v1/language/asr/metrics', headers=self._auth(self.analyst_token))
        self.assertEqual(denied.status_code, 403)

        response = self.client.get('/api/v1/language/asr/metrics', headers=self._auth(self.admin_token), query_string={'limit': 50})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('metrics', payload)
        self.assertIn('meta', payload)
        self.assertIn('total_events', payload['metrics'])
        self.assertIn('providers', payload['metrics'])
        self.assertIn('recent_events', payload['metrics'])

    def test_language_asr_metrics_tracks_voice_ingest_events(self):
        response = self.client.post(
            '/api/submit-voice',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Voice IVR',
                'audio_base64': 'dGVzdA==',
                'audio_mime_type': 'audio/wav',
            },
        )
        self.assertEqual(response.status_code, 200)

        metrics = self.client.get('/api/v1/language/asr/metrics', headers=self._auth(self.admin_token))
        self.assertEqual(metrics.status_code, 200)
        payload = metrics.get_json()['metrics']
        self.assertGreaterEqual(int(payload.get('total_events') or 0), 1)


    def test_language_asr_control_endpoints_rbac_and_update(self):
        old_override = dict(self.app.extensions.get('asr_circuit_state') or {}).get('__override__', {})
        try:
            denied_control = self.client.get('/api/v1/language/asr/control', headers=self._auth(self.analyst_token))
            self.assertEqual(denied_control.status_code, 403)

            denied_override = self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.auditor_token),
                json={'mode': 'force', 'forced_provider': 'simulation'},
            )
            self.assertEqual(denied_override.status_code, 403)

            set_override = self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': 'force', 'forced_provider': 'simulation', 'bypass_circuit': True},
            )
            self.assertEqual(set_override.status_code, 200)
            o_payload = set_override.get_json()
            self.assertTrue(o_payload['success'])
            self.assertEqual((o_payload.get('override') or {}).get('mode'), 'force')
            self.assertEqual((o_payload.get('override') or {}).get('forced_provider'), 'simulation')

            control = self.client.get('/api/v1/language/asr/control', headers=self._auth(self.auditor_token))
            self.assertEqual(control.status_code, 200)
            c_payload = control.get_json()
            self.assertTrue(c_payload['success'])
            self.assertIn('control', c_payload)
            self.assertIn('role', c_payload['control'])
            self.assertIn('override', c_payload['control'])
            self.assertEqual((c_payload['control']['override'] or {}).get('forced_provider'), 'simulation')
        finally:
            restore_mode = str(old_override.get('mode') or 'auto')
            restore_provider = str(old_override.get('forced_provider') or '')
            restore_bypass = bool(old_override.get('bypass_circuit', False))
            self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': restore_mode, 'forced_provider': restore_provider, 'bypass_circuit': restore_bypass},
            )
    def test_language_asr_circuit_reset_endpoint(self):
        old_override = dict(self.app.extensions.get('asr_circuit_state') or {}).get('__override__', {})
        try:
            seed_override = self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': 'force', 'forced_provider': 'simulation'},
            )
            self.assertEqual(seed_override.status_code, 200)

            reset_bad = self.client.post(
                '/api/v1/language/asr/circuit/reset',
                headers=self._auth(self.admin_token),
                json={'provider': 'invalid'},
            )
            self.assertEqual(reset_bad.status_code, 400)

            denied = self.client.post(
                '/api/v1/language/asr/circuit/reset',
                headers=self._auth(self.auditor_token),
                json={'provider': 'bhashini'},
            )
            self.assertEqual(denied.status_code, 403)

            reset_ok = self.client.post(
                '/api/v1/language/asr/circuit/reset',
                headers=self._auth(self.admin_token),
                json={'provider': 'bhashini'},
            )
            self.assertEqual(reset_ok.status_code, 200)
            payload = reset_ok.get_json()
            self.assertTrue(payload['success'])
            self.assertIn('bhashini', payload.get('reset') or [])
        finally:
            restore_mode = str(old_override.get('mode') or 'auto')
            restore_provider = str(old_override.get('forced_provider') or '')
            restore_bypass = bool(old_override.get('bypass_circuit', False))
            self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': restore_mode, 'forced_provider': restore_provider, 'bypass_circuit': restore_bypass},
            )


    def test_language_asr_control_audit_trail_contract_and_rbac(self):
        old_override = dict(self.app.extensions.get('asr_circuit_state') or {}).get('__override__', {})
        try:
            denied = self.client.get('/api/v1/language/asr/control/audit-trail', headers=self._auth(self.analyst_token))
            self.assertEqual(denied.status_code, 403)

            create_evt = self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': 'force', 'forced_provider': 'simulation', 'bypass_circuit': False},
            )
            self.assertEqual(create_evt.status_code, 200)

            response = self.client.get('/api/v1/language/asr/control/audit-trail', headers=self._auth(self.auditor_token), query_string={'limit': 10})
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload['success'])
            self.assertIn('items', payload)
            self.assertIn('meta', payload)
            self.assertIn('export_metadata', payload)
            self.assertIn('digest_sha256', payload['export_metadata'])
            self.assertIn('signature_hmac_sha256', payload['export_metadata'])
            self.assertGreaterEqual(int((payload.get('meta') or {}).get('count') or 0), 1)
            first = (payload.get('items') or [{}])[0]
            self.assertIn('action', first)
            self.assertIn('actor', first)
            self.assertIn('created_at', first)
        finally:
            restore_mode = str(old_override.get('mode') or 'auto')
            restore_provider = str(old_override.get('forced_provider') or '')
            restore_bypass = bool(old_override.get('bypass_circuit', False))
            self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': restore_mode, 'forced_provider': restore_provider, 'bypass_circuit': restore_bypass},
            )


    def test_language_asr_control_audit_trail_csv_export_and_filters(self):
        old_override = dict(self.app.extensions.get('asr_circuit_state') or {}).get('__override__', {})
        try:
            create_evt = self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': 'force', 'forced_provider': 'simulation', 'bypass_circuit': False},
            )
            self.assertEqual(create_evt.status_code, 200)

            csv_resp = self.client.get(
                '/api/v1/language/asr/control/audit-trail',
                headers=self._auth(self.admin_token),
                query_string={'format': 'csv', 'limit': 20, 'action': 'language_asr_override_updated'},
            )
            self.assertEqual(csv_resp.status_code, 200)
            self.assertIn('text/csv', csv_resp.content_type)
            body = csv_resp.get_data(as_text=True)
            self.assertIn('# exported_by,', body)
            self.assertIn('# exported_at,', body)
            self.assertIn('# digest_sha256,', body)
            self.assertIn('# signature_hmac_sha256,', body)
            self.assertIn('actor,action,resource_type,resource_id,ip_address,created_at,details', body)
            self.assertIn('language_asr_override_updated', body)

            bad_filter = self.client.get(
                '/api/v1/language/asr/control/audit-trail',
                headers=self._auth(self.admin_token),
                query_string={'action': 'invalid_action'},
            )
            self.assertEqual(bad_filter.status_code, 400)
        finally:
            restore_mode = str(old_override.get('mode') or 'auto')
            restore_provider = str(old_override.get('forced_provider') or '')
            restore_bypass = bool(old_override.get('bypass_circuit', False))
            self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': restore_mode, 'forced_provider': restore_provider, 'bypass_circuit': restore_bypass},
            )


    def test_language_asr_audit_export_verification_endpoint(self):
        old_override = dict(self.app.extensions.get('asr_circuit_state') or {}).get('__override__', {})
        try:
            create_evt = self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': 'force', 'forced_provider': 'simulation', 'bypass_circuit': False},
            )
            self.assertEqual(create_evt.status_code, 200)

            export_resp = self.client.get(
                '/api/v1/language/asr/control/audit-trail',
                headers=self._auth(self.admin_token),
                query_string={'limit': 10},
            )
            self.assertEqual(export_resp.status_code, 200)
            export_meta = (export_resp.get_json() or {}).get('export_metadata') or {}
            self.assertIn('digest_sha256', export_meta)

            denied = self.client.post(
                '/api/v1/language/asr/control/audit-trail/verify',
                headers=self._auth(self.analyst_token),
                json={'export_metadata': export_meta},
            )
            self.assertEqual(denied.status_code, 403)

            verify_ok = self.client.post(
                '/api/v1/language/asr/control/audit-trail/verify',
                headers=self._auth(self.auditor_token),
                json={'export_metadata': export_meta},
            )
            self.assertEqual(verify_ok.status_code, 200)
            v_payload = verify_ok.get_json()
            self.assertTrue(v_payload['success'])
            self.assertTrue((v_payload.get('verification') or {}).get('valid'))

            tampered = dict(export_meta)
            tampered['count'] = int(tampered.get('count') or 0) + 1
            verify_bad = self.client.post(
                '/api/v1/language/asr/control/audit-trail/verify',
                headers=self._auth(self.admin_token),
                json={'export_metadata': tampered},
            )
            self.assertEqual(verify_bad.status_code, 200)
            bad_payload = verify_bad.get_json()
            self.assertFalse((bad_payload.get('verification') or {}).get('valid'))

            invalid = self.client.post(
                '/api/v1/language/asr/control/audit-trail/verify',
                headers=self._auth(self.admin_token),
                json={'export_metadata': 'not-an-object'},
            )
            self.assertEqual(invalid.status_code, 400)
        finally:
            restore_mode = str(old_override.get('mode') or 'auto')
            restore_provider = str(old_override.get('forced_provider') or '')
            restore_bypass = bool(old_override.get('bypass_circuit', False))
            self.client.post(
                '/api/v1/language/asr/provider-override',
                headers=self._auth(self.admin_token),
                json={'mode': restore_mode, 'forced_provider': restore_provider, 'bypass_circuit': restore_bypass},
            )

    def test_asr_provider_contract_google_stt(self):
        class _StubGoogleSTT:
            @staticmethod
            def transcribe_bytes(audio_bytes, language_code, mime_type='audio/wav'):
                return {
                    'transcript': 'Google Cloud Speech-to-Text transcript',
                    'confidence': 0.95,
                    'provider': 'google',
                    'model': 'Google Cloud Speech-to-Text',
                }

        self.app.config.update(LANGUAGE_ASR_PROVIDER='google')
        self.app.extensions['google_stt_client'] = _StubGoogleSTT()

        response = self.client.post(
            '/api/submit-voice',
            json={
                'language': 'ta',
                'district': self.valid_district,
                'source': 'Voice IVR',
                'audio_base64': 'dGVzdA==',
                'audio_mime_type': 'audio/webm;codecs=opus',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['transcript'], 'Google Cloud Speech-to-Text transcript')

    def test_submit_requires_text(self):
        response = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': '',
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['error'], 'text is required')

    def test_submit_success_contract(self):
        response = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Street lights are not working near school.',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertTrue(payload['success'])
        self.assertIn('request_id', payload)
        self.assertIn('classification', payload)
        self.assertIn('ai_mode', payload)

    def test_submission_evidence_separates_local_save_from_cloud_and_field_validation(self):
        original_bigquery = self.app.extensions.get('google_bigquery_client')
        original_pubsub = self.app.extensions.get('google_pubsub_client')
        self.app.extensions['google_bigquery_client'] = None
        self.app.extensions['google_pubsub_client'] = None
        try:
            typed = self.client.post('/api/submit', json={
                'language': 'en', 'district': self.valid_district, 'source': 'Web Form',
                'text': 'Water pressure is low in the market lane.',
            })
            voice = self.client.post('/api/submit-voice', json={
                'language': 'en', 'district': self.valid_district, 'source': 'Voice IVR',
                'audio_base64': 'dGVzdA==', 'audio_mime_type': 'audio/wav',
            })
        finally:
            self.app.extensions['google_bigquery_client'] = original_bigquery
            self.app.extensions['google_pubsub_client'] = original_pubsub

        for response, endpoint in ((typed, '/api/submit'), (voice, '/api/submit-voice')):
            self.assertEqual(response.status_code, 200, response.get_json())
            payload = response.get_json()
            self.assertEqual(payload['cloud_ingestion']['ingest_status'], 'not_configured')
            evidence = payload['submission_evidence']
            self.assertEqual(evidence['endpoint'], endpoint)
            self.assertEqual(evidence['local_persistence'], 'saved')
            self.assertEqual(evidence['cloud_validation'], 'not_configured')
            self.assertEqual(evidence['field_validation'], 'not_completed')

            with self.app.app_context():
                row = get_db().execute(
                    'SELECT ai_metadata_json FROM citizen_requests WHERE request_id = ?',
                    (payload['request_id'],),
                ).fetchone()
            self.assertEqual(json.loads(row['ai_metadata_json'])['submission_evidence']['endpoint'], endpoint)

    def test_cloud_ingestion_errors_do_not_expose_credentials(self):
        class FailingBigQuery:
            def insert_request(self, _record):
                raise RuntimeError('api-key=secret-should-not-leak')

        original_bigquery = self.app.extensions.get('google_bigquery_client')
        original_pubsub = self.app.extensions.get('google_pubsub_client')
        self.app.extensions['google_bigquery_client'] = FailingBigQuery()
        self.app.extensions['google_pubsub_client'] = None
        try:
            response = self.client.post('/api/submit', json={
                'language': 'en', 'district': self.valid_district, 'source': 'Web Form',
                'text': 'Drain cover is damaged at the bus stop.',
            })
        finally:
            self.app.extensions['google_bigquery_client'] = original_bigquery
            self.app.extensions['google_pubsub_client'] = original_pubsub

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload['cloud_ingestion']['ingest_status'], 'failed_dead_letter')
        self.assertNotIn('secret-should-not-leak', json.dumps(payload))
        self.assertEqual(payload['cloud_ingestion']['errors'], ['BigQuery replication failed'])


    def test_submit_includes_ward_department_routing(self):
        with self.app.app_context():
            repo = self.app.extensions['reference_repo']
            districts = set(repo.list_districts())

        district = 'Chennai' if 'Chennai' in districts else self.valid_district

        response = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': district,
                'source': 'Web Form',
                'text': 'Street lights are not working in ward 6',
                'ward': '6',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertIn('routing', payload)
        routing = payload['routing']
        self.assertIn('service', routing)
        self.assertIn('department', routing)
        self.assertEqual(routing.get('ward'), '6')

        with self.app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT ward, service_type, routed_department FROM citizen_requests WHERE request_id = ?",
                (payload['request_id'],),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual((row['ward'] or ''), '6')
            self.assertTrue((row['service_type'] or '').strip())
            self.assertTrue((row['routed_department'] or '').strip())

    def test_submit_map_assisted_ward_tagging_contract(self):
        response = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Overflow near market junction',
                'lat': 13.0827,
                'lng': 80.2707,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('routing', payload)
        self.assertTrue((payload['routing'].get('ward') or '').strip())


    def test_geo_ward_suggest_pin_polygon_and_svamitva_contract(self):
        self.app.config.update(
            PIN_GEOCODE_INDEX={
                '600001': {'lat': 13.08, 'lng': 80.27, 'district': self.valid_district, 'village': 'Village-A'}
            },
            WARD_POLYGONS={
                self.valid_district: [
                    {
                        'ward': 'W-POLY-01',
                        'points': [
                            {'lat': 13.05, 'lng': 80.25},
                            {'lat': 13.10, 'lng': 80.25},
                            {'lat': 13.10, 'lng': 80.30},
                            {'lat': 13.05, 'lng': 80.30},
                        ],
                    }
                ]
            },
            SVAMITVA_VILLAGE_MAPS={
                self.valid_district: [
                    {
                        'village': 'Village-A',
                        'points': [
                            {'lat': 13.05, 'lng': 80.25},
                            {'lat': 13.10, 'lng': 80.25},
                            {'lat': 13.10, 'lng': 80.30},
                            {'lat': 13.05, 'lng': 80.30},
                        ],
                    }
                ]
            },
        )

        response = self.client.post(
            '/api/v1/geo/ward-suggest',
            json={'district': self.valid_district, 'pin': '600001', 'text': 'water issue'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload.get('ward'), 'W-POLY-01')
        self.assertEqual(payload.get('ward_source'), 'ward_polygon')
        self.assertEqual(payload.get('village'), 'Village-A')
        self.assertEqual(payload.get('pin'), '600001')

    def test_submit_uses_pin_geocode_context(self):
        self.app.config.update(
            PIN_GEOCODE_INDEX={
                '600001': {'lat': 13.08, 'lng': 80.27, 'district': self.valid_district, 'village': 'Village-A'}
            },
            WARD_POLYGONS={
                self.valid_district: [
                    {
                        'ward': 'W-POLY-01',
                        'points': [
                            {'lat': 13.05, 'lng': 80.25},
                            {'lat': 13.10, 'lng': 80.25},
                            {'lat': 13.10, 'lng': 80.30},
                            {'lat': 13.05, 'lng': 80.30},
                        ],
                    }
                ]
            },
            SVAMITVA_VILLAGE_MAPS={
                self.valid_district: [
                    {
                        'village': 'Village-A',
                        'points': [
                            {'lat': 13.05, 'lng': 80.25},
                            {'lat': 13.10, 'lng': 80.25},
                            {'lat': 13.10, 'lng': 80.30},
                            {'lat': 13.05, 'lng': 80.30},
                        ],
                    }
                ]
            },
        )

        response = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Need drainage clearance',
                'pin': '600001',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual((payload.get('routing') or {}).get('ward'), 'W-POLY-01')
    def test_submit_voice_accepts_webm_codecs_mime(self):
        response = self.client.post(
            '/api/submit-voice',
            json={
                'language': 'ta',
                'district': self.valid_district,
                'source': 'Voice IVR',
                'audio_base64': 'dGVzdA==',
                'audio_mime_type': 'audio/webm;codecs=opus',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertTrue(payload['success'])
        self.assertIn('request_id', payload)
        self.assertIn('transcript', payload)


    def test_submit_idempotency_key_reuses_existing_request(self):
        headers = {'X-Idempotency-Key': 'submit-idem-001'}
        body = {
            'language': 'en',
            'district': self.valid_district,
            'source': 'Web Form',
            'text': 'Drain cover is broken near market road.',
        }

        first = self.client.post('/api/submit', json=body, headers=headers)
        second = self.client.post('/api/submit', json=body, headers=headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_payload = first.get_json()
        second_payload = second.get_json()

        self.assertEqual(first_payload['request_id'], second_payload['request_id'])
        self.assertTrue(second_payload['idempotent_reused'])

    def test_submit_voice_idempotency_key_reuses_existing_request(self):
        headers = {'X-Idempotency-Key': 'voice-idem-001'}
        body = {
            'language': 'ta',
            'district': self.valid_district,
            'source': 'Voice IVR',
            'audio_base64': 'dGVzdA==',
            'audio_mime_type': 'audio/webm;codecs=opus',
        }

        first = self.client.post('/api/submit-voice', json=body, headers=headers)
        second = self.client.post('/api/submit-voice', json=body, headers=headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_payload = first.get_json()
        second_payload = second.get_json()

        self.assertEqual(first_payload['request_id'], second_payload['request_id'])
        self.assertTrue(second_payload['idempotent_reused'])

    def test_email_webhook_idempotency_key_reuses_existing_request(self):
        self.app.config.update(
            WEBHOOK_SHARED_TOKEN='email-webhook-token',
            WEBHOOK_REQUIRE_REPLAY_PROTECTION=False,
            WEBHOOK_ALLOWED_IPS='',
            TWILIO_AUTH_TOKEN='',
            TELEGRAM_WEBHOOK_SECRET='',
            META_APP_SECRET='',
        )
        headers = {
            'X-Webhook-Token': 'email-webhook-token',
            'X-Idempotency-Key': 'email-idem-001',
        }
        body = {
            'body': 'Garbage collection missed for 3 days in ward lane.',
            'language': 'en',
            'district': self.valid_district,
            'from': 'resident@example.org',
        }

        first = self.client.post('/api/channels/email/webhook', json=body, headers=headers)
        second = self.client.post('/api/channels/email/webhook', json=body, headers=headers)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_payload = first.get_json()
        second_payload = second.get_json()

        self.assertEqual(first_payload['request_id'], second_payload['request_id'])
        self.assertTrue(second_payload['idempotent_reused'])

        with self.app.app_context():
            db = get_db()
            count_row = db.execute(
                "SELECT COUNT(*) AS c FROM citizen_requests WHERE request_id = ?",
                (first_payload['request_id'],),
            ).fetchone()
            self.assertEqual(int(count_row['c']), 1)
    def test_sms_keyword_workflow_new_status_cosign(self):
        self.app.config.update(WEBHOOK_REQUIRE_REPLAY_PROTECTION=False, WEBHOOK_ALLOWED_IPS='')
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        new_resp = self.client.post(
            '/api/channels/sms/keyword',
            headers=headers,
            json={
                'text': 'NV NEW Drainage overflow near bus stand',
                'language': 'en',
                'district': self.valid_district,
                'from': '919900001111',
            },
        )
        self.assertEqual(new_resp.status_code, 200)
        new_payload = new_resp.get_json()
        self.assertTrue(new_payload['success'])
        self.assertEqual(new_payload.get('keyword_action'), 'new')
        request_id = new_payload['request_id']

        status_resp = self.client.post(
            '/api/channels/sms/keyword',
            headers=headers,
            json={'text': f'NV STATUS {request_id}', 'from': '919900001111'},
        )
        self.assertEqual(status_resp.status_code, 200)
        status_payload = status_resp.get_json()
        self.assertTrue(status_payload['success'])
        self.assertEqual(status_payload.get('keyword_action'), 'status')
        self.assertEqual(status_payload['request']['request_id'], request_id)

        token_resp = self.client.get(f'/api/v1/requests/{request_id}/cosign-status')
        self.assertEqual(token_resp.status_code, 200)
        token = token_resp.get_json()['token']['value']

        cosign_resp = self.client.post(
            '/api/channels/sms/keyword',
            headers=headers,
            json={'text': f'NV COSIGN {token}', 'from': '919900001111'},
        )
        self.assertEqual(cosign_resp.status_code, 200)
        cosign_payload = cosign_resp.get_json()
        self.assertTrue(cosign_payload['success'])
        self.assertEqual(cosign_payload.get('keyword_action'), 'cosign')
        self.assertGreaterEqual(int(cosign_payload.get('verified_support_count') or 0), 1)

    def test_ivr_missed_call_orchestration_contract(self):
        self.app.config.update(WEBHOOK_REQUIRE_REPLAY_PROTECTION=False, WEBHOOK_ALLOWED_IPS='')
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}
        response = self.client.post(
            '/api/channels/ivr/missed-call',
            headers=headers,
            json={'phone': '919955551111', 'district': self.valid_district, 'language': 'ta'},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload.get('status'), 'scheduled')
        self.assertIn('callback_job_id', payload)


    def test_ivr_callback_state_machine_retry_dead_letter(self):
        self.app.config.update(WEBHOOK_REQUIRE_REPLAY_PROTECTION=False, WEBHOOK_ALLOWED_IPS='')
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        create = self.client.post(
            '/api/channels/ivr/missed-call',
            headers=headers,
            json={'phone': '919911110000', 'district': self.valid_district, 'language': 'en'},
        )
        self.assertEqual(create.status_code, 200)
        callback_id = create.get_json()['callback_id']

        self.app.config.update(IVR_CALLBACK_RETRY_BASE_SECONDS=120, IVR_CALLBACK_RETRY_BACKOFF_MULTIPLIER=2.0, IVR_CALLBACK_RETRY_MAX_SECONDS=1800)
        p1 = self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        self.assertEqual(p1.status_code, 200)
        first_processed = p1.get_json().get('processed', [])
        self.assertTrue(first_processed)
        self.assertIn('next_retry_at', first_processed[0])
        self.assertEqual(first_processed[0].get('status'), 'retry_pending')

        immediate = self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        self.assertEqual(immediate.status_code, 200)
        self.assertEqual(int(immediate.get_json().get('count') or 0), 0)

        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ivr_callback_jobs SET next_retry_at = ? WHERE callback_id = ?", ('1970-01-01T00:00:00Z', callback_id))
            db.commit()

        p2 = self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        self.assertEqual(p2.status_code, 200)
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ivr_callback_jobs SET next_retry_at = ? WHERE callback_id = ?", ('1970-01-01T00:00:00Z', callback_id))
            db.commit()
        p3 = self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        self.assertEqual(p3.status_code, 200)

        status = self.client.get(f'/api/channels/ivr/callbacks/{callback_id}', headers=self._auth(self.auditor_token))
        self.assertEqual(status.status_code, 200)
        payload = status.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['callback']['status'], 'failed')

        with self.app.app_context():
            db = get_db()
            row = db.execute('SELECT COUNT(*) AS c FROM ivr_callback_dead_letters WHERE callback_id = ?', (callback_id,)).fetchone()
            self.assertGreaterEqual(int(row['c'] or 0), 1)
    def test_intelligence_demand_velocity_and_silence_map_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Road repair needed urgently',
            },
        )
        self.assertEqual(submit.status_code, 200)

        velocity = self.client.get('/api/v1/intelligence/demand-velocity', headers=self._auth(self.analyst_token), query_string={'days': 30, 'limit': 20})
        self.assertEqual(velocity.status_code, 200)
        vpayload = velocity.get_json()
        self.assertTrue(vpayload['success'])
        self.assertIn('items', vpayload)

        silence = self.client.get('/api/v1/intelligence/silence-map', headers=self._auth(self.auditor_token), query_string={'limit': 20})
        self.assertEqual(silence.status_code, 200)
        spayload = silence.get_json()
        self.assertTrue(spayload['success'])
        self.assertIn('items', spayload)
    def test_attachment_ingest_stub_success_contract(self):
        response = self.client.post(
            '/api/attachments/ingest-stub',
            json={
                'request_id': 'VB-test-attachment-001',
                'attachment_manifest': {
                    'photos': [{'name': 'photo1.jpg', 'type': 'image/jpeg', 'source': 'photos'}],
                    'files': [{'name': 'memo.pdf', 'type': 'application/pdf', 'source': 'files'}],
                    'camera_captures': [{'name': 'camera-capture-1.jpg', 'type': 'image/jpeg', 'source': 'camera'}],
                    'google_ready': True,
                },
                'camera_captures_base64': ['dGVzdA=='],
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertTrue(payload['success'])
        self.assertTrue(payload['validated'])
        self.assertEqual(payload['request_id'], 'VB-test-attachment-001')
        self.assertEqual(payload['accepted_count'], 3)
        self.assertEqual(payload['rejected_count'], 0)
        self.assertEqual(payload['issues'], [])

    def test_attachment_ingest_stub_validation_error(self):
        response = self.client.post(
            '/api/attachments/ingest-stub',
            json={
                'request_id': 'VB-test-attachment-002',
                'attachment_manifest': {
                    'photos': [{'name': 'photo1.jpg', 'type': 'image/jpeg', 'source': 'photos'}],
                    'files': [],
                    'camera_captures': [{'name': 'camera-capture-1.jpg', 'type': 'image/jpeg', 'source': 'camera'}],
                },
                'camera_captures_base64': [],
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()

        self.assertFalse(payload['success'])
        self.assertFalse(payload['validated'])
        self.assertEqual(payload['request_id'], 'VB-test-attachment-002')
        self.assertGreater(payload['rejected_count'], 0)
        self.assertIn('camera_captures_base64 count must match attachment_manifest.camera_captures count', payload['issues'])




    def test_submit_returns_initial_sla_contract(self):
        response = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Water leakage in main road',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertIn('sla', payload)
        self.assertIn('due_at', payload['sla'])
        self.assertIsNone(payload['sla']['breached_at'])
        self.assertEqual(payload['sla']['escalation_level'], 0)
        self.assertIn('policy_mode', payload['sla'])
        self.assertIn('tier1_target', payload['sla'])


    def test_sla_v11_category_channel_rule_and_target_contract(self):
        self.app.config.update(
            SLA_URGENCY_HOURS={'Routine': 72, 'Urgent': 24, 'Emergency': 4},
            SLA_RULES={
                'by_category': {},
                'by_channel': {},
                'by_category_channel': {
                    'Road|WhatsApp': {'Routine': 1, 'Urgent': 1, 'Emergency': 1},
                },
            },
            SLA_ESCALATION_TARGETS={
                'by_department': {},
                'by_channel': {
                    'WhatsApp': {
                        'department': 'Escalation NOC',
                        'assignee': 'NOC Lead',
                    }
                },
            },
        )

        response = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'WhatsApp',
                'text': 'Road pothole issue in ward 8',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertEqual(payload['sla'].get('policy_mode'), 'category_channel')
        self.assertEqual(payload['sla'].get('tier1_target', {}).get('department'), 'Escalation NOC')
        self.assertEqual(payload['sla'].get('tier1_target', {}).get('assignee'), 'NOC Lead')

        due_at = datetime.fromisoformat(payload['sla']['due_at'].replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta_hours = (due_at - now).total_seconds() / 3600.0
        self.assertLessEqual(delta_hours, 1.5)

    def test_timeline_auto_applies_sla_breach_and_tier1_escalation(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Road damage unresolved',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', request_id),
            )
            db.commit()

        timeline_resp = self.client.get(f'/api/requests/{request_id}/timeline')
        self.assertEqual(timeline_resp.status_code, 200)
        payload = timeline_resp.get_json()

        self.assertEqual(payload['request']['status'], 'Escalated')
        self.assertEqual(int(payload['request'].get('sla_escalation_level') or 0), 1)
        self.assertTrue((payload['request'].get('sla_breached_at') or '').strip())

        event_types = [event.get('event_type') for event in payload['timeline']]
        self.assertIn('sla_breached', event_types)
        self.assertIn('sla_escalated_tier1', event_types)
        escalated_event = next(e for e in payload['timeline'] if e.get('event_type') == 'sla_escalated_tier1')
        self.assertIn('tier1_target', escalated_event.get('details', {}))


    def test_sla_v12_tier2_escalation_and_notification_hooks(self):
        self.app.config.update(
            SLA_TIER2_DELAY_HOURS=1,
            SLA_RULES={
                'by_category': {},
                'by_channel': {},
                'by_category_channel': {},
                'tier2_delay_hours': 1,
            },
            SLA_ESCALATION_TARGETS={
                'by_department': {},
                'by_channel': {},
                'by_department_tier2': {},
                'by_channel_tier2': {
                    'Web Form': {
                        'department': 'State Control Room',
                        'assignee': 'Escalation Commander',
                    }
                },
            },
        )

        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Persistent drainage overflow',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ?, sla_breached_at = ?, sla_escalation_level = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z', 1, request_id),
            )
            db.commit()

        sweep = self.client.post('/api/v1/sla/sweep', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(sweep.status_code, 200)

        timeline_resp = self.client.get(f'/api/requests/{request_id}/timeline')
        self.assertEqual(timeline_resp.status_code, 200)
        payload = timeline_resp.get_json()

        self.assertEqual(int(payload['request'].get('sla_escalation_level') or 0), 2)
        event_types = [event.get('event_type') for event in payload['timeline']]
        self.assertIn('sla_escalated_tier2', event_types)

        tier2_event = next(e for e in payload['timeline'] if e.get('event_type') == 'sla_escalated_tier2')
        tier2_target = tier2_event.get('details', {}).get('tier2_target', {})
        self.assertEqual(tier2_target.get('department'), 'State Control Room')
        self.assertEqual(tier2_target.get('assignee'), 'Escalation Commander')

        logs_resp = self.client.get('/api/v1/audit-logs', headers=self._auth(self.auditor_token))
        self.assertEqual(logs_resp.status_code, 200)
        logs = logs_resp.get_json()['audit_logs']
        actions = [entry.get('action') for entry in logs]
        self.assertIn('request_sla_tier2_escalated', actions)
        self.assertIn('sla_notification_hook_emitted', actions)

        with self.app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT COUNT(*) AS c FROM notification_deliveries WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            self.assertGreaterEqual(int(row['c']), 1)

    def test_notification_deliveries_endpoint_contract_and_rbac(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Repeated transformer outage',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ?, sla_breached_at = ?, sla_escalation_level = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z', 1, request_id),
            )
            db.commit()

        sweep = self.client.post('/api/v1/sla/sweep', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(sweep.status_code, 200)

        analyst = self.client.get('/api/v1/notifications/deliveries', headers=self._auth(self.analyst_token))
        self.assertEqual(analyst.status_code, 403)

        auditor = self.client.get(
            '/api/v1/notifications/deliveries',
            headers=self._auth(self.auditor_token),
            query_string={'request_id': request_id, 'stage': 'tier2'},
        )
        self.assertEqual(auditor.status_code, 200)
        payload = auditor.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('deliveries', payload)
        self.assertGreaterEqual(len(payload['deliveries']), 1)

        first = payload['deliveries'][0]
        self.assertIn('delivery_id', first)
        self.assertIn('status', first)
        self.assertIn('target', first)
        self.assertIn('payload', first)

    def test_notification_ops_overlay_endpoint_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Ops overlay smoke test for notifications',
            },
        )
        self.assertEqual(submit.status_code, 200)

        overlay = self.client.get('/api/v1/notifications/ops-overlay', headers=self._auth(self.auditor_token), query_string={'limit': 50})
        self.assertEqual(overlay.status_code, 200)
        payload = overlay.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('overlay', payload)
        self.assertIn('metrics', payload['overlay'])
        self.assertIn('connectors', payload['overlay'])
        self.assertIn('slo', payload['overlay'])
        self.assertIn('recent_deliveries', payload['overlay'])
        self.assertIn('recent_receipt_events', payload['overlay'])

    def test_notification_receipt_and_metrics_endpoints_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Long outage in ward',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ?, sla_breached_at = ?, sla_escalation_level = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z', 1, request_id),
            )
            db.commit()

        sweep = self.client.post('/api/v1/sla/sweep', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(sweep.status_code, 200)

        deliveries = self.client.get('/api/v1/notifications/deliveries', headers=self._auth(self.admin_token), query_string={'request_id': request_id})
        self.assertEqual(deliveries.status_code, 200)
        items = deliveries.get_json()['deliveries']
        self.assertGreaterEqual(len(items), 1)
        delivery_id = items[0]['delivery_id']

        reconcile = self.client.post(
            '/api/v1/notifications/receipts',
            headers=self._auth(self.admin_token),
            json={'delivery_id': delivery_id, 'provider': items[0].get('provider') or 'email', 'status': 'delivered', 'external_id': 'ACK-001'},
        )
        self.assertEqual(reconcile.status_code, 200)

        metrics = self.client.get('/api/v1/notifications/metrics', headers=self._auth(self.auditor_token))
        self.assertEqual(metrics.status_code, 200)
        self.assertTrue(metrics.get_json()['success'])
        self.assertIn('by_status', metrics.get_json()['metrics'])
    def test_notification_connector_health_and_receipt_events_endpoints_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Escalation test for health endpoint',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ?, sla_breached_at = ?, sla_escalation_level = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z', 1, request_id),
            )
            db.commit()

        sweep = self.client.post('/api/v1/sla/sweep', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(sweep.status_code, 200)

        deliveries = self.client.get('/api/v1/notifications/deliveries', headers=self._auth(self.admin_token), query_string={'request_id': request_id})
        self.assertEqual(deliveries.status_code, 200)
        delivery_id = deliveries.get_json()['deliveries'][0]['delivery_id']

        with self.app.app_context():
            evt = register_notification_receipt_event(
                delivery_id=delivery_id,
                provider='email',
                status='delivered',
                external_id='ACK-OPS-001',
                raw_payload={'delivery_id': delivery_id, 'provider': 'email', 'status': 'delivered', 'external_id': 'ACK-OPS-001'},
            )
            self.assertTrue(evt['accepted'])

        health = self.client.get('/api/v1/notifications/connectors/health', headers=self._auth(self.admin_token))
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.get_json()['success'])
        self.assertIn('connectors', health.get_json())

        events = self.client.get('/api/v1/notifications/receipt-events', headers=self._auth(self.auditor_token), query_string={'delivery_id': delivery_id})
        self.assertEqual(events.status_code, 200)
        payload = events.get_json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload['events']), 1)
    def test_notification_slo_fanout_multi_target_priority_weight(self):
        self.app.config.update(
            NOTIFICATION_SLO_MIN_DELIVERIES=1,
            NOTIFICATION_SLO_MAX_FAILURE_RATE=0.0,
            NOTIFICATION_SLO_MAX_OPEN_CIRCUITS=0,
            OUTBOUND_CONNECTORS_ENABLED=False,
            NOTIFICATION_SLO_ALERT_TARGETS={
                'targets': [
                    {'email': 'low@example.org', 'language': 'en', 'priority': 10, 'weight': 1, 'active': True},
                    {'email': 'high@example.org', 'language': 'en', 'priority': 90, 'weight': 5, 'active': True},
                    {'email': 'mid@example.org', 'language': 'en', 'priority': 50, 'weight': 2, 'active': True},
                ]
            },
            NOTIFICATION_SLO_ALERT_MAX_TARGETS_PER_ALERT=2,
            NOTIFICATION_SLO_ALERT_MAX_PER_RUN=5,
            NOTIFICATION_SLO_ALERT_DEDUPE_SECONDS=0,
            NOTIFICATION_SLO_ALERT_COOLDOWN_SECONDS=0,
        )

        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM notification_slo_alert_events')
            db.execute('DELETE FROM notification_deliveries')
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            db.execute("DELETE FROM notification_deliveries")
            db.execute("DELETE FROM notification_slo_alert_events")
            db.execute(
                "INSERT INTO notification_deliveries (delivery_id, request_id, stage, provider, channel, target_json, payload_json, status, attempt_count, max_retries, external_id, last_error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ('NOTIFY-SLO-MULTI-001', 'VB-SLO-MULTI-1', 'tier1', 'email', 'Web Form', '{}', '{}', 'failed', 1, 0, '', 'simulated failure', now, now),
            )
            db.commit()

        response = self.client.get('/api/v1/notifications/slo-dashboard', headers=self._auth(self.admin_token), query_string={'notify': 'true'})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['notify'])
        notes = payload.get('notifications') or []
        self.assertGreaterEqual(len(notes), 1)

        joined = json.dumps(notes)
        self.assertIn('high@example.org', joined)
        self.assertIn('mid@example.org', joined)
        self.assertNotIn('low@example.org', joined)

    def test_notification_slo_dashboard_notify_hooks_contract(self):
        self.app.config.update(
            NOTIFICATION_SLO_MIN_DELIVERIES=1,
            NOTIFICATION_SLO_MAX_FAILURE_RATE=0.0,
            NOTIFICATION_SLO_MAX_OPEN_CIRCUITS=0,
            OUTBOUND_CONNECTORS_ENABLED=False,
            NOTIFICATION_SLO_ALERT_TARGET={'email': 'ops@example.org', 'language': 'en'},
            NOTIFICATION_SLO_ALERT_TARGETS={'targets': [{'email': 'ops@example.org', 'language': 'en', 'priority': 100, 'weight': 1, 'active': True}]},
            NOTIFICATION_SLO_ALERT_MAX_TARGETS_PER_ALERT=1,
            NOTIFICATION_SLO_ALERT_MAX_PER_RUN=5,
            NOTIFICATION_SLO_ALERT_DEDUPE_SECONDS=600,
            NOTIFICATION_SLO_ALERT_COOLDOWN_SECONDS=600,
        )

        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM notification_slo_alert_events')
            db.execute('DELETE FROM notification_deliveries')
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            db.execute(
                "INSERT INTO notification_deliveries (delivery_id, request_id, stage, provider, channel, target_json, payload_json, status, attempt_count, max_retries, external_id, last_error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ('NOTIFY-SLO-001', 'VB-SLO-REQ-1', 'tier1', 'email', 'Web Form', '{}', '{}', 'failed', 1, 0, '', 'simulated failure', now, now),
            )
            db.execute(
                "INSERT OR REPLACE INTO notification_connector_health (provider, active_key_slot, failure_count, open_until_epoch, last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ('email', 'primary', 2, int(timezone.utc.utcoffset(None).total_seconds() if timezone.utc.utcoffset(None) else 0), 'open', now),
            )
            db.commit()

        response = self.client.get('/api/v1/notifications/slo-dashboard', headers=self._auth(self.admin_token), query_string={'notify': 'true'})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['notify'])
        self.assertGreaterEqual(len(payload.get('notifications') or []), 1)

        second = self.client.get('/api/v1/notifications/slo-dashboard', headers=self._auth(self.admin_token), query_string={'notify': 'true'})
        self.assertEqual(second.status_code, 200)
        second_payload = second.get_json()
        self.assertTrue(second_payload['success'])
        self.assertTrue(second_payload['notify'])
        self.assertEqual(len(second_payload.get('notifications') or []), 0)
        self.assertGreaterEqual(len(second_payload.get('dashboard', {}).get('alerts') or []), 1)

    def test_notification_slo_dashboard_contract(self):
        response = self.client.get('/api/v1/notifications/slo-dashboard', headers=self._auth(self.auditor_token))
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('dashboard', payload)
        self.assertIn('slo', payload['dashboard'])
        self.assertIn('observed', payload['dashboard'])

    def test_receipt_status_normalization_matrix_email_sms_whatsapp(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Status matrix validation request',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ?, sla_breached_at = ?, sla_escalation_level = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z', 1, request_id),
            )
            db.commit()

        sweep = self.client.post('/api/v1/sla/sweep', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(sweep.status_code, 200)

        deliveries = self.client.get('/api/v1/notifications/deliveries', headers=self._auth(self.admin_token), query_string={'request_id': request_id})
        self.assertEqual(deliveries.status_code, 200)
        delivery_id = deliveries.get_json()['deliveries'][0]['delivery_id']

        email_rc = self.client.post(
            '/api/v1/notifications/receipts',
            headers=self._auth(self.admin_token),
            json={'delivery_id': delivery_id, 'provider': 'email', 'status': 'bounced', 'external_id': 'EMAIL-BOUNCE-1'},
        )
        self.assertEqual(email_rc.status_code, 200)
        self.assertEqual(email_rc.get_json()['receipt']['status'], 'failed')

        sms_rc = self.client.post(
            '/api/v1/notifications/receipts',
            headers=self._auth(self.admin_token),
            json={'delivery_id': delivery_id, 'provider': 'sms', 'status': 'received', 'external_id': 'SMS-RECV-1'},
        )
        self.assertEqual(sms_rc.status_code, 200)
        self.assertEqual(sms_rc.get_json()['receipt']['status'], 'sent')

        wa_rc = self.client.post(
            '/api/v1/notifications/receipts',
            headers=self._auth(self.admin_token),
            json={'delivery_id': delivery_id, 'provider': 'whatsapp', 'status': 'read', 'external_id': 'WA-READ-1'},
        )
        self.assertEqual(wa_rc.status_code, 200)
        self.assertEqual(wa_rc.get_json()['receipt']['status'], 'delivered')

    def test_receipt_status_normalization_aliases(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'SMS outage in ward 1',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ?, sla_breached_at = ?, sla_escalation_level = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z', 1, request_id),
            )
            db.commit()

        sweep = self.client.post('/api/v1/sla/sweep', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(sweep.status_code, 200)

        deliveries = self.client.get('/api/v1/notifications/deliveries', headers=self._auth(self.admin_token), query_string={'request_id': request_id})
        self.assertEqual(deliveries.status_code, 200)
        delivery_id = deliveries.get_json()['deliveries'][0]['delivery_id']

        reconcile = self.client.post(
            '/api/v1/notifications/receipts',
            headers=self._auth(self.admin_token),
            json={'delivery_id': delivery_id, 'provider': 'sms', 'status': 'delivrd', 'external_id': 'ACK-SMS-1'},
        )
        self.assertEqual(reconcile.status_code, 200)
        receipt = reconcile.get_json()['receipt']
        self.assertEqual(receipt['status'], 'delivered')
        self.assertIn('metadata', receipt)
        self.assertEqual((receipt.get('metadata') or {}).get('raw_status'), 'delivrd')

    def test_notification_receipt_webhook_signature_verification(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Drainage overflow near school',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ?, sla_breached_at = ?, sla_escalation_level = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', '2000-01-01T00:00:00Z', 1, request_id),
            )
            db.commit()

        sweep = self.client.post('/api/v1/sla/sweep', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(sweep.status_code, 200)

        deliveries = self.client.get('/api/v1/notifications/deliveries', headers=self._auth(self.admin_token), query_string={'request_id': request_id})
        self.assertEqual(deliveries.status_code, 200)
        delivery_id = deliveries.get_json()['deliveries'][0]['delivery_id']

        self.app.config.update(
            NOTIFICATION_RECEIPT_TOKEN='receipt-token-v2',
            NOTIFICATION_RECEIPT_EMAIL_SECRET='receipt-email-secret',
            NOTIFICATION_RECEIPT_BIND_REPLAY_IN_SIGNATURE=True,
        )

        body = {'delivery_id': delivery_id, 'provider': 'email', 'status': 'delivered', 'external_id': 'ACK-WEBHOOK-1'}
        raw = json.dumps(body).encode('utf-8')
        ts = '1725862200'
        nonce = 'nonce-001'
        bad_headers = {
            'X-Notification-Token': 'receipt-token-v2',
            'X-Notification-Provider': 'email',
            'X-Notification-Timestamp': ts,
            'X-Notification-Nonce': nonce,
            'X-Notification-Signature': 'sha256=deadbeef',
            'Content-Type': 'application/json',
        }
        bad = self.client.post('/api/notifications/receipts/webhook', headers=bad_headers, data=raw)
        self.assertEqual(bad.status_code, 401)

        invalid_body = {'delivery_id': delivery_id, 'provider': 'email', 'status': 'delivered'}
        invalid_raw = json.dumps(invalid_body).encode('utf-8')
        invalid = self.client.post('/api/notifications/receipts/webhook', headers=bad_headers, data=invalid_raw)
        self.assertEqual(invalid.status_code, 400)

        digest = hmac.new(
            b'receipt-email-secret',
            (ts + ':' + nonce + ':').encode('utf-8') + raw,
            hashlib.sha256,
        ).hexdigest()
        good_headers = dict(bad_headers)
        good_headers['X-Notification-Signature'] = f'sha256={digest}'
        good = self.client.post('/api/notifications/receipts/webhook', headers=good_headers, data=raw)
        self.assertEqual(good.status_code, 200)
        self.assertTrue(good.get_json()['success'])

        dup = self.client.post('/api/notifications/receipts/webhook', headers=good_headers, data=raw)
        self.assertEqual(dup.status_code, 200)
        self.assertTrue(bool(dup.get_json().get('duplicate')))

    def test_connector_secret_provider_resolution(self):
        self.app.config.update(
            OUTBOUND_CONNECTORS_ENABLED=True,
            NOTIFICATION_SECRET_PROVIDER_ENABLED=True,
            NOTIFICATION_SECRET_PROVIDER_PREFIX='vb/connectors',
            NOTIFICATION_CHANNEL_PREFERENCE=['email'],
            NOTIFICATION_MAX_RETRIES=0,
            NOTIFICATION_CIRCUIT_BREAKER_ENABLED=False,
            EMAIL_CONNECTOR_ENDPOINT='https://connector.local/email',
            EMAIL_CONNECTOR_API_KEY='fallback-primary',
            EMAIL_CONNECTOR_API_KEY_NEXT='',
            EMAIL_CONNECTOR_SIGNING_SECRET='fallback-sign',
        )

        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM notification_connector_health')
            db.commit()

            def _provider(name: str):
                if name.endswith('/email/api_key_primary'):
                    return 'provider-primary'
                if name.endswith('/email/signing_secret'):
                    return 'provider-signing'
                return ''

            self.app.extensions['secret_provider'] = _provider

            seen = {}
            def _post(url, payload, headers=None, timeout=5):
                seen['auth'] = str((headers or {}).get('Authorization') or '')
                seen['sig'] = str((headers or {}).get('X-Notification-Signature') or '')
                return 200, 'ok'

            with patch('visbharat.services.notifications._post_json', side_effect=_post):
                res = dispatch_sla_notification(
                    request_id='VB-CONN-SEC-001',
                    stage='tier1',
                    actor='system',
                    source_channel='Web Form',
                    target={'email': 'ops@example.org', 'language': 'en'},
                    sla_due_at='2000-01-01T00:00:00Z',
                    details={},
                )

            self.assertEqual(res['status'], 'sent')
            self.assertEqual(seen.get('auth'), 'Bearer provider-primary')
            self.assertTrue(str(seen.get('sig') or '').startswith('sha256='))

    def test_connector_provider_native_whatsapp_cloud_adapter(self):
        self.app.config.update(
            OUTBOUND_CONNECTORS_ENABLED=True,
            NOTIFICATION_CHANNEL_PREFERENCE=['whatsapp'],
            NOTIFICATION_MAX_RETRIES=0,
            WHATSAPP_CONNECTOR_PROVIDER='meta',
            WHATSAPP_CONNECTOR_ENDPOINT='https://graph.facebook.com/v20.0/123/messages',
            WHATSAPP_CONNECTOR_API_KEY='wa-token',
        )

        captured = {}
        def _post(url, payload, headers=None, timeout=5):
            captured['url'] = url
            captured['payload'] = payload
            captured['auth'] = str((headers or {}).get('Authorization') or '')
            return 200, 'wa-ok'

        with self.app.app_context():
            with patch('visbharat.services.notifications._post_json', side_effect=_post):
                result = dispatch_sla_notification(
                    request_id='VB-CONN-WA-001',
                    stage='tier1',
                    actor='system',
                    source_channel='Web Form',
                    target={'phone': '+15550007777', 'language': 'en'},
                    sla_due_at='2000-01-01T00:00:00Z',
                    details={},
                )

        self.assertEqual(result['status'], 'sent')
        self.assertEqual(result['provider'], 'whatsapp')
        self.assertEqual(captured.get('auth'), 'Bearer wa-token')
        self.assertEqual((captured.get('payload') or {}).get('messaging_product'), 'whatsapp')

    def test_connector_provider_native_twilio_sms_adapter(self):
        self.app.config.update(
            OUTBOUND_CONNECTORS_ENABLED=True,
            NOTIFICATION_CHANNEL_PREFERENCE=['sms'],
            NOTIFICATION_MAX_RETRIES=0,
            SMS_CONNECTOR_PROVIDER='twilio',
            SMS_TWILIO_ACCOUNT_SID='AC123',
            SMS_TWILIO_AUTH_TOKEN='token123',
            SMS_TWILIO_FROM_NUMBER='+15550001111',
        )

        class _FakeMessages:
            def create(self, body=None, from_=None, to=None):
                class _R:
                    sid = 'SM-123'
                return _R()

        class _FakeClient:
            def __init__(self):
                self.messages = _FakeMessages()

        with self.app.app_context():
            with patch('visbharat.services.notifications._twilio_client', return_value=_FakeClient()):
                result = dispatch_sla_notification(
                    request_id='VB-CONN-TWILIO-001',
                    stage='tier1',
                    actor='system',
                    source_channel='Web Form',
                    target={'phone': '+15550009999', 'language': 'en'},
                    sla_due_at='2000-01-01T00:00:00Z',
                    details={},
                )

            self.assertEqual(result['status'], 'sent')
            self.assertEqual(result['provider'], 'sms')
            self.assertTrue(str(result.get('external_id') or '').startswith('SM-'))

    def test_connector_rotation_and_circuit_breaker(self):
        self.app.config.update(
            OUTBOUND_CONNECTORS_ENABLED=True,
            NOTIFICATION_SECRET_PROVIDER_ENABLED=False,
            OUTBOUND_CONNECTOR_SIGNING_ENABLED=True,
            OUTBOUND_CONNECTOR_SIGN_BIND_NONCE=True,
            NOTIFICATION_CHANNEL_PREFERENCE=['email'],
            NOTIFICATION_MAX_RETRIES=0,
            NOTIFICATION_RETRY_BACKOFF_SECONDS=0,
            NOTIFICATION_CIRCUIT_BREAKER_ENABLED=True,
            NOTIFICATION_CIRCUIT_FAIL_THRESHOLD=2,
            EMAIL_CONNECTOR_ENDPOINT='https://connector.local/email',
            EMAIL_CONNECTOR_API_KEY='primary-key',
            EMAIL_CONNECTOR_API_KEY_NEXT='secondary-key',
            EMAIL_CONNECTOR_SIGNING_SECRET='email-sign-secret',
            EMAIL_CONNECTOR_ACTIVE_KEY_SLOT='primary',
        )

        with self.app.app_context():
            self.app.extensions['secret_provider'] = None
            db = get_db()
            db.execute('CREATE TABLE IF NOT EXISTS notification_dead_letters (id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT NOT NULL, request_id TEXT NOT NULL, stage TEXT NOT NULL, provider TEXT, payload_json TEXT, last_error TEXT, created_at TEXT NOT NULL)')
            db.execute('DELETE FROM notification_deliveries')
            db.execute('DELETE FROM notification_connector_health')
            db.commit()

            def _rotation_post(url, payload, headers=None, timeout=5):
                auth = str((headers or {}).get('Authorization') or '')
                signature = str((headers or {}).get('X-Notification-Signature') or '')
                self.assertTrue(signature.startswith('sha256='))
                self.assertTrue(bool((headers or {}).get('X-Notification-Timestamp')))
                self.assertTrue(bool((headers or {}).get('X-Notification-Nonce')))
                if auth == 'Bearer primary-key':
                    return 401, 'unauthorized'
                if auth == 'Bearer secondary-key':
                    return 200, 'sent-secondary'
                return 500, 'unexpected'

            with patch('visbharat.services.notifications._post_json', side_effect=_rotation_post):
                first = dispatch_sla_notification(
                    request_id='VB-CONN-ROT-001',
                    stage='tier1',
                    actor='system',
                    source_channel='Web Form',
                    target={'email': 'ops@example.org', 'language': 'en'},
                    sla_due_at='2000-01-01T00:00:00Z',
                    details={},
                )
            self.assertEqual(first['status'], 'sent')
            self.assertEqual(first['provider'], 'email')

            def _always_fail(url, payload, headers=None, timeout=5):
                return 500, 'connector down'

            with patch('visbharat.services.notifications._post_json', side_effect=_always_fail):
                fail1 = dispatch_sla_notification(
                    request_id='VB-CONN-CB-001',
                    stage='tier1',
                    actor='system',
                    source_channel='Web Form',
                    target={'email': 'ops@example.org', 'language': 'en'},
                    sla_due_at='2000-01-01T00:00:00Z',
                    details={},
                )
                fail2 = dispatch_sla_notification(
                    request_id='VB-CONN-CB-002',
                    stage='tier1',
                    actor='system',
                    source_channel='Web Form',
                    target={'email': 'ops@example.org', 'language': 'en'},
                    sla_due_at='2000-01-01T00:00:00Z',
                    details={},
                )
            self.assertEqual(fail1['status'], 'failed')
            self.assertEqual(fail2['status'], 'failed')

            with patch('visbharat.services.notifications._post_json', side_effect=Exception('should-not-call')) as mocked:
                blocked = dispatch_sla_notification(
                    request_id='VB-CONN-CB-003',
                    stage='tier1',
                    actor='system',
                    source_channel='Web Form',
                    target={'email': 'ops@example.org', 'language': 'en'},
                    sla_due_at='2000-01-01T00:00:00Z',
                    details={},
                )
            self.assertEqual(mocked.call_count, 0)
            self.assertEqual(blocked['status'], 'failed')
            self.assertIn('circuit open', str(blocked.get('last_error') or '').lower())

            row = db.execute('SELECT provider, failure_count, open_until_epoch FROM notification_connector_health WHERE provider = ? LIMIT 1', ('email',)).fetchone()
            self.assertIsNotNone(row)
            self.assertGreaterEqual(int(row['failure_count'] or 0), 2)
            self.assertGreater(int(row['open_until_epoch'] or 0), 0)

    def test_admin_sla_sweep_endpoint_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Streetlight outage',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        with self.app.app_context():
            db = get_db()
            db.execute(
                "UPDATE citizen_requests SET sla_due_at = ? WHERE request_id = ?",
                ('2000-01-01T00:00:00Z', request_id),
            )
            db.commit()

        sweep = self.client.post('/api/v1/sla/sweep', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(sweep.status_code, 200)
        payload = sweep.get_json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(int(payload['checked']), 1)
        self.assertIn('request_ids', payload)


    def test_demand_cluster_recompute_and_list_contract(self):
        samples = [
            {'text': 'No drinking water in ward 4 for two days', 'language': 'en'},
            {'text': 'Water not coming in our area since yesterday', 'language': 'en'},
            {'text': 'Road potholes causing accidents near bus stand', 'language': 'en'},
        ]
        for sample in samples:
            response = self.client.post(
                '/api/submit',
                json={
                    'language': sample['language'],
                    'district': self.valid_district,
                    'source': 'Web Form',
                    'text': sample['text'],
                },
            )
            self.assertEqual(response.status_code, 200)

        recompute = self.client.post(
            '/api/v1/demand/clusters/recompute',
            headers=self._auth(self.admin_token),
            json={'limit': 200},
        )
        self.assertEqual(recompute.status_code, 200)
        rpayload = recompute.get_json()
        self.assertTrue(rpayload['success'])
        self.assertGreaterEqual(int(rpayload.get('clusters', 0)), 1)

        list_resp = self.client.get('/api/v1/demand/clusters', headers=self._auth(self.auditor_token), query_string={'limit': 50})
        self.assertEqual(list_resp.status_code, 200)
        payload = list_resp.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('items', payload)
        self.assertGreaterEqual(len(payload['items']), 1)
        first = payload['items'][0]
        self.assertIn('cluster_id', first)
        self.assertIn('member_count', first)
        self.assertIn('quality', first)
        self.assertIn('avg_similarity', first['quality'])


    def test_incremental_cluster_assignment_at_ingest(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM cluster_members')
            db.execute('DELETE FROM demand_clusters')
            db.commit()

        first = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'No water supply in ward 12 for two days',
            },
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.get_json()
        self.assertIn('cluster', first_payload)
        self.assertIn('cluster_id', first_payload['cluster'])

        second = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'No water supply in ward 12 for two days',
            },
        )
        self.assertEqual(second.status_code, 200)
        second_payload = second.get_json()
        self.assertIn('cluster', second_payload)
        self.assertIn('cluster_id', second_payload['cluster'])

        with self.app.app_context():
            db = get_db()
            member_rows = db.execute(
                'SELECT request_id, cluster_id FROM cluster_members WHERE request_id IN (?, ?)',
                (first_payload['request_id'], second_payload['request_id']),
            ).fetchall()
            self.assertEqual(len(member_rows), 2)
            cluster_ids = {str(row['cluster_id']) for row in member_rows}
            self.assertEqual(len(cluster_ids), 1)

            cluster_row = db.execute(
                'SELECT member_count FROM demand_clusters WHERE cluster_id = ? LIMIT 1',
                (next(iter(cluster_ids)),),
            ).fetchone()
            self.assertIsNotNone(cluster_row)
            self.assertGreaterEqual(int(cluster_row['member_count'] or 0), 2)

        recompute = self.client.post(
            '/api/v1/demand/clusters/recompute',
            headers=self._auth(self.admin_token),
            json={'limit': 200},
        )
        self.assertEqual(recompute.status_code, 200)
        recompute_payload = recompute.get_json()
        self.assertTrue(recompute_payload['success'])

    def test_incremental_cluster_cross_lingual_normalization(self):
        first = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'pani supply issue in ward 7',
            },
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.get_json()

        second = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'water supply issue in ward 7',
            },
        )
        self.assertEqual(second.status_code, 200)
        second_payload = second.get_json()

        self.assertEqual(first_payload['cluster']['cluster_id'], second_payload['cluster']['cluster_id'])

    def test_incremental_cluster_honors_similarity_threshold(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM cluster_members')
            db.execute('DELETE FROM demand_clusters')
            db.commit()

        original_threshold = self.app.config.get('CLUSTER_SIMILARITY_THRESHOLD')
        original_min_shared = self.app.config.get('CLUSTER_MIN_SHARED_TOKENS')
        self.app.config.update(CLUSTER_SIMILARITY_THRESHOLD=0.9, CLUSTER_MIN_SHARED_TOKENS=2)
        try:
            first = self.client.post(
                '/api/submit',
                json={
                    'language': 'en',
                    'district': self.valid_district,
                    'source': 'Web Form',
                    'text': 'water leakage in ward 3',
                },
            )
            self.assertEqual(first.status_code, 200)
            first_payload = first.get_json()

            second = self.client.post(
                '/api/submit',
                json={
                    'language': 'en',
                    'district': self.valid_district,
                    'source': 'Web Form',
                    'text': 'water issue near ward 3',
                },
            )
            self.assertEqual(second.status_code, 200)
            second_payload = second.get_json()

            self.assertNotEqual(first_payload['cluster']['cluster_id'], second_payload['cluster']['cluster_id'])
        finally:
            self.app.config['CLUSTER_SIMILARITY_THRESHOLD'] = original_threshold
            self.app.config['CLUSTER_MIN_SHARED_TOKENS'] = original_min_shared

    def test_incremental_cluster_deterministic_tie_break(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM cluster_members')
            db.execute('DELETE FROM demand_clusters')
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

            base_metadata = json.dumps({'cluster_key': 'Water Supply|Tamil Nadu|supply-water-ward', 'token_examples': ['water', 'supply', 'ward']})
            db.execute(
                '''
                INSERT INTO demand_clusters (
                    cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('CL-000100', 'Tamil Nadu', 'Water Supply', 'water supply ward issue', 3, 'seed-1', base_metadata, now, now),
            )
            db.execute(
                '''
                INSERT INTO demand_clusters (
                    cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('CL-000050', 'Tamil Nadu', 'Water Supply', 'water supply ward complaint', 3, 'seed-2', base_metadata, now, now),
            )
            db.commit()

            result = assign_request_to_cluster(
                request_id='VB-TIE-BREAK-001',
                state='Tamil Nadu',
                category='Water Supply',
                text='water supply problem in ward',
            )
            self.assertEqual(result['cluster_id'], 'CL-000050')

    def test_demand_cluster_merge_and_split_contract(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM cluster_members')
            db.execute('DELETE FROM demand_clusters')
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            meta = json.dumps({'cluster_key': 'Water Supply|Tamil Nadu|water-supply-ward', 'token_examples': ['water', 'supply', 'ward']})

            db.execute(
                '''
                INSERT INTO demand_clusters (
                    cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('CL-MERGE-001', 'Tamil Nadu', 'Water Supply', 'water supply issue ward 1', 2, 'REQ-1', meta, now, now),
            )
            db.execute(
                '''
                INSERT INTO demand_clusters (
                    cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('CL-MERGE-002', 'Tamil Nadu', 'Water Supply', 'water supply issue ward 2', 1, 'REQ-3', meta, now, now),
            )
            db.execute('INSERT INTO cluster_members (request_id, cluster_id, similarity_score, created_at) VALUES (?, ?, ?, ?)', ('REQ-1', 'CL-MERGE-001', 1.0, now))
            db.execute('INSERT INTO cluster_members (request_id, cluster_id, similarity_score, created_at) VALUES (?, ?, ?, ?)', ('REQ-2', 'CL-MERGE-001', 0.8, now))
            db.execute('INSERT INTO cluster_members (request_id, cluster_id, similarity_score, created_at) VALUES (?, ?, ?, ?)', ('REQ-3', 'CL-MERGE-002', 0.9, now))
            db.commit()

        merge_resp = self.client.post(
            '/api/v1/demand/clusters/merge',
            headers=self._auth(self.admin_token),
            json={'source_cluster_id': 'CL-MERGE-002', 'target_cluster_id': 'CL-MERGE-001'},
        )
        self.assertEqual(merge_resp.status_code, 200)
        merge_payload = merge_resp.get_json()
        self.assertTrue(merge_payload['success'])
        self.assertEqual(merge_payload['moved_members'], 1)

        with self.app.app_context():
            db = get_db()
            moved_row = db.execute('SELECT cluster_id FROM cluster_members WHERE request_id = ? LIMIT 1', ('REQ-3',)).fetchone()
            self.assertIsNotNone(moved_row)
            self.assertEqual(str(moved_row['cluster_id']), 'CL-MERGE-001')

        split_resp = self.client.post(
            '/api/v1/demand/clusters/split',
            headers=self._auth(self.admin_token),
            json={'source_cluster_id': 'CL-MERGE-001', 'request_ids': ['REQ-2'], 'new_cluster_id': 'CL-SPLIT-001'},
        )
        self.assertEqual(split_resp.status_code, 200)
        split_payload = split_resp.get_json()
        self.assertTrue(split_payload['success'])
        self.assertEqual(split_payload['new_cluster_id'], 'CL-SPLIT-001')
        self.assertEqual(split_payload['moved_members'], 1)

        with self.app.app_context():
            db = get_db()
            split_member = db.execute('SELECT cluster_id FROM cluster_members WHERE request_id = ? LIMIT 1', ('REQ-2',)).fetchone()
            self.assertIsNotNone(split_member)
            self.assertEqual(str(split_member['cluster_id']), 'CL-SPLIT-001')

    def test_low_confidence_triage_and_rescore_contract(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM cluster_members')
            db.execute('DELETE FROM demand_clusters')
            db.execute('DELETE FROM citizen_requests WHERE request_id = ?', ('REQ-LC-1',))
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            meta = json.dumps({'cluster_key': 'Water Supply|Tamil Nadu|pipe-repair', 'token_examples': ['pipe', 'repair']})
            db.execute(
                '''
                INSERT INTO demand_clusters (
                    cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('CL-LOWCONF-001', 'Tamil Nadu', 'Water Supply', 'pipe repair needed', 1, 'REQ-LC-1', meta, now, now),
            )
            db.execute(
                'INSERT INTO cluster_members (request_id, cluster_id, similarity_score, created_at) VALUES (?, ?, ?, ?)',
                ('REQ-LC-1', 'CL-LOWCONF-001', 0.21, now),
            )
            db.execute(
                '''
                INSERT INTO citizen_requests (
                    request_id, source_channel, input_language, district, state, lat, lng,
                    original_text, translated_text, category, urgency, sentiment, status,
                    submitted_by, ward, service_type, routed_department,
                    sla_due_at, sla_breached_at, sla_escalation_level, ai_metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'REQ-LC-1',
                    'Web Form',
                    'en',
                    self.valid_district,
                    'Tamil Nadu',
                    13.0,
                    80.0,
                    'major pipe burst and water leakage',
                    'major pipe burst and water leakage',
                    'Water Supply',
                    'Urgent',
                    'Negative',
                    'New',
                    'tester',
                    '7',
                    'water',
                    'Water Board',
                    None,
                    None,
                    0,
                    '{}',
                    now,
                ),
            )
            db.commit()

        low_resp = self.client.get('/api/v1/demand/clusters/low-confidence', headers=self._auth(self.auditor_token), query_string={'limit': 50})
        self.assertEqual(low_resp.status_code, 200)
        low_payload = low_resp.get_json()
        self.assertTrue(low_payload['success'])
        self.assertIn('review_threshold', low_payload)
        self.assertGreaterEqual(len(low_payload['items']), 1)
        self.assertEqual(low_payload['items'][0]['request_id'], 'REQ-LC-1')
        self.assertIn('explanation', low_payload['items'][0])
        self.assertIn('top_overlap_tokens', low_payload['items'][0]['explanation'])

        override_resp = self.client.post(
            '/api/v1/demand/clusters/member-override',
            headers=self._auth(self.admin_token),
            json={
                'request_id': 'REQ-LC-1',
                'cluster_id': 'CL-LOWCONF-001',
                'decision': 'approved',
                'reason': 'validated by analyst review',
            },
        )
        self.assertEqual(override_resp.status_code, 200)
        override_payload = override_resp.get_json()
        self.assertTrue(override_payload['success'])
        self.assertEqual(override_payload['override']['status'], 'approved')

        low_after_override = self.client.get('/api/v1/demand/clusters/low-confidence', headers=self._auth(self.auditor_token), query_string={'limit': 50})
        self.assertEqual(low_after_override.status_code, 200)
        low_after_items = low_after_override.get_json()['items']
        row = next(item for item in low_after_items if item['request_id'] == 'REQ-LC-1')
        self.assertFalse(bool(row['is_low_confidence']))
        self.assertIsNotNone(row['override'])
        self.assertEqual(row['override']['status'], 'approved')

        rescore_resp = self.client.post('/api/v1/demand/clusters/rescore-low-confidence', headers=self._auth(self.admin_token), json={'limit': 50})
        self.assertEqual(rescore_resp.status_code, 200)
        rescore_payload = rescore_resp.get_json()
        self.assertTrue(rescore_payload['success'])
        self.assertGreaterEqual(int(rescore_payload['rescored']), 1)

    def test_override_metrics_and_export_contract(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM cluster_member_overrides')
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            old_ts = '2001-01-01T00:00:00Z'
            db.execute(
                '''
                INSERT INTO cluster_member_overrides (request_id, cluster_id, status, decision, reason, actor, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('REQ-MET-1', 'CL-MET-1', 'approved', 'approved', 'manual review ok', 'Analyst User', old_ts, old_ts),
            )
            db.execute(
                '''
                INSERT INTO cluster_member_overrides (request_id, cluster_id, status, decision, reason, actor, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('REQ-MET-2', 'CL-MET-2', 'superseded', 'superseded', 'auto stale sweep', 'system', now, now),
            )
            db.commit()

        metrics_resp = self.client.get('/api/v1/demand/clusters/member-overrides/metrics', headers=self._auth(self.auditor_token), query_string={'limit': 100})
        self.assertEqual(metrics_resp.status_code, 200)
        metrics_payload = metrics_resp.get_json()
        self.assertTrue(metrics_payload['success'])
        self.assertIn('metrics', metrics_payload)
        self.assertIn('by_status', metrics_payload['metrics'])
        self.assertIn('by_actor', metrics_payload['metrics'])
        self.assertIn('transition_funnel', metrics_payload['metrics'])

        export_json = self.client.get(
            '/api/v1/demand/clusters/member-overrides/history/export',
            headers=self._auth(self.auditor_token),
            query_string={'status': 'approved', 'limit': 100, 'format': 'json'},
        )
        self.assertEqual(export_json.status_code, 200)
        export_payload = export_json.get_json()
        self.assertTrue(export_payload['success'])
        self.assertGreaterEqual(len(export_payload['items']), 1)
        self.assertEqual(export_payload['items'][0]['status'], 'approved')

        export_csv = self.client.get(
            '/api/v1/demand/clusters/member-overrides/history/export',
            headers=self._auth(self.auditor_token),
            query_string={'limit': 100, 'format': 'csv'},
        )
        self.assertEqual(export_csv.status_code, 200)
        self.assertIn('text/csv', export_csv.content_type)

    def test_override_alerts_and_backlog_summary_contract(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM cluster_member_overrides')
            now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            old_ts = '2000-01-01T00:00:00Z'
            for idx in range(0, 3):
                db.execute(
                    '''
                    INSERT INTO cluster_member_overrides (request_id, cluster_id, status, decision, reason, actor, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (f'REQ-AL-{idx}', 'CL-AL-1', 'rejected', 'rejected', 'manual reject', 'Analyst User', now, now),
                )
            db.execute(
                '''
                INSERT INTO cluster_member_overrides (request_id, cluster_id, status, decision, reason, actor, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('REQ-AL-PEND', 'CL-AL-2', 'pending', 'pending', 'awaiting review', 'Analyst User', old_ts, old_ts),
            )
            db.commit()

        alerts_resp = self.client.get(
            '/api/v1/demand/clusters/member-overrides/alerts',
            headers=self._auth(self.auditor_token),
            query_string={'recent_hours': 48, 'rejected_spike_threshold': 2, 'superseded_spike_threshold': 10},
        )
        self.assertEqual(alerts_resp.status_code, 200)
        alerts_payload = alerts_resp.get_json()
        self.assertTrue(alerts_payload['success'])
        self.assertIn('alerts', alerts_payload)
        self.assertTrue(any(a.get('type') == 'rejected_spike' for a in alerts_payload['alerts']))

        alerts_notify_resp = self.client.get(
            '/api/v1/demand/clusters/member-overrides/alerts',
            headers=self._auth(self.admin_token),
            query_string={'recent_hours': 48, 'rejected_spike_threshold': 2, 'notify': 'true'},
        )
        self.assertEqual(alerts_notify_resp.status_code, 200)
        alerts_notify_payload = alerts_notify_resp.get_json()
        self.assertTrue(alerts_notify_payload['notify'])
        self.assertGreaterEqual(len(alerts_notify_payload.get('notifications') or []), 1)

        with self.app.app_context():
            db = get_db()
            nrow = db.execute("SELECT COUNT(*) AS c FROM notification_deliveries WHERE stage = 'override_alert'").fetchone()
            self.assertGreaterEqual(int(nrow['c'] or 0), 1)

        backlog_resp = self.client.get('/api/v1/demand/clusters/member-overrides/backlog-summary', headers=self._auth(self.auditor_token))
        self.assertEqual(backlog_resp.status_code, 200)
        backlog_payload = backlog_resp.get_json()
        self.assertTrue(backlog_payload['success'])
        self.assertIn('aging_buckets', backlog_payload)
        self.assertGreaterEqual(int(backlog_payload.get('pending_total') or 0), 1)

    def test_policy_alignment_map_and_rag_brief_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={'language': 'en', 'district': self.valid_district, 'source': 'Web Form', 'text': 'Need drainage and water pipeline rehabilitation'},
        )
        self.assertEqual(submit.status_code, 200)

        alignment = self.client.get('/api/v1/policy/alignment-map', headers=self._auth(self.analyst_token), query_string={'limit': 20})
        self.assertEqual(alignment.status_code, 200)
        ap = alignment.get_json()
        self.assertTrue(ap['success'])
        self.assertIn('items', ap)
        self.assertIn('meta', ap)
        if ap.get('items'):
            first = ap['items'][0]
            self.assertIn('district', first)
            self.assertIn('lat', first)
            self.assertIn('lng', first)
            self.assertIn('alignment_gap', first)
            self.assertIn('score_explainability', first)

        rag = self.client.get(
            '/api/v1/policy/rag-brief',
            headers=self._auth(self.auditor_token),
            query_string={'query': 'water supply investment gap', 'district': self.valid_district, 'limit': 3},
        )
        self.assertEqual(rag.status_code, 200)
        rp = rag.get_json()
        self.assertTrue(rp['success'])
        self.assertIn('summary', rp)
        self.assertIn('citation_chain', rp)
        chain = rp.get('citation_chain') or {}
        self.assertIn('sources', chain)
        self.assertIsInstance(chain.get('sources'), list)
        self.assertGreaterEqual(len(chain.get('sources') or []), 1)
        cite = chain['sources'][0]
        self.assertIn('source_id', cite)
        self.assertIn('title', cite)
        self.assertIn('score', cite)
        self.assertIn('excerpt', cite)

    def test_layer4_fusion_sources_and_join_contract(self):
        base = os.path.join(os.getcwd(), 'tests')
        secc_path = os.path.join(base, 'l4_secc_sample.json')
        census_path = os.path.join(base, 'l4_census_sample.csv')
        nfhs_path = os.path.join(base, 'l4_nfhs_sample.json')
        sdg_path = os.path.join(base, 'l4_sdg_sample.json')
        mpi_path = os.path.join(base, 'l4_mpi_sample.json')
        aspirational_path = os.path.join(base, 'l4_aspirational_sample.json')
        gati_path = os.path.join(base, 'l4_gati_sample.json')
        budget_path = os.path.join(base, 'l4_budget_sample.json')

        sample_district = self.valid_district

        with open(secc_path, 'w', encoding='utf-8') as f:
            json.dump([{'district': sample_district, 'state': 'SampleState', 'secc_household_deprivation_index': 0.41}], f)
        with open(census_path, 'w', encoding='utf-8') as f:
            f.write('district,state,population\n')
            f.write(f'{sample_district},SampleState,1200000\n')
        with open(nfhs_path, 'w', encoding='utf-8') as f:
            json.dump([{'district': sample_district, 'state': 'SampleState', 'nfhs_health_access_score': 61.2}], f)
        with open(sdg_path, 'w', encoding='utf-8') as f:
            json.dump([{'district': sample_district, 'state': 'SampleState', 'sdg_index_score': 58.4}], f)
        with open(mpi_path, 'w', encoding='utf-8') as f:
            json.dump([{'district': sample_district, 'state': 'SampleState', 'mpi_score': 0.24}], f)
        with open(aspirational_path, 'w', encoding='utf-8') as f:
            json.dump([{'district': sample_district, 'state': 'SampleState', 'is_aspirational': True}], f)
        with open(gati_path, 'w', encoding='utf-8') as f:
            json.dump([{'district': sample_district, 'state': 'SampleState', 'infrastructure_gap_score': 0.52}], f)
        with open(budget_path, 'w', encoding='utf-8') as f:
            json.dump([{'district': sample_district, 'state': 'SampleState', 'total_outlay_lakh': 12500.75}], f)

        self.app.config.update(
            L4_SECC_DATA_PATH=secc_path,
            L4_CENSUS_DATA_PATH=census_path,
            L4_NFHS_DATA_PATH=nfhs_path,
            L4_SDG_DATA_PATH=sdg_path,
            L4_NITI_MPI_DATA_PATH=mpi_path,
            L4_ASPIRATIONAL_DATA_PATH=aspirational_path,
            L4_GATI_SHAKTI_DATA_PATH=gati_path,
            L4_BUDGET_OUTLAY_DATA_PATH=budget_path,
        )

        submit = self.client.post(
            '/api/submit',
            json={'language': 'en', 'district': sample_district, 'source': 'Web Form', 'text': 'Need pipeline repair'},
        )
        self.assertEqual(submit.status_code, 200)

        sources = self.client.get('/api/v1/layer4/fusion/sources', headers=self._auth(self.auditor_token))
        self.assertEqual(sources.status_code, 200)
        sources_payload = sources.get_json()
        self.assertTrue(sources_payload['success'])
        self.assertGreaterEqual(int(sources_payload.get('configured_sources') or 0), 8)

        fusion = self.client.get('/api/v1/layer4/fusion', headers=self._auth(self.analyst_token), query_string={'limit': 20})
        self.assertEqual(fusion.status_code, 200)
        fp = fusion.get_json()
        self.assertTrue(fp['success'])
        self.assertIn('items', fp)
        self.assertIn('meta', fp)
        self.assertGreaterEqual(len(fp.get('items') or []), 1)

        first = (fp.get('items') or [])[0]
        self.assertIn('fusion', first)
        self.assertIn('fusion_coverage', first)
        self.assertIn('secc', first['fusion'])
        self.assertIn('census', first['fusion'])
        self.assertIn('nfhs', first['fusion'])
        self.assertIn('sdg_india_index', first['fusion'])
        self.assertIn('niti_mpi', first['fusion'])
        self.assertIn('aspirational_districts', first['fusion'])
        self.assertIn('pm_gati_shakti', first['fusion'])
        self.assertIn('budget_outlays', first['fusion'])

        secc_prov = first['fusion']['secc'].get('data_provenance') or {}
        self.assertIn('mode', secc_prov)
        self.assertIn('source_name', secc_prov)
        self.assertIn('publisher', secc_prov)
        self.assertIn('is_synthetic_demo_fallback', secc_prov)

    def test_federation_publish_batch_and_schema_contract(self):
        with self.app.app_context():
            db = get_db()
            db.execute("DELETE FROM federation_external_events WHERE external_event_id = 'FED-001'")
            db.commit()

        schema_resp = self.client.get('/api/v1/federation/schema', headers=self._auth(self.auditor_token))
        self.assertEqual(schema_resp.status_code, 200)
        schema_payload = schema_resp.get_json()
        self.assertTrue(schema_payload['success'])
        self.assertIn('schema', schema_payload)

        publish_body = {
            'source': 'state-demand-grid',
            'external_event_id': 'FED-001',
            'language': 'en',
            'district': self.valid_district,
            'text': 'Federation test water shortage in ward 9',
            'sender': 'federation-node',
        }
        first = self.client.post('/api/v1/federation/publish', headers=self._auth(self.admin_token), json=publish_body)
        self.assertEqual(first.status_code, 200)
        first_payload = first.get_json()
        self.assertTrue(first_payload['success'])
        self.assertFalse(bool(first_payload.get('federation_reused')))
        self.assertTrue((first_payload.get('request_id') or '').strip())

        second = self.client.post('/api/v1/federation/publish', headers=self._auth(self.admin_token), json=publish_body)
        self.assertEqual(second.status_code, 200)
        second_payload = second.get_json()
        self.assertTrue(second_payload['success'])
        self.assertTrue(bool(second_payload.get('federation_reused')))
        self.assertEqual(first_payload['request_id'], second_payload['request_id'])

        batch = self.client.post(
            '/api/v1/federation/batch-publish',
            headers=self._auth(self.admin_token),
            json={
                'items': [
                    {
                        'source': 'state-demand-grid',
                        'external_event_id': 'FED-002',
                        'language': 'en',
                        'district': self.valid_district,
                        'text': 'Federation batch pothole issue',
                    },
                    {
                        'source': 'state-demand-grid',
                        'external_event_id': 'FED-001',
                        'language': 'en',
                        'district': self.valid_district,
                        'text': 'duplicate should reuse',
                    },
                ]
            },
        )
        self.assertEqual(batch.status_code, 200)
        batch_payload = batch.get_json()
        self.assertTrue(batch_payload['success'])
        self.assertGreaterEqual(int(batch_payload.get('accepted') or 0), 1)
        self.assertGreaterEqual(int(batch_payload.get('reused') or 0), 1)

        with self.app.app_context():
            db = get_db()
            row = db.execute(
                'SELECT COUNT(*) AS c FROM federation_external_events WHERE source = ? AND external_event_id = ?',
                ('state-demand-grid', 'FED-001'),
            ).fetchone()
            self.assertEqual(int(row['c'] or 0), 1)

    def test_cosign_engine_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Need immediate road repair on main street',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        status_resp = self.client.get(f'/api/v1/requests/{request_id}/cosign-status')
        self.assertEqual(status_resp.status_code, 200)
        status_payload = status_resp.get_json()
        self.assertTrue(status_payload['success'])
        self.assertIn('token', status_payload)
        token = status_payload['token']['value']

        cosign_resp = self.client.post(
            f'/api/v1/requests/{request_id}/cosign',
            json={
                'token': token,
                'supporter_ref': 'citizen-001',
                'supporter_name': 'Citizen One',
                'channel': 'web',
            },
        )
        self.assertEqual(cosign_resp.status_code, 200)
        cosign_payload = cosign_resp.get_json()
        self.assertTrue(cosign_payload['success'])
        self.assertGreaterEqual(int(cosign_payload['verified_support_count'] or 0), 1)

        status_after = self.client.get(f'/api/v1/requests/{request_id}/cosign-status')
        self.assertEqual(status_after.status_code, 200)
        status_after_payload = status_after.get_json()
        self.assertGreaterEqual(int(status_after_payload.get('verified_support_count') or 0), 1)

    def test_request_timeline_and_lifecycle_update_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Need drainage repair urgently',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        update = self.client.post(
            f'/api/v1/requests/{request_id}/lifecycle/update',
            headers=self._auth(self.admin_token),
            json={'action': 'in_progress', 'reason': 'assigned to municipal team'},
        )
        self.assertEqual(update.status_code, 200)
        timeline_payload = update.get_json()
        self.assertTrue(timeline_payload['success'])
        self.assertIn('timeline', timeline_payload)

        public_timeline = self.client.get(f'/api/requests/{request_id}/timeline')
        self.assertEqual(public_timeline.status_code, 200)
        self.assertTrue(public_timeline.get_json()['success'])

    def test_closure_feedback_reopen_trigger_contract(self):
        submit = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Street light still not fixed',
            },
        )
        self.assertEqual(submit.status_code, 200)
        request_id = submit.get_json()['request_id']

        feedback = self.client.post(
            f'/api/requests/{request_id}/closure-feedback',
            json={'rating': 1, 'feedback_text': 'Issue unresolved'},
        )
        self.assertEqual(feedback.status_code, 200)
        payload = feedback.get_json()
        self.assertTrue(payload['success'])
        self.assertTrue(payload['reopen_triggered'])

        timeline = self.client.get(f'/api/requests/{request_id}/timeline')
        events = timeline.get_json()['timeline']
        self.assertTrue(any(e.get('event_type') == 'reopened_by_feedback' for e in events))
    def test_dialogflow_session_contract(self):
        response = self.client.post(
            '/api/dialogflow/session',
            json={
                'session_state': 'collect_issue',
                'message': 'There is a severe road damage near the bus stand',
                'language': 'en',
                'channel': 'web',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('session', payload)
        session = payload['session']
        self.assertIn('intent', session)
        self.assertIn('session_state', session)
        self.assertIn('next_prompt', session)

    def test_dialogflow_session_requires_message(self):
        response = self.client.post('/api/dialogflow/session', json={'session_state': 'collect_issue'})
        self.assertEqual(response.status_code, 400)

    def test_ai_status_lists_dialogflow_feature(self):
        response = self.client.get('/api/ai/status')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('dialogflow_cx_guided_conversation', payload.get('supported_features', []))


    def test_deployment_profile_contract(self):
        response = self.client.get('/api/deployment/profile')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertTrue(payload['success'])
        self.assertIn('deployment_profile', payload)
        self.assertIn('profile_mode', payload)
        self.assertIn('pilot_scope_enforced', payload)
        self.assertIn('languages', payload)
        self.assertIn('language_codes', payload)
        self.assertIn('states', payload)
        self.assertIn('districts', payload)
        self.assertIn('state_to_districts_scope', payload)
        self.assertIn('counts', payload)

        counts = payload['counts']
        self.assertIn('languages', counts)
        self.assertIn('states', counts)
        self.assertIn('districts', counts)

        self.assertIsInstance(payload['states'], list)
        self.assertIsInstance(payload['districts'], list)
        self.assertIsInstance(payload['languages'], dict)

class TestAuthAndRbac(BaseApiTestCase):
    def test_auth_me_requires_token(self):
        response = self.client.get('/api/v1/auth/me')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['error'], 'Missing bearer token')

    def test_auth_me_rejects_invalid_token(self):
        response = self.client.get('/api/v1/auth/me', headers=self._auth('invalid-token'))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['error'], 'Invalid token')

    def test_analyst_role_matrix(self):
        requests_response = self.client.get('/api/v1/requests', headers=self._auth(self.analyst_token))
        self.assertEqual(requests_response.status_code, 200)

        users_response = self.client.get('/api/v1/users', headers=self._auth(self.analyst_token))
        self.assertEqual(users_response.status_code, 403)
        self.assertEqual(users_response.get_json()['error'], 'Forbidden for this role')

        audit_response = self.client.get('/api/v1/audit-logs', headers=self._auth(self.analyst_token))
        self.assertEqual(audit_response.status_code, 403)

    def test_auditor_and_admin_authorization(self):
        auditor_audit_logs = self.client.get('/api/v1/audit-logs', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_audit_logs.status_code, 200)

        admin_users = self.client.get('/api/v1/users', headers=self._auth(self.admin_token))
        self.assertEqual(admin_users.status_code, 200)


    


    def test_demand_cluster_endpoint_rbac(self):
        analyst_recompute = self.client.post('/api/v1/demand/clusters/recompute', headers=self._auth(self.analyst_token), json={'limit': 50})
        self.assertEqual(analyst_recompute.status_code, 200)

        analyst_list = self.client.get('/api/v1/demand/clusters', headers=self._auth(self.analyst_token))
        self.assertEqual(analyst_list.status_code, 200)

        auditor_recompute = self.client.post('/api/v1/demand/clusters/recompute', headers=self._auth(self.auditor_token), json={'limit': 50})
        self.assertEqual(auditor_recompute.status_code, 403)

        analyst_merge = self.client.post('/api/v1/demand/clusters/merge', headers=self._auth(self.analyst_token), json={'source_cluster_id': 'A', 'target_cluster_id': 'B'})
        self.assertIn(analyst_merge.status_code, (200, 400))

        auditor_merge = self.client.post('/api/v1/demand/clusters/merge', headers=self._auth(self.auditor_token), json={'source_cluster_id': 'A', 'target_cluster_id': 'B'})
        self.assertEqual(auditor_merge.status_code, 403)

        analyst_split = self.client.post('/api/v1/demand/clusters/split', headers=self._auth(self.analyst_token), json={'source_cluster_id': 'A', 'request_ids': ['REQ-1']})
        self.assertIn(analyst_split.status_code, (200, 400))

        auditor_split = self.client.post('/api/v1/demand/clusters/split', headers=self._auth(self.auditor_token), json={'source_cluster_id': 'A', 'request_ids': ['REQ-1']})
        self.assertEqual(auditor_split.status_code, 403)

        analyst_rescore = self.client.post('/api/v1/demand/clusters/rescore-low-confidence', headers=self._auth(self.analyst_token), json={'limit': 25})
        self.assertIn(analyst_rescore.status_code, (200, 400))

        auditor_rescore = self.client.post('/api/v1/demand/clusters/rescore-low-confidence', headers=self._auth(self.auditor_token), json={'limit': 25})
        self.assertEqual(auditor_rescore.status_code, 403)

        auditor_low = self.client.get('/api/v1/demand/clusters/low-confidence', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_low.status_code, 200)

        analyst_override = self.client.post('/api/v1/demand/clusters/member-override', headers=self._auth(self.analyst_token), json={'request_id': 'REQ-1', 'cluster_id': 'CL-1', 'decision': 'approved', 'reason': 'ok'})
        self.assertIn(analyst_override.status_code, (200, 400))

        auditor_override = self.client.post('/api/v1/demand/clusters/member-override', headers=self._auth(self.auditor_token), json={'request_id': 'REQ-1', 'cluster_id': 'CL-1', 'decision': 'approved', 'reason': 'ok'})
        self.assertEqual(auditor_override.status_code, 403)

        auditor_history = self.client.get('/api/v1/demand/clusters/member-overrides/history', headers=self._auth(self.auditor_token), query_string={'request_id': 'REQ-1'})
        self.assertIn(auditor_history.status_code, (200, 400))

        analyst_sweep = self.client.post('/api/v1/demand/clusters/member-overrides/sweep-stale', headers=self._auth(self.analyst_token), json={'stale_hours': 24, 'limit': 20})
        self.assertIn(analyst_sweep.status_code, (200, 400))

        auditor_sweep = self.client.post('/api/v1/demand/clusters/member-overrides/sweep-stale', headers=self._auth(self.auditor_token), json={'stale_hours': 24, 'limit': 20})
        self.assertEqual(auditor_sweep.status_code, 403)

        auditor_metrics = self.client.get('/api/v1/demand/clusters/member-overrides/metrics', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_metrics.status_code, 200)

        auditor_export = self.client.get('/api/v1/demand/clusters/member-overrides/history/export', headers=self._auth(self.auditor_token), query_string={'limit': 20})
        self.assertEqual(auditor_export.status_code, 200)

        auditor_alerts = self.client.get('/api/v1/demand/clusters/member-overrides/alerts', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_alerts.status_code, 200)

        auditor_backlog = self.client.get('/api/v1/demand/clusters/member-overrides/backlog-summary', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_backlog.status_code, 200)

        analyst_fed_publish = self.client.post('/api/v1/federation/publish', headers=self._auth(self.analyst_token), json={'source': 'rbac-test', 'external_event_id': 'rbac-1', 'district': self.valid_district, 'text': 'rbac'})
        self.assertIn(analyst_fed_publish.status_code, (200, 400))

        auditor_fed_publish = self.client.post('/api/v1/federation/publish', headers=self._auth(self.auditor_token), json={'source': 'rbac-test', 'external_event_id': 'rbac-2', 'district': self.valid_district, 'text': 'rbac'})
        self.assertEqual(auditor_fed_publish.status_code, 403)

        auditor_fed_schema = self.client.get('/api/v1/federation/schema', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_fed_schema.status_code, 200)

    def test_policy_scoring_weights_api_contract_and_rbac(self):
        auditor_get = self.client.get('/api/v1/policy/scoring-weights', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_get.status_code, 200)
        auditor_payload = auditor_get.get_json()
        self.assertTrue(auditor_payload['success'])
        self.assertIn('weights', auditor_payload)
        self.assertIn('profile', auditor_payload)

        analyst_put = self.client.put(
            '/api/v1/policy/scoring-weights',
            headers=self._auth(self.analyst_token),
            json={'profile': 'default', 'change_reason': 'rbac-analyst-try', 'weights': {'w1_demand_density': 0.3}},
        )
        self.assertEqual(analyst_put.status_code, 403)

        missing_reason = self.client.put(
            '/api/v1/policy/scoring-weights',
            headers=self._auth(self.admin_token),
            json={'profile': 'default', 'weights': {'w1_demand_density': 0.3}},
        )
        self.assertEqual(missing_reason.status_code, 400)

        admin_put = self.client.put(
            '/api/v1/policy/scoring-weights',
            headers=self._auth(self.admin_token),
            json={
                'profile': 'default',
                'change_reason': 'optimize demand density weight',
                'weights': {
                    'w1_demand_density': 0.25,
                    'w2_demand_velocity': 0.15,
                    'w3_deprivation': 0.15,
                    'w4_infrastructure_gap': 0.15,
                    'w5_investment_already_made': 0.10,
                    'w6_relative_cost': 0.10,
                    'w7_equity_mandate': 0.10,
                },
            },
        )
        self.assertEqual(admin_put.status_code, 200)
        admin_payload = admin_put.get_json()
        self.assertTrue(admin_payload['success'])
        self.assertEqual(admin_payload['weights']['w1_demand_density'], 0.25)
        self.assertEqual(admin_payload['updated_by'], 'Platform Admin')

        invalid_put = self.client.put(
            '/api/v1/policy/scoring-weights',
            headers=self._auth(self.admin_token),
            json={'profile': 'default', 'change_reason': 'invalid negative weight', 'weights': {'w1_demand_density': -1}},
        )
        self.assertEqual(invalid_put.status_code, 400)

        create_equity = self.client.put(
            '/api/v1/policy/scoring-weights',
            headers=self._auth(self.admin_token),
            json={
                'profile': 'equity_first',
                'change_reason': 'create equity profile',
                'weights': {
                    'w1_demand_density': 0.16,
                    'w2_demand_velocity': 0.14,
                    'w3_deprivation': 0.18,
                    'w4_infrastructure_gap': 0.12,
                    'w5_investment_already_made': 0.08,
                    'w6_relative_cost': 0.06,
                    'w7_equity_mandate': 0.26,
                },
            },
        )
        self.assertEqual(create_equity.status_code, 200)

        list_profiles = self.client.get('/api/v1/policy/scoring-profiles', headers=self._auth(self.auditor_token))
        self.assertEqual(list_profiles.status_code, 200)
        list_payload = list_profiles.get_json()
        self.assertTrue(list_payload['success'])
        self.assertIn('default', list_payload['profiles'])
        self.assertIn('equity_first', list_payload['profiles'])

        activate_by_analyst = self.client.post(
            '/api/v1/policy/scoring-profiles/activate',
            headers=self._auth(self.analyst_token),
            json={'profile': 'equity_first', 'change_reason': 'rbac denied'},
        )
        self.assertEqual(activate_by_analyst.status_code, 403)

        activate_missing_reason = self.client.post(
            '/api/v1/policy/scoring-profiles/activate',
            headers=self._auth(self.admin_token),
            json={'profile': 'equity_first'},
        )
        self.assertEqual(activate_missing_reason.status_code, 400)

        activate_ok = self.client.post(
            '/api/v1/policy/scoring-profiles/activate',
            headers=self._auth(self.admin_token),
            json={'profile': 'equity_first', 'change_reason': 'switch to equity profile'},
        )
        self.assertEqual(activate_ok.status_code, 200)
        self.assertEqual(activate_ok.get_json()['active']['active_profile'], 'equity_first')

        rollback_missing_reason = self.client.post(
            '/api/v1/policy/scoring-profiles/rollback',
            headers=self._auth(self.admin_token),
            json={},
        )
        self.assertEqual(rollback_missing_reason.status_code, 400)

        rollback_ok = self.client.post(
            '/api/v1/policy/scoring-profiles/rollback',
            headers=self._auth(self.admin_token),
            json={'change_reason': 'restore previous default'},
        )
        self.assertEqual(rollback_ok.status_code, 200)
        self.assertEqual(rollback_ok.get_json()['active']['active_profile'], 'default')

    def test_webhook_security_status_admin_only(self):
        self.app.config.update(
            WEBHOOK_SHARED_TOKEN='status-webhook-token',
            WHATSAPP_VERIFY_TOKEN='status-whatsapp-verify',
            META_APP_SECRET='status-meta-secret',
            TELEGRAM_WEBHOOK_SECRET='status-telegram-secret',
            TWILIO_AUTH_TOKEN='status-twilio-token',
            WEBHOOK_REQUIRE_REPLAY_PROTECTION=True,
            WEBHOOK_REPLAY_WINDOW_SECONDS=300,
            WEBHOOK_REPLAY_STORE='db',
            WEBHOOK_BIND_REPLAY_IN_SIGNATURE=True,
            WEBHOOK_ALLOWED_IPS='127.0.0.1/32,10.0.0.0/8',
            WEBHOOK_SECURITY_POLICY_VERSION='2026-09-v1',
        )

        analyst_resp = self.client.get('/api/security/webhook-status', headers=self._auth(self.analyst_token))
        self.assertEqual(analyst_resp.status_code, 403)

        admin_resp = self.client.get('/api/security/webhook-status', headers=self._auth(self.admin_token))
        self.assertEqual(admin_resp.status_code, 200)
        payload = admin_resp.get_json()

        self.assertTrue(payload['success'])
        sec = payload['webhook_security']
        self.assertTrue(sec['shared_token_required'])
        self.assertTrue(sec['meta_signature_enforced'])
        self.assertTrue(sec['telegram_secret_enforced'])
        self.assertTrue(sec['twilio_signature_enforced'])
        self.assertTrue(sec['replay_protection_enabled'])
        self.assertEqual(sec['replay_store'], 'db')
        self.assertTrue(sec['bind_replay_in_signature'])
        self.assertTrue(sec['source_ip_allowlist_enabled'])
        self.assertEqual(sec['source_ip_allowlist_count'], 2)
        self.assertIn('last_checked_at', sec)
        self.assertIn('policy_version', sec)
        self.assertEqual(sec['policy_version'], '2026-09-v1')
    def test_admin_can_rotate_user_token(self):
        create_response = self.client.post(
            '/api/v1/users',
            headers=self._auth(self.admin_token),
            json={
                'name': 'Rotate Target',
                'role': 'analyst',
                'api_token': 'rotate-target-token-001',
            },
        )
        self.assertEqual(create_response.status_code, 200)

        users_response = self.client.get('/api/v1/users', headers=self._auth(self.admin_token))
        users = users_response.get_json()['users']
        target_user = next(user for user in users if user['name'] == 'Rotate Target')

        rotate_response = self.client.post(
            f"/api/v1/users/{target_user['id']}/rotate-token",
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(rotate_response.status_code, 200)

        rotated_token = rotate_response.get_json()['api_token']
        auth_response = self.client.get('/api/v1/auth/me', headers=self._auth(rotated_token))
        self.assertEqual(auth_response.status_code, 200)
        self.assertEqual(auth_response.get_json()['user']['name'], 'Rotate Target')
    def test_admin_can_create_user(self):
        create_response = self.client.post(
            '/api/v1/users',
            headers=self._auth(self.admin_token),
            json={
                'name': 'Test Analyst',
                'role': 'analyst',
                'api_token': 'test-analyst-token-001',
            },
        )
        self.assertEqual(create_response.status_code, 200)

        list_response = self.client.get('/api/v1/users', headers=self._auth(self.admin_token))
        users = list_response.get_json()['users']
        self.assertTrue(any(user['name'] == 'Test Analyst' for user in users))


class TestPhase7AndGovernance(BaseApiTestCase):
    def test_geo_ward_suggest_contract(self):
        response = self.client.post('/api/v1/geo/ward-suggest', json={'district': self.valid_district, 'lat': 13.0827, 'lng': 80.2707})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('ward', payload)
        self.assertIn('ward_source', payload)

    def test_submit_records_consent_event(self):
        response = self.client.post(
            '/api/submit',
            json={
                'language': 'en',
                'district': self.valid_district,
                'source': 'Web Form',
                'text': 'Need drainage cleanup in market lane.',
                'consent_granted': True,
                'consent_scope': 'request_processing',
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn('consent', payload)
        self.assertTrue(payload['consent']['consent_granted'])



    def test_ivr_callback_webhook_reconciliation_idempotent(self):
        self.app.config.update(WEBHOOK_REQUIRE_REPLAY_PROTECTION=False, WEBHOOK_ALLOWED_IPS='', TWILIO_AUTH_TOKEN='')
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM ivr_callback_alert_events')
            db.execute('DELETE FROM ivr_callback_dead_letters')
            db.execute('DELETE FROM ivr_callback_jobs')
            db.commit()

        create = self.client.post(
            '/api/channels/ivr/missed-call',
            headers=headers,
            json={'phone': '919933330000', 'district': self.valid_district, 'language': 'en'},
        )
        self.assertEqual(create.status_code, 200)
        callback_id = create.get_json()['callback_id']

        fail_payload = {
            'provider': 'twilio',
            'callback_id': callback_id,
            'status': 'failed',
            'event_id': 'EVT-1',
        }
        first = self.client.post('/api/channels/ivr/callbacks/webhook', headers=headers, json=fail_payload)
        self.assertEqual(first.status_code, 200)
        fp = first.get_json()
        self.assertTrue(fp['success'])
        self.assertFalse(bool(fp.get('idempotent_reused', False)))

        duplicate = self.client.post('/api/channels/ivr/callbacks/webhook', headers=headers, json=fail_payload)
        self.assertEqual(duplicate.status_code, 200)
        dp = duplicate.get_json()
        self.assertTrue(dp['success'])
        self.assertTrue(bool(dp.get('idempotent_reused', False)))

        with self.app.app_context():
            db = get_db()
            row = db.execute('SELECT COUNT(*) AS c FROM ivr_callback_events WHERE callback_id = ?', (callback_id,)).fetchone()
            self.assertEqual(int(row['c'] or 0), 1)

        ok_payload = {
            'provider': 'twilio',
            'callback_id': callback_id,
            'status': 'completed',
            'event_id': 'EVT-2',
        }
        second = self.client.post('/api/channels/ivr/callbacks/webhook', headers=headers, json=ok_payload)
        self.assertEqual(second.status_code, 200)
        status = self.client.get(f'/api/channels/ivr/callbacks/{callback_id}', headers=self._auth(self.auditor_token))
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()['callback']['status'], 'connected')



    def test_ivr_callback_webhook_exotel_signature_verification(self):
        self.app.config.update(WEBHOOK_REQUIRE_REPLAY_PROTECTION=False, WEBHOOK_ALLOWED_IPS='', TWILIO_AUTH_TOKEN='', EXOTEL_WEBHOOK_SECRET='exotel-secret')
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        create = self.client.post('/api/channels/ivr/missed-call', headers=headers, json={'phone': '919977770000', 'district': self.valid_district, 'language': 'en'})
        self.assertEqual(create.status_code, 200)
        callback_id = create.get_json()['callback_id']

        payload = {
            'provider': 'exotel',
            'Call': {'Sid': 'EXO-SID-SIG', 'Status': 'completed', 'CustomField': callback_id},
        }
        body = json.dumps(payload)

        bad_headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN'], 'Content-Type': 'application/json', 'X-Exotel-Signature': 'sha256=bad'}
        bad = self.client.post('/api/channels/ivr/callbacks/webhook', headers=bad_headers, data=body)
        self.assertEqual(bad.status_code, 401)

        digest = hmac.new(b'exotel-secret', body.encode('utf-8'), hashlib.sha256).hexdigest()
        good_headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN'], 'Content-Type': 'application/json', 'X-Exotel-Signature': f'sha256={digest}'}
        good = self.client.post('/api/channels/ivr/callbacks/webhook', headers=good_headers, data=body)
        self.assertEqual(good.status_code, 200)
        gp = good.get_json()
        self.assertTrue(gp['success'])
        self.assertEqual(gp['status'], 'connected')

    def test_ivr_callback_webhook_exotel_mapping(self):
        self.app.config.update(WEBHOOK_REQUIRE_REPLAY_PROTECTION=False, WEBHOOK_ALLOWED_IPS='', TWILIO_AUTH_TOKEN='')
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        create = self.client.post(
            '/api/channels/ivr/missed-call',
            headers=headers,
            json={'phone': '919944440000', 'district': self.valid_district, 'language': 'en'},
        )
        self.assertEqual(create.status_code, 200)
        callback_id = create.get_json()['callback_id']

        payload = {
            'provider': 'exotel',
            'Call': {
                'Sid': 'EXO-SID-1',
                'Status': 'completed',
                'CustomField': callback_id,
            },
        }
        resp = self.client.post('/api/channels/ivr/callbacks/webhook', headers=headers, json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body['success'])
        self.assertEqual(body['status'], 'connected')



    def test_ivr_callback_alerts_dedupe_cooldown(self):
        self.app.config.update(
            WEBHOOK_REQUIRE_REPLAY_PROTECTION=False,
            WEBHOOK_ALLOWED_IPS='',
            IVR_CALLBACK_ALERT_FAILURE_COUNT_THRESHOLD=1,
            IVR_CALLBACK_ALERT_DEAD_LETTER_COUNT_THRESHOLD=1,
            IVR_CALLBACK_ALERT_DEDUPE_SECONDS=3600,
            IVR_CALLBACK_ALERT_COOLDOWN_SECONDS=1200,
            OUTBOUND_CONNECTORS_ENABLED=False,
        )
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM ivr_callback_alert_events')
            db.execute('DELETE FROM ivr_callback_dead_letters')
            db.execute('DELETE FROM ivr_callback_jobs')
            db.commit()

        create = self.client.post('/api/channels/ivr/missed-call', headers=headers, json={'phone': '919966660000', 'district': self.valid_district, 'language': 'en'})
        self.assertEqual(create.status_code, 200)
        callback_id = create.get_json()['callback_id']

        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ivr_callback_jobs SET next_retry_at = ? WHERE callback_id = ?", ('1970-01-01T00:00:00Z', callback_id))
            db.commit()
        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ivr_callback_jobs SET next_retry_at = ? WHERE callback_id = ?", ('1970-01-01T00:00:00Z', callback_id))
            db.commit()
        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})

        first = self.client.get('/api/channels/ivr/callbacks/alerts', headers=self._auth(self.auditor_token), query_string={'notify': 'true'})
        self.assertEqual(first.status_code, 200)
        fp = first.get_json()
        self.assertGreaterEqual(len(fp.get('notifications') or []), 1)

        second = self.client.get('/api/channels/ivr/callbacks/alerts', headers=self._auth(self.auditor_token), query_string={'notify': 'true'})
        self.assertEqual(second.status_code, 200)
        sp = second.get_json()
        self.assertEqual(len(sp.get('notifications') or []), 0)


    def test_ivr_callback_alerts_endpoint_alert_state_contract(self):
        self.app.config.update(
            WEBHOOK_REQUIRE_REPLAY_PROTECTION=False,
            WEBHOOK_ALLOWED_IPS='',
            IVR_CALLBACK_ALERT_FAILURE_COUNT_THRESHOLD=9999,
            IVR_CALLBACK_ALERT_DEAD_LETTER_COUNT_THRESHOLD=9999,
        )
        response = self.client.get('/api/channels/ivr/callbacks/alerts', headers=self._auth(self.auditor_token), query_string={'notify': 'false'})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('alert_state', payload)
        self.assertIn('active_alerts', payload['alert_state'])
        self.assertIn('suppressed', payload['alert_state'])
        self.assertIn('dedupe_seconds', payload['alert_state'])
        self.assertIn('cooldown_seconds', payload['alert_state'])

    def test_ivr_callback_alerts_threshold_and_notify(self):
        self.app.config.update(
            WEBHOOK_REQUIRE_REPLAY_PROTECTION=False,
            WEBHOOK_ALLOWED_IPS='',
            IVR_CALLBACK_ALERT_FAILURE_COUNT_THRESHOLD=1,
            IVR_CALLBACK_ALERT_DEAD_LETTER_COUNT_THRESHOLD=1,
            OUTBOUND_CONNECTORS_ENABLED=False,
        )
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM ivr_callback_alert_events')
            db.execute('DELETE FROM ivr_callback_dead_letters')
            db.execute('DELETE FROM ivr_callback_jobs')
            db.commit()

        create = self.client.post(
            '/api/channels/ivr/missed-call',
            headers=headers,
            json={'phone': '919955550000', 'district': self.valid_district, 'language': 'en'},
        )
        self.assertEqual(create.status_code, 200)
        callback_id = create.get_json()['callback_id']

        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ivr_callback_jobs SET next_retry_at = ? WHERE callback_id = ?", ('1970-01-01T00:00:00Z', callback_id))
            db.commit()
        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ivr_callback_jobs SET next_retry_at = ? WHERE callback_id = ?", ('1970-01-01T00:00:00Z', callback_id))
            db.commit()
        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})

        alerts = self.client.get('/api/channels/ivr/callbacks/alerts', headers=self._auth(self.auditor_token), query_string={'notify': 'true'})
        self.assertEqual(alerts.status_code, 200)
        payload = alerts.get_json()
        self.assertTrue(payload['success'])
        self.assertGreaterEqual(len(payload.get('alerts') or []), 1)
        self.assertGreaterEqual(len(payload.get('notifications') or []), 1)

    def test_ivr_callback_alert_history_and_export_contract(self):
        self.app.config.update(
            WEBHOOK_REQUIRE_REPLAY_PROTECTION=False,
            WEBHOOK_ALLOWED_IPS='',
            IVR_CALLBACK_ALERT_FAILURE_COUNT_THRESHOLD=1,
            IVR_CALLBACK_ALERT_DEAD_LETTER_COUNT_THRESHOLD=1,
            OUTBOUND_CONNECTORS_ENABLED=False,
        )
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM ivr_callback_alert_events')
            db.execute('DELETE FROM ivr_callback_dead_letters')
            db.execute('DELETE FROM ivr_callback_jobs')
            db.commit()

        create = self.client.post('/api/channels/ivr/missed-call', headers=headers, json={'phone': '919977770000', 'district': self.valid_district, 'language': 'en'})
        self.assertEqual(create.status_code, 200)
        callback_id = create.get_json()['callback_id']

        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ivr_callback_jobs SET next_retry_at = ? WHERE callback_id = ?", ('1970-01-01T00:00:00Z', callback_id))
            db.commit()
        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE ivr_callback_jobs SET next_retry_at = ? WHERE callback_id = ?", ('1970-01-01T00:00:00Z', callback_id))
            db.commit()
        self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': True})

        alerts = self.client.get('/api/channels/ivr/callbacks/alerts', headers=self._auth(self.auditor_token), query_string={'notify': 'true'})
        self.assertEqual(alerts.status_code, 200)
        self.assertTrue(alerts.get_json()['success'])

        history = self.client.get('/api/channels/ivr/callbacks/alerts/history', headers=self._auth(self.auditor_token), query_string={'limit': 20})
        self.assertEqual(history.status_code, 200)
        payload = history.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('items', payload)
        self.assertIn('meta', payload)
        self.assertGreaterEqual(len(payload.get('items') or []), 1)

        csv_export = self.client.get('/api/channels/ivr/callbacks/alerts/history/export', headers=self._auth(self.auditor_token), query_string={'limit': 20, 'format': 'csv'})
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn('text/csv', csv_export.content_type)

    def test_ivr_callback_alert_history_filters(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM ivr_callback_alert_events')
            db.execute('DELETE FROM notification_deliveries')

            db.execute(
                "INSERT INTO notification_deliveries (delivery_id, request_id, stage, provider, channel, target_json, payload_json, status, attempt_count, max_retries, external_id, last_error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ('DEL-IVR-1', 'REQ-IVR-1', 'ivr_callback_failed_spike', 'exotel', 'voice', '{}', '{}', 'failed', 1, 3, '', '', '2026-09-09T10:00:00Z', '2026-09-09T10:00:00Z'),
            )
            db.execute(
                "INSERT INTO notification_deliveries (delivery_id, request_id, stage, provider, channel, target_json, payload_json, status, attempt_count, max_retries, external_id, last_error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ('DEL-IVR-2', 'REQ-IVR-2', 'ivr_callback_dead_letter_spike', 'twilio', 'voice', '{}', '{}', 'sent', 1, 3, '', '', '2026-09-09T11:00:00Z', '2026-09-09T11:00:00Z'),
            )

            db.execute(
                "INSERT INTO ivr_callback_alert_events (alert_key, alert_type, severity, observed_json, notification_delivery_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ('AK-IVR-1', 'ivr_callback_failed_spike', 'high', '{"alert":{"type":"ivr_callback_failed_spike","observed":7,"threshold":1},"metrics":{"failed":7,"dead_letter_count":2,"connected":1,"retry_pending":3,"total_callbacks":9}}', 'DEL-IVR-1', '2026-09-09T10:10:00Z'),
            )
            db.execute(
                "INSERT INTO ivr_callback_alert_events (alert_key, alert_type, severity, observed_json, notification_delivery_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ('AK-IVR-2', 'ivr_callback_dead_letter_spike', 'high', '{"alert":{"type":"ivr_callback_dead_letter_spike"}}', 'DEL-IVR-2', '2026-09-09T11:10:00Z'),
            )
            db.commit()

        filtered = self.client.get(
            '/api/channels/ivr/callbacks/alerts/history',
            headers=self._auth(self.auditor_token),
            query_string={
                'delivery_status': 'failed',
                'date_from': '2026-09-09T10:00:00Z',
                'date_to': '2026-09-09T10:59:59Z',
            },
        )
        self.assertEqual(filtered.status_code, 200)
        payload = filtered.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(len(payload.get('items') or []), 1)
        self.assertEqual(payload['items'][0]['delivery_status'], 'failed')

        exported = self.client.get(
            '/api/channels/ivr/callbacks/alerts/history/export',
            headers=self._auth(self.auditor_token),
            query_string={'delivery_status': 'failed', 'format': 'json'},
        )
        self.assertEqual(exported.status_code, 200)
        export_payload = exported.get_json()
        self.assertTrue(export_payload['success'])
        self.assertEqual(len(export_payload.get('items') or []), 1)
        self.assertEqual(export_payload['items'][0].get('observed_value'), 7)
        self.assertEqual(export_payload['items'][0].get('threshold_value'), 1)

        exported_csv = self.client.get(
            '/api/channels/ivr/callbacks/alerts/history/export',
            headers=self._auth(self.auditor_token),
            query_string={'delivery_status': 'failed', 'format': 'csv'},
        )
        self.assertEqual(exported_csv.status_code, 200)
        self.assertIn('observed_value', exported_csv.get_data(as_text=True))
        self.assertIn('threshold_value', exported_csv.get_data(as_text=True))
    def test_ivr_callback_alert_history_rbac(self):
        denied_history = self.client.get('/api/channels/ivr/callbacks/alerts/history')
        self.assertEqual(denied_history.status_code, 401)

        denied_export = self.client.get('/api/channels/ivr/callbacks/alerts/history/export', query_string={'format': 'json'})
        self.assertEqual(denied_export.status_code, 401)
    def test_ivr_callback_metrics_contract(self):
        self.app.config.update(WEBHOOK_REQUIRE_REPLAY_PROTECTION=False, WEBHOOK_ALLOWED_IPS='')
        headers = {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

        create = self.client.post(
            '/api/channels/ivr/missed-call',
            headers=headers,
            json={'phone': '919922220000', 'district': self.valid_district, 'language': 'en'},
        )
        self.assertEqual(create.status_code, 200)

        process = self.client.post('/api/channels/ivr/callbacks/process', headers=self._auth(self.analyst_token), json={'limit': 10, 'force_fail': False})
        self.assertEqual(process.status_code, 200)

        metrics = self.client.get('/api/channels/ivr/callbacks/metrics', headers=self._auth(self.auditor_token))
        self.assertEqual(metrics.status_code, 200)
        payload = metrics.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('metrics', payload)
        self.assertIn('total_callbacks', payload['metrics'])
        self.assertIn('connected', payload['metrics'])
        self.assertIn('dead_letter_count', payload['metrics'])

    def test_governance_endpoints_contract_and_rbac(self):
        admin_res = self.client.get('/api/v1/governance/consent-ledger', headers=self._auth(self.admin_token))
        self.assertEqual(admin_res.status_code, 200)
        self.assertTrue(admin_res.get_json()['success'])

        auditor_res = self.client.get('/api/v1/governance/dpdp-controls', headers=self._auth(self.auditor_token))
        self.assertEqual(auditor_res.status_code, 200)
        self.assertTrue(auditor_res.get_json()['success'])
        self.assertIn('version', auditor_res.get_json()['dpdp'])

        denied = self.client.get('/api/v1/governance/dpdp-controls', headers=self._auth(self.analyst_token))
        self.assertEqual(denied.status_code, 403)


    def test_governance_dpdp_evidence_readiness_dpg_contract_and_rbac(self):
        evidence = self.client.get('/api/v1/governance/dpdp/evidence', headers=self._auth(self.auditor_token))
        self.assertEqual(evidence.status_code, 200)
        ep = evidence.get_json()
        self.assertTrue(ep['success'])
        self.assertIn('artifacts', ep['evidence'])

        readiness = self.client.get('/api/v1/governance/dpdp/readiness', headers=self._auth(self.admin_token))
        self.assertEqual(readiness.status_code, 200)
        rp = readiness.get_json()
        self.assertTrue(rp['success'])
        self.assertIn('score', rp['readiness'])

        dpg = self.client.get('/api/v1/governance/dpg-status', headers=self._auth(self.auditor_token))
        self.assertEqual(dpg.status_code, 200)
        dp = dpg.get_json()
        self.assertTrue(dp['success'])
        self.assertIn('registration_status', dp['dpg'])

        denied = self.client.get('/api/v1/governance/dpg-status', headers=self._auth(self.analyst_token))
        self.assertEqual(denied.status_code, 403)

    def test_pii_scrubbing_on_submit_and_federation_ingest(self):
        submit = self.client.post('/api/submit', json={'language': 'en', 'district': self.valid_district, 'source': 'Web Form', 'text': 'My phone is 9876543210 and aadhaar 123412341234. Email me at citizen@example.com'})
        self.assertEqual(submit.status_code, 200)
        req_id = submit.get_json()['request_id']
        with self.app.app_context():
            db = get_db()
            row = db.execute('SELECT original_text, ai_metadata_json FROM citizen_requests WHERE request_id = ? LIMIT 1', (req_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertIn('[PII_PHONE:', str(row['original_text']))
            self.assertNotIn('9876543210', str(row['original_text']))
            self.assertIn('pii_scrub', json.loads(row['ai_metadata_json'] or '{}'))

        fed = self.client.post('/api/v1/federation/publish', headers=self._auth(self.admin_token), json={'source': 'external-x', 'external_event_id': 'evt-1', 'text': 'Call me 9988776655 and PAN ABCDE1234F', 'district': self.valid_district, 'language': 'en'})
        self.assertEqual(fed.status_code, 200)
        fid = fed.get_json()['request_id']
        with self.app.app_context():
            db = get_db()
            row2 = db.execute('SELECT original_text FROM citizen_requests WHERE request_id = ? LIMIT 1', (fid,)).fetchone()
            self.assertIsNotNone(row2)
            self.assertIn('[PII_PHONE:', str(row2['original_text']))
            self.assertNotIn('9988776655', str(row2['original_text']))

    def test_public_transparency_dp_metadata(self):
        self.app.config.update(PUBLIC_TRANSPARENCY_DP_ENABLED=True, PUBLIC_TRANSPARENCY_DP_EPSILON=0.75)
        response = self.client.get('/api/public/transparency/summary')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('differential_privacy', payload['summary'])
        self.assertIn('anonymization', payload['summary'])
        self.assertTrue(payload['summary']['differential_privacy']['enabled'])
if __name__ == '__main__':
    unittest.main()





























































