"""Offline regressions for empty ASR results and Telegram voice recovery."""

from copy import deepcopy
from unittest.mock import Mock

from flask import Flask
import pytest

from visbharat.blueprints import api
from visbharat.services import telegram_gateway as telegram


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JURY_REQUIRE_LIVE_MODELS=False,
        LANGUAGE_ASR_PROVIDER='google',
        LANGUAGE_ASR_STRICT_MODE=False,
        TELEGRAM_BOT_TOKEN='test-token',
    )
    with app.app_context():
        yield app


def provider(transcript, mode='google_stt_live', **extra):
    client = Mock(spec=['transcribe_bytes'])
    client.transcribe_bytes.return_value = {
        'transcript': transcript, 'provider_mode': mode, **extra,
    }
    return client


@pytest.mark.parametrize('transcript', ['', ' \n\t', None, ['not a transcript']])
def test_empty_or_invalid_primary_tries_next_provider(app, transcript):
    primary = provider(transcript)
    secondary = provider('మా వీధిలో మురుగు కాలువ మూసుకుపోయింది.', 'gemini_audio_stt_live')
    app.extensions.update(google_stt_client=primary, google_ai_client=secondary)

    result = api._run_speech_to_text({}, 'te', audio_bytes=b'fixture', mime_type='audio/ogg')

    assert result == secondary.transcribe_bytes.return_value
    secondary.transcribe_bytes.assert_called_once_with(
        audio_bytes=b'fixture', language_code='te-IN', mime_type='audio/ogg',
    )
    events = app.extensions['asr_metrics_store']['events']
    assert [(e['provider'], e['status']) for e in events] == [('google', 'failed'), ('gemini', 'success')]
    assert events[1]['fallback_from'] == 'google'
    assert api._read_asr_circuit('google')['failure_count'] == 1


def test_valid_primary_does_not_call_secondary(app):
    primary = provider('குடிநீர் குழாயில் கசிவு உள்ளது.')
    secondary = provider('unused', 'gemini_audio_stt_live')
    app.extensions.update(google_stt_client=primary, google_ai_client=secondary)
    result = api._run_speech_to_text({}, 'ta', audio_bytes=b'fixture', require_live=True)
    assert result == primary.transcribe_bytes.return_value
    secondary.transcribe_bytes.assert_not_called()
    assert [e['status'] for e in app.extensions['asr_metrics_store']['events']] == ['success']


def mock_telegram_download(monkeypatch):
    download = Mock(side_effect=[
        Mock(json=lambda: {'result': {'file_path': 'voice/fixture.ogg'}}),
        Mock(content=b'fixture'),
    ])
    monkeypatch.setattr(telegram.requests, 'get', download)
    return download


def test_telegram_voice_uses_secondary_transcript(app, monkeypatch):
    app.extensions.update(
        google_stt_client=provider(''),
        google_ai_client=provider('  The drain is blocked.  ', 'gemini_audio_stt_live'),
    )
    mock_telegram_download(monkeypatch)
    assert telegram._voice_text('fictional-file', 'te') == 'The drain is blocked.'


@pytest.mark.parametrize('failure', ['empty', 'exception', 'no_clients', 'simulation', 'fallback'])
def test_telegram_all_providers_fail_preserves_draft_and_accepts_text(app, monkeypatch, failure):
    primary = provider('')
    secondary = provider(' \n ', 'gemini_audio_stt_live')
    if failure == 'exception':
        primary.transcribe_bytes.side_effect = RuntimeError('Provider unavailable')
        secondary.transcribe_bytes.side_effect = RuntimeError('Provider unavailable')
    if failure == 'fallback':
        # Even a live-looking mode must not override an explicit fallback flag.
        primary = provider('Invented request', fallback_used=True)
        secondary = provider('Invented request', 'simulation', model='simulated')
    if failure != 'no_clients':
        app.extensions.update(google_stt_client=primary, google_ai_client=secondary)
    if failure == 'simulation':
        app.config['LANGUAGE_ASR_PROVIDER'] = 'simulation'

    mock_telegram_download(monkeypatch)
    simulation = Mock(side_effect=AssertionError('Never invent a Telegram transcript'))
    monkeypatch.setattr(api, 'simulate_speech_to_text', simulation)
    session = {
        'stage': 'privacy', 'language': 'te', 'issue': 'Voice report',
        'voice_file_id': 'fictional-file', 'state': 'Andhra Pradesh',
        'district': 'Tirupati', 'ward': 'Example ward', 'nonce': 'retained-nonce',
        'consent_granted': True,
    }
    original = deepcopy(session)
    stored = {}
    monkeypatch.setattr(telegram, '_save', lambda chat, draft: stored.update(deepcopy(draft)))
    monkeypatch.setattr(telegram, '_session', lambda chat: deepcopy(stored))
    monkeypatch.setattr(telegram, '_i18n', lambda language, key: key)
    notify = Mock()
    monkeypatch.setattr(telegram, '_text', notify)
    ingest = Mock()

    telegram._submit(123, session, ingest)

    ingest.assert_not_called()
    simulation.assert_not_called()
    assert stored == {**original, 'stage': 'issue', 'consent_granted': False}
    assert notify.call_args.args == (123, 'asr_error')
    assert all(e['status'] != 'success' for e in app.extensions.get('asr_metrics_store', {}).get('events', []))

    # The retry prompt must actually permit entering replacement text.
    telegram._message({'message': {'text': 'The drain near the school is blocked.'}}, 123, ingest)
    assert stored['issue'] == 'The drain near the school is blocked.'
    assert stored['stage'] == 'state'
    assert stored['district'] == 'Tirupati'
    assert stored['nonce'] == original['nonce']
    assert stored['consent_granted'] is False
    ingest.assert_not_called()


def test_explicit_google_override_does_not_call_other_provider(app):
    app.extensions.update(
        google_stt_client=provider(''),
        google_ai_client=provider('Unused', 'gemini_audio_stt_live'),
    )
    api._set_asr_override_state('force', 'google', False, 'test')
    with pytest.raises(ValueError, match='live ASR providers unavailable'):
        api._run_speech_to_text({}, 'te', audio_bytes=b'fixture', require_live=True)
    app.extensions['google_ai_client'].transcribe_bytes.assert_not_called()
