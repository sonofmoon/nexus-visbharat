"""Offline regressions for empty ASR results and Telegram voice recovery."""

from copy import deepcopy
from unittest.mock import Mock

from flask import Flask
import pytest

from visbharat.blueprints import api
from visbharat.services import telegram_gateway as telegram
from visbharat.services.telegram_jobs import process_one
from visbharat.db import get_db, close_db, SQLITE_SCHEMA_SQL


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        JURY_REQUIRE_LIVE_MODELS=False,
        LANGUAGE_ASR_PROVIDER='google',
        LANGUAGE_ASR_STRICT_MODE=False,
        TELEGRAM_BOT_TOKEN='test-token',
        DATABASE_URL='', DATABASE_PATH=':memory:',
    )
    app.teardown_appcontext(close_db)
    with app.app_context():
        get_db().executescript(SQLITE_SCHEMA_SQL)
        telegram.migrate(get_db())
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
    assert process_one(ingest)

    ingest.assert_not_called()
    simulation.assert_not_called()
    assert stored['stage'] == 'issue'
    assert stored['consent_granted'] is False
    assert stored['voice_file_id'] == original['voice_file_id']
    assert notify.call_args.args == ('123', 'asr_error')
    assert all(e['status'] != 'success' for e in app.extensions.get('asr_metrics_store', {}).get('events', []))

    # The retry prompt must actually permit entering replacement text.
    telegram._message({'message': {'text': 'The drain near the school is blocked.'}}, 123, ingest)
    assert stored['issue'] == 'The drain near the school is blocked.'
    assert stored['stage'] == 'location_confirm'
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


def mock_draft(monkeypatch, session):
    stored = deepcopy(session)
    def save(chat, draft):
        stored.clear()
        stored.update(deepcopy(draft))
    monkeypatch.setattr(telegram, '_save', save)
    monkeypatch.setattr(telegram, '_session', lambda chat: deepcopy(stored))
    monkeypatch.setattr(telegram, '_i18n', lambda language, key: key)
    notify = Mock()
    monkeypatch.setattr(telegram, '_text', notify)
    monkeypatch.setattr(telegram, '_send_quick_action', Mock())
    monkeypatch.setattr(telegram, '_queue_callback_answer', Mock())
    return stored, notify


def test_initial_voice_failure_stays_at_issue_and_allows_recording_retry(app, monkeypatch):
    stored, notify = mock_draft(monkeypatch, {
        'stage': 'issue', 'language': 'te', 'nonce': 'retained-nonce',
        'state': 'Andhra Pradesh', 'district': 'Tirupati',
    })
    transcribe = Mock(side_effect=[ValueError('Empty transcript'), 'The drain is blocked.'])
    monkeypatch.setattr(telegram, '_voice_text', transcribe)
    ingest = Mock()
    telegram._message({'message': {'voice': {'file_id': 'first-recording'}}}, 123, ingest)
    assert stored['stage'] == 'transcribing'
    transcribe.assert_not_called()
    assert process_one(ingest)
    assert stored['stage'] == 'issue'
    assert stored['voice_file_id'] == 'first-recording'
    assert stored['issue'] == ''
    assert stored['district'] == 'Tirupati'
    assert stored['consent_granted'] is False
    assert notify.call_args.args == ('123', 'asr_error')
    ingest.assert_not_called()

    telegram._message({'message': {'voice': {'file_id': 'second-recording'}}}, 123, ingest)
    assert process_one(ingest)
    assert stored['stage'] == 'location_confirm'
    assert stored['voice_file_id'] == 'second-recording'
    assert stored['issue'] == 'The drain is blocked.'
    ingest.assert_not_called()


@pytest.mark.parametrize('issue', ['', 'Voice report'])
def test_review_blocks_untranscribed_voice_drafts(app, monkeypatch, issue):
    session = {
        'stage': 'review', 'language': 'te', 'issue': issue,
        'voice_file_id': 'older-recording', 'district': 'Tirupati',
    }
    stored, notify = mock_draft(monkeypatch, session)
    telegram._send_review(123, session)
    assert stored['stage'] == 'issue'
    assert stored['voice_file_id'] == 'older-recording'
    assert stored['consent_granted'] is False
    notify.assert_called_once_with(123, 'asr_error')


def test_late_transcription_requires_review_and_fresh_consent_before_save(app, monkeypatch):
    session = {
        'stage': 'privacy', 'language': 'te', 'issue': 'Voice report',
        'voice_file_id': 'older-recording', 'state': 'Andhra Pradesh',
        'district': 'Tirupati', 'consent_granted': True, 'nonce': 'retained-nonce',
    }
    stored, notify = mock_draft(monkeypatch, session)
    transcript = 'మా వీధిలో మురుగు కాలువ మూసుకుపోయింది.'
    transcribe = Mock(return_value=transcript)
    monkeypatch.setattr(telegram, '_voice_text', transcribe)
    ingest = Mock(return_value=({'success': True, 'request_id': 'NVB-TEST'}, None))

    telegram._submit(123, session, ingest)
    assert process_one(ingest)

    ingest.assert_not_called()
    assert stored['stage'] == 'location_confirm'
    assert stored['issue'] == transcript
    assert stored['consent_granted'] is False
    telegram._callback({'callback_query': {'data': 'location:confirm'}}, 123, ingest)
    assert transcript in notify.call_args.args[1]

    telegram._callback({'callback_query': {'data': 'confirm'}}, 123, ingest)
    assert stored['stage'] == 'privacy'
    ingest.assert_not_called()
    telegram._callback({'callback_query': {'data': 'consent'}}, 123, ingest)
    ingest.assert_not_called()
    assert process_one(ingest)
    ingest.assert_called_once()
    assert ingest.call_args.kwargs['text'] == transcript
    assert stored['stage'] == 'complete'
    transcribe.assert_called_once()
