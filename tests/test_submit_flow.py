import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
except ImportError:
    sync_playwright = None
from werkzeug.serving import make_server

from visbharat import create_app
from visbharat.config import Config
from visbharat.db import get_db
from visbharat.services.google_bigquery import GoogleBigQueryClient


class TestSubmitFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest('Install Playwright and Chromium to run the submission integration suite.')
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except PlaywrightError:
            cls.playwright.stop()
            raise unittest.SkipTest('Run playwright install chromium to enable submission browser tests.')
        cls.temporary = tempfile.TemporaryDirectory(prefix='nvb-submit-test-')
        with patch.object(Config, 'DATABASE_PATH', str(Path(cls.temporary.name) / 'requests.db')), patch.object(Config, 'DATABASE_URL', ''), patch.dict(os.environ, {'NVB_DISABLE_EXTERNAL_SERVICES': '1'}):
            cls.app = create_app()
        cls.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False, JURY_REQUIRE_LIVE_MODELS=True)
        cls.ai = Mock(spec=['classify_request', 'translate_text'])
        cls.ai.classify_request.return_value = {'category': 'Road', 'urgency': 'Routine', 'sentiment': 'Concerned', 'confidence': 0.94, 'model': 'external-ai-test-fixture', 'provider_mode': 'google_ai_live'}
        cls.ai.translate_text.side_effect = lambda text, source_lang, target_lang: {'translated_text': text, 'model': 'external-translation-test-fixture', 'provider_mode': 'google_translation_live'}
        cls.app.extensions['google_ai_client'] = cls.ai
        cls.server = make_server('127.0.0.1', 0, cls.app, threaded=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.thread.join(timeout=10)
        cls.temporary.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        self.context.route('https://**/*', lambda route: route.abort())
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.client = self.app.test_client()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def count(self):
        with self.app.app_context():
            return get_db().execute('SELECT COUNT(*) AS count FROM citizen_requests').fetchone()['count']

    def row(self, request_id):
        with self.app.app_context():
            return dict(get_db().execute('SELECT * FROM citizen_requests WHERE request_id = ?', (request_id,)).fetchone())

    def fill_form(self):
        self.page.goto(self.base_url + '/submit', wait_until='domcontentloaded')
        self.page.wait_for_function("document.querySelector('#state').options.length > 1")
        self.page.select_option('#state', label='Tamil Nadu')
        self.page.wait_for_function("document.querySelector('#district').options.length > 1")
        self.page.select_option('#district', label='Karur')
        self.page.locator('#ward').fill('Ward 12')
        self.page.locator('#complaint-text').fill('Street lighting is broken and needs repair.')
        expect(self.page.locator('#ai-confidence')).to_have_text('94.0%')
        self.page.evaluate("() => { document.getElementById('location-lat').value = '10.96'; document.getElementById('location-lng').value = '78.07'; }")

    def saved_id(self):
        expect(self.page.locator('#successMessage')).to_be_visible(timeout=15000)
        return self.page.locator('#complaintId').inner_text()

    def test_typed_request_persists_and_tracks_without_evidence(self):
        before = self.count()
        evidence_requests = []
        self.page.on('request', lambda request: evidence_requests.append(request.url) if 'ingest-stub' in request.url else None)
        self.fill_form()
        self.page.locator('#submitBtn').click()
        request_id = self.saved_id()
        row = self.row(request_id)
        self.assertEqual(self.count(), before + 1)
        self.assertEqual((row['lat'], row['lng']), (10.96, 78.07))
        self.assertEqual(row['ward'], 'Ward 12')
        self.assertEqual(row['district'], 'Karur')
        self.assertEqual(evidence_requests, [])
        self.page.locator('#trackStatusBtn').click()
        expect(self.page.locator('#trackResultContainer')).to_contain_text(request_id)

    def test_voice_keeps_location_category_and_consent(self):
        self.fill_form()
        self.page.select_option('#category', 'Electricity')
        self.page.locator('#consentGranted').uncheck()
        self.page.evaluate("() => { lastVoiceAudioBase64 = btoa('audio fixture'); lastVoiceMimeType = 'audio/webm'; }")
        with patch('visbharat.blueprints.api._run_speech_to_text', return_value={'transcript': 'Street lighting is broken.', 'confidence': 0.99, 'model': 'external-asr-test-fixture', 'provider_mode': 'google_stt_live'}):
            self.page.locator('#submitBtn').click()
            request_id = self.saved_id()
        row = self.row(request_id)
        self.assertEqual(row['source_channel'], 'Voice IVR')
        self.assertEqual(row['category'], 'Electricity')
        self.assertEqual((row['lat'], row['lng'], row['ward']), (10.96, 78.07, 'Ward 12'))
        with self.app.app_context():
            consent = get_db().execute('SELECT consent_granted FROM consent_ledger WHERE request_id = ?', (request_id,)).fetchone()
            self.assertFalse(consent['consent_granted'])

    def test_network_retry_reuses_saved_request(self):
        self.fill_form()
        before = self.count()
        keys = []

        def lose_first_response(route):
            keys.append(route.request.headers['x-idempotency-key'])
            if len(keys) == 1:
                response = route.fetch()
                self.assertTrue(response.json()['success'])
                route.abort('failed')
            else:
                route.continue_()

        self.page.route('**/api/submit', lose_first_response)
        self.page.locator('#submitBtn').click()
        expect(self.page.locator('#submissionErrorText')).to_contain_text('Could not reach the server')
        expect(self.page.locator('#complaint-text')).to_have_value('Street lighting is broken and needs repair.')
        self.page.locator('#submitBtn').click()
        self.saved_id()
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(self.count(), before + 1)

    def test_failed_evidence_does_not_hide_saved_request(self):
        self.fill_form()
        before = self.count()
        self.page.locator('#request-evidence summary').click()
        self.page.locator('#photo-attachments').set_input_files({'name': 'street.png', 'mimeType': 'image/png', 'buffer': b'image fixture'})
        attempts = []

        def evidence_failure(route):
            attempts.append(route.request.post_data_json['request_id'])
            if len(attempts) == 1:
                route.fulfill(status=503, json={'success': False, 'error': 'Evidence service unavailable'})
            else:
                route.continue_()

        self.page.route('**/api/attachments/ingest-stub', evidence_failure)
        self.page.locator('#submitBtn').click()
        request_id = self.saved_id()
        expect(self.page.locator('#submissionAttachmentNotice')).to_contain_text('Your request is saved, but evidence registration failed')
        self.page.locator('#retryAttachmentsBtn').click()
        expect(self.page.locator('#submissionAttachmentNotice')).to_contain_text('have been accepted')
        self.assertEqual(attempts, [request_id, request_id])
        self.assertEqual(self.count(), before + 1)

    def test_invalid_evidence_is_rejected_before_request_creation(self):
        self.fill_form()
        before = self.count()
        self.page.locator('#request-evidence summary').click()
        self.page.locator('#photo-attachments').set_input_files([{'name': f'image-{index}.png', 'mimeType': 'image/png', 'buffer': b'fixture'} for index in range(21)])
        self.page.locator('#submitBtn').click()
        expect(self.page.locator('#submissionErrorText')).to_contain_text('no more than 20 photos')
        self.assertEqual(self.count(), before)

    def test_non_json_failure_preserves_form_and_shows_clear_error(self):
        self.fill_form()
        self.page.route('**/api/submit', lambda route: route.fulfill(status=500, content_type='text/html', body='<h1>Server error</h1>'))
        self.page.locator('#submitBtn').click()
        expect(self.page.locator('#submissionErrorText')).to_contain_text('unreadable response (HTTP 500)')
        expect(self.page.locator('#submissionError')).to_be_focused()
        expect(self.page.locator('#complaintForm')).to_be_visible()
        expect(self.page.locator('#submitBtn')).to_be_enabled()

    def test_recording_must_finish_before_submission(self):
        self.fill_form()
        before = self.count()
        self.page.evaluate('recording = true')
        self.page.locator('#submitBtn').click()
        expect(self.page.locator('#submissionErrorText')).to_contain_text('Stop recording')
        self.page.evaluate('() => { recording = false; voiceFinalizing = true; }')
        self.page.locator('#submitBtn').click()
        expect(self.page.locator('#submissionErrorText')).to_contain_text('wait for transcription')
        self.assertEqual(self.count(), before)

    def test_voice_failure_allows_explicit_written_text_submission(self):
        self.fill_form()
        before = self.count()
        self.page.evaluate("lastVoiceAudioBase64 = btoa('audio fixture')")
        with patch('visbharat.blueprints.api._run_speech_to_text', side_effect=ValueError('Speech provider unavailable')):
            self.page.locator('#submitBtn').click()
            expect(self.page.locator('#submissionErrorText')).to_contain_text('voice processing failed')
        self.page.locator('#submitTextInsteadBtn').click()
        self.page.locator('#submitBtn').click()
        request_id = self.saved_id()
        self.assertEqual(self.row(request_id)['source_channel'], 'Web Form')
        self.assertEqual(self.count(), before + 1)

    def test_ai_outage_returns_json_without_saving(self):
        before = self.count()
        with patch.object(self.ai, 'classify_request', side_effect=ValueError('external AI unavailable')):
            response = self.client.post('/api/submit', json={'text': 'Broken street light', 'language': 'en', 'district': 'Karur'})
        self.assertEqual(response.status_code, 503)
        self.assertIn('Your request was not saved', response.get_json()['error'])
        self.assertEqual(self.count(), before)

    def test_retry_after_post_save_failure_does_not_duplicate(self):
        before = self.count()
        headers = {'X-Idempotency-Key': str(uuid.uuid4())}
        payload = {'text': 'Broken street light', 'language': 'en', 'district': 'Karur', 'ward': 'Ward 12'}
        with patch('visbharat.blueprints.api.assign_request_to_cluster', side_effect=RuntimeError('post-save processing failed')):
            response = self.client.post('/api/submit', json=payload, headers=headers)
        self.assertEqual(response.status_code, 500)
        retry = self.client.post('/api/submit', json=payload, headers=headers)
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.get_json()['idempotent_reused'])
        self.assertEqual(self.count(), before + 1)

    def test_cloud_outage_keeps_local_request_and_dead_letter(self):
        cloud = Mock()
        cloud.insert_request.side_effect = TimeoutError('cloud write timed out')
        before = self.count()
        with patch.dict(self.app.extensions, {'google_bigquery_client': cloud}):
            response = self.client.post('/api/submit', json={'text': 'Broken street light', 'language': 'en', 'district': 'Karur', 'ward': 'Ward 12'})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['cloud_ingestion']['dead_letter_logged'])
        self.assertEqual(self.count(), before + 1)

    def test_bigquery_insert_has_bounded_timeout_without_long_retries(self):
        client = object.__new__(GoogleBigQueryClient)
        client.client = Mock()
        client.client.insert_rows_json.return_value = []
        client.project_id, client.dataset, client.table = 'test-project', 'test_dataset', 'test_table'
        client.col_id, client.col_source, client.col_date, client.col_lang = 'request_id', 'source_channel', 'created_at', 'input_language'
        client.schema_fields = set()
        self.assertTrue(client.insert_request({'request_id': 'TEST-NO-NETWORK'}))
        arguments = client.client.insert_rows_json.call_args.kwargs
        self.assertIsNone(arguments['retry'])
        self.assertEqual(arguments['timeout'], 10)


if __name__ == '__main__':
    unittest.main()
