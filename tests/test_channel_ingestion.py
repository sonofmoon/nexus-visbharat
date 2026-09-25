import os
import json
import hmac
import hashlib
import base64
import time
import unittest

from visbharat import create_app
from visbharat.db import init_db, seed_default_users


class TestChannelIngestion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_channels.db')
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
            WEBHOOK_SHARED_TOKEN='test-webhook-token',
            WHATSAPP_VERIFY_TOKEN='test-whatsapp-verify',
            META_APP_SECRET='test-meta-secret',
            TELEGRAM_WEBHOOK_SECRET='test-telegram-secret',
            TWILIO_AUTH_TOKEN='test-twilio-token',
            WEBHOOK_REQUIRE_REPLAY_PROTECTION=True,
            WEBHOOK_REPLAY_WINDOW_SECONDS=300,
            WEBHOOK_NONCE_CACHE_MAX=1000,
            WEBHOOK_BIND_REPLAY_IN_SIGNATURE=True,
            WEBHOOK_REPLAY_STORE='db',
            WEBHOOK_ALLOWED_IPS='',
        )
        with cls.app.app_context():
            init_db()
            seed_default_users()

        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except OSError:
                pass

    def _base_headers(self):
        return {'X-Webhook-Token': self.app.config['WEBHOOK_SHARED_TOKEN']}

    def _replay_headers(self, nonce='nonce-1', ts=None):
        if ts is None:
            ts = int(time.time())
        return {
            'X-Webhook-Timestamp': str(ts),
            'X-Webhook-Nonce': nonce,
        }

    def _meta_sig_header(self, raw_body: bytes, ts: int, nonce: str):
        bind_payload = f"{ts}:{nonce}:".encode('utf-8') + raw_body
        digest = hmac.new(
            self.app.config['META_APP_SECRET'].encode('utf-8'),
            bind_payload,
            hashlib.sha256,
        ).hexdigest()
        return {'X-Hub-Signature-256': f'sha256={digest}'}

    def _telegram_headers(self):
        return {'X-Telegram-Bot-Api-Secret-Token': self.app.config['TELEGRAM_WEBHOOK_SECRET']}

    def _twilio_signature(self, url: str, params: dict, ts: int, nonce: str):
        items = sorted((str(k), str(v)) for k, v in params.items())
        to_sign = url + ''.join(k + v for k, v in items) + str(ts) + str(nonce)
        digest = hmac.new(
            self.app.config['TWILIO_AUTH_TOKEN'].encode('utf-8'),
            to_sign.encode('utf-8'),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode('utf-8')

    def test_whatsapp_verification_get(self):
        response = self.client.get(
            '/api/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=test-whatsapp-verify&hub.challenge=abc123'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), 'abc123')

    
    def test_webhook_rejects_disallowed_source_ip(self):
        original = self.app.config.get('WEBHOOK_ALLOWED_IPS', '')
        self.app.config['WEBHOOK_ALLOWED_IPS'] = '10.10.10.10/32'
        try:
            body = {'text': 'Need drainage cleanup urgently', 'language': 'en', 'district': 'Chennai', 'from': 'wa-user-ip'}
            raw = json.dumps(body).encode('utf-8')
            ts = int(time.time())
            headers = {}
            headers.update(self._base_headers())
            headers.update(self._replay_headers(nonce='wa-ip-block-1', ts=ts))
            headers.update(self._meta_sig_header(raw, ts, 'wa-ip-block-1'))

            response = self.client.post('/api/channels/whatsapp/webhook', headers=headers, data=raw, content_type='application/json')
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.get_json()['error'], 'source ip not allowed')
        finally:
            self.app.config['WEBHOOK_ALLOWED_IPS'] = original
    def test_whatsapp_webhook_rejects_replayed_nonce(self):
        body = {'text': 'Need water tanker', 'language': 'en', 'district': 'Chennai', 'from': 'wa-user-1'}
        raw = json.dumps(body).encode('utf-8')
        headers = {}
        headers.update(self._base_headers())
        ts = int(time.time())
        headers.update(self._replay_headers(nonce='replay-whatsapp-1', ts=ts))
        headers.update(self._meta_sig_header(raw, ts, 'replay-whatsapp-1'))

        first = self.client.post('/api/channels/whatsapp/webhook', headers=headers, data=raw, content_type='application/json')
        self.assertEqual(first.status_code, 200)

        second = self.client.post('/api/channels/whatsapp/webhook', headers=headers, data=raw, content_type='application/json')
        self.assertEqual(second.status_code, 401)
        self.assertEqual(second.get_json()['error'], 'invalid or replayed webhook request')

    def test_whatsapp_webhook_rejects_stale_timestamp(self):
        body = {'text': 'Need drainage cleanup urgently', 'language': 'en', 'district': 'Chennai', 'from': 'wa-user-2'}
        raw = json.dumps(body).encode('utf-8')
        old_ts = int(time.time()) - 1000
        headers = {}
        headers.update(self._base_headers())
        headers.update(self._replay_headers(nonce='stale-whatsapp-1', ts=old_ts))
        headers.update(self._meta_sig_header(raw, old_ts, 'stale-whatsapp-1'))

        response = self.client.post('/api/channels/whatsapp/webhook', headers=headers, data=raw, content_type='application/json')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['error'], 'invalid or replayed webhook request')

    def test_whatsapp_webhook_ingests_with_valid_meta_signature(self):
        body = {'text': 'Need drainage cleanup urgently', 'language': 'en', 'district': 'Chennai', 'from': 'wa-user-3'}
        raw = json.dumps(body).encode('utf-8')
        headers = {}
        headers.update(self._base_headers())
        ts = int(time.time())
        headers.update(self._replay_headers(nonce='wa-valid-1', ts=ts))
        headers.update(self._meta_sig_header(raw, ts, 'wa-valid-1'))

        response = self.client.post('/api/channels/whatsapp/webhook', headers=headers, data=raw, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['channel'], 'WhatsApp')

    def test_telegram_webhook_rejects_bad_secret(self):
        response = self.client.post(
            '/api/channels/telegram/webhook',
            headers={**self._base_headers(), **self._replay_headers(nonce='tg-bad-secret-1')},
            json={
                'language': 'en',
                'district': 'Chennai',
                'message': {'text': 'Need drainage cleanup urgently', 'from': {'id': 12345}},
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['error'], 'invalid telegram secret')

    def test_telegram_webhook_ingests(self):
        response = self.client.post(
            '/api/channels/telegram/webhook',
            headers={**self._base_headers(), **self._replay_headers(nonce='tg-valid-1'), **self._telegram_headers()},
            json={
                'language': 'en',
                'district': 'Chennai',
                'message': {'text': 'Need drainage cleanup urgently', 'from': {'id': 12345}},
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['channel'], 'Telegram')

    def test_sms_webhook_rejects_without_token(self):
        response = self.client.post(
            '/api/channels/sms/webhook',
            headers=self._replay_headers(nonce='sms-no-token-1'),
            json={'text': 'Street lights are off', 'district': 'Chennai', 'language': 'en'},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['error'], 'invalid webhook token')

    def test_sms_webhook_rejects_bad_twilio_signature(self):
        response = self.client.post(
            '/api/channels/sms/webhook',
            headers={**self._base_headers(), **self._replay_headers(nonce='sms-bad-sig-1')},
            data={'body': 'Street lights are off', 'district': 'Chennai', 'language': 'en', 'from': '+911234567890'},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()['error'], 'invalid twilio signature')

    def test_sms_webhook_ingests_with_valid_twilio_signature(self):
        form = {'body': 'Street lights are off', 'district': 'Chennai', 'language': 'en', 'from': '+911234567890'}
        url = 'http://localhost/api/channels/sms/webhook'
        ts = int(time.time())
        headers = {**self._base_headers(), **self._replay_headers(nonce='sms-valid-1', ts=ts)}
        headers['X-Twilio-Signature'] = self._twilio_signature(url, form, ts, 'sms-valid-1')
        response = self.client.post('/api/channels/sms/webhook', headers=headers, data=form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['channel'], 'SMS')

    def test_ivr_webhook_ingests(self):
        form = {'transcript': 'Water supply problem in ward 11', 'district': 'Hyderabad', 'language': 'en', 'caller': '+911234567890'}
        url = 'http://localhost/api/channels/ivr/webhook'
        ts = int(time.time())
        headers = {**self._base_headers(), **self._replay_headers(nonce='ivr-valid-1', ts=ts)}
        headers['X-Twilio-Signature'] = self._twilio_signature(url, form, ts, 'ivr-valid-1')
        response = self.client.post('/api/channels/ivr/webhook', headers=headers, data=form)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['channel'], 'Voice IVR')

    def test_gmail_health_endpoint(self):
        response = self.client.get('/api/channels/email/gmail/health')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['channel'], 'Gmail')
        self.assertIn('enabled', payload)

    def test_gmail_pubsub_webhook_rejects_when_disabled(self):
        self.app.config['GMAIL_ENABLED'] = False
        response = self.client.post('/api/channels/email/gmail/pubsub', json={})
        self.assertEqual(response.status_code, 503)

    def test_gmail_pubsub_webhook_rejects_bad_token(self):
        self.app.config.update(
            GMAIL_ENABLED=True,
            GMAIL_MAILBOX='nexusvisbharat@gmail.com',
            GMAIL_PUBSUB_SHARED_TOKEN='secret-token-123',
        )
        response = self.client.post(
            '/api/channels/email/gmail/pubsub',
            headers={'X-Gmail-PubSub-Token': 'wrong-token'},
            json={},
        )
        self.assertEqual(response.status_code, 401)

    def test_gmail_pubsub_ingests_and_deduplicates(self):
        from unittest.mock import MagicMock, patch
        from visbharat.services.gmail_gateway import renew_watch

        self.app.config.update(
            GMAIL_ENABLED=True,
            GMAIL_MAILBOX='nexusvisbharat@gmail.com',
            GMAIL_PUBSUB_SHARED_TOKEN='secret-token-123',
            GMAIL_PUBSUB_TOPIC='projects/test/topics/test',
            GMAIL_SEND_CONFIRMATIONS=True,
        )

        mock_client = MagicMock()
        mock_client.mailbox = 'nexusvisbharat@gmail.com'
        mock_client.watch.return_value = {'historyId': '100', 'expiration': 1800000000000}
        mock_client.history.return_value = ['msg_abc_1']
        mock_client.message.return_value = {
            'id': 'msg_abc_1',
            'threadId': 'thread_1',
            'payload': {
                'headers': [
                    {'name': 'From', 'value': 'Citizen User <citizen@example.com>'},
                    {'name': 'Subject', 'value': 'Pothole on Main Road in Chennai'},
                ],
                'body': {'data': base64.urlsafe_b64encode(b'Please repair the deep pothole on Main Road.').decode('ascii')},
            },
        }
        mock_client.send_confirmation.return_value = {'id': 'sent_1'}

        with self.app.app_context():
            renew_watch(mock_client)

        pubsub_data = base64.b64encode(json.dumps({'emailAddress': 'nexusvisbharat@gmail.com', 'historyId': '105'}).encode('utf-8')).decode('ascii')
        headers = {'X-Gmail-PubSub-Token': 'secret-token-123'}

        with patch('visbharat.services.gmail_gateway.GmailClient', return_value=mock_client):
            res1 = self.client.post('/api/channels/email/gmail/pubsub', headers=headers, json={'message': {'data': pubsub_data}})
            self.assertEqual(res1.status_code, 200)
            data1 = res1.get_json()
            self.assertTrue(data1['success'])
            self.assertEqual(data1['status'], 'ok')
            self.assertEqual(len(data1['processed']), 1)
            self.assertEqual(data1['processed'][0]['status'], 'processed')
            ticket_id = data1['processed'][0]['request_id']
            self.assertTrue(ticket_id.startswith('NVB-'))
            mock_client.send_confirmation.assert_called_once()

            res2 = self.client.post('/api/channels/email/gmail/pubsub', headers=headers, json={'message': {'data': pubsub_data}})
            self.assertEqual(res2.status_code, 200)
            data2 = res2.get_json()
            self.assertEqual(data2['processed'][0]['status'], 'duplicate')
            self.assertEqual(data2['processed'][0]['request_id'], ticket_id)

    def test_gmail_ignores_nvb_confirmation_feedback_messages(self):
        from unittest.mock import MagicMock, patch
        from visbharat.services.gmail_gateway import renew_watch

        self.app.config.update(
            GMAIL_ENABLED=True,
            GMAIL_MAILBOX='nexusvisbharat@gmail.com',
            GMAIL_PUBSUB_SHARED_TOKEN='secret-token-loop',
            GMAIL_PUBSUB_TOPIC='projects/test/topics/test',
            GMAIL_SEND_CONFIRMATIONS=True,
        )
        mock_client = MagicMock()
        mock_client.mailbox = 'nexusvisbharat@gmail.com'
        mock_client.watch.return_value = {'historyId': '200', 'expiration': 1800000000000}
        mock_client.history.return_value = ['msg_confirmation_1']
        ticket_id = 'NVB-20260924DB67'
        mock_client.message.return_value = {
            'id': 'msg_confirmation_1',
            'threadId': 'thread_confirmation',
            'payload': {
                'headers': [
                    {'name': 'From', 'value': 'citizen@example.com'},
                    {'name': 'Subject', 'value': f'Nexus VisBharat Ticket {ticket_id}'},
                ],
                'body': {'data': base64.urlsafe_b64encode((
                    f'Your NVB request has been received.\n\nTicket ID: {ticket_id}\n'
                    f'Subject: Nexus VisBharat Ticket {ticket_id}\n\n'
                    'The request is now queued for municipal review. Please keep this Ticket ID for tracking.\n'
                    'Do not reply with passwords, Aadhaar numbers, bank details, or other sensitive information.'
                ).encode('utf-8')).decode('ascii')},
            },
        }

        with self.app.app_context():
            renew_watch(mock_client)

        pubsub_data = base64.b64encode(json.dumps({'emailAddress': 'nexusvisbharat@gmail.com', 'historyId': '205'}).encode('utf-8')).decode('ascii')
        with patch('visbharat.services.gmail_gateway.GmailClient', return_value=mock_client):
            response = self.client.post(
                '/api/channels/email/gmail/pubsub',
                headers={'X-Gmail-PubSub-Token': 'secret-token-loop'},
                json={'message': {'data': pubsub_data}},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['processed'][0]['status'], 'ignored_system_message')
        mock_client.send_confirmation.assert_not_called()


if __name__ == '__main__':
    unittest.main()



