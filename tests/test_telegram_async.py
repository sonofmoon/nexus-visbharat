"""Durability, fast webhook, consent and delivery regressions; no provider calls."""
import json
from unittest.mock import Mock

import pytest

from tests.test_speech_fallback import app
from visbharat.db import get_db
from visbharat.services import telegram_gateway as bot, telegram_jobs as jobs


def update(number, text=None, action=None, voice=None, chat=123):
    if action is not None:
        return {'update_id': number, 'callback_query': {'id': str(number), 'data': action, 'message': {'chat': {'id': chat}}}}
    message = {'chat': {'id': chat}}
    if text is not None:
        message['text'] = text
    if voice:
        message['voice'] = {'file_id': voice}
    return {'update_id': number, 'message': message}


def reviewed():
    return dict(stage='review', language='te', nonce='fixture-nonce', issue='A blocked drain near the school.',
                state='Andhra Pradesh', district='Tirupati', consent_granted=False)


def save_ticket(**kwargs):
    rid = kwargs['reserved_request_id']
    db = get_db()
    db.execute('''INSERT INTO citizen_requests
        (request_id,source_channel,input_language,district,state,lat,lng,original_text,translated_text,
         category,urgency,sentiment,status,submitted_by,ai_metadata_json,created_at)
        VALUES (?,'Telegram','te','Tirupati','Andhra Pradesh',0,0,?,?,'Sanitation','Urgent','Neutral','New',?,'{}',?)''',
        (rid, kwargs['text'], kwargs['text'], kwargs['sender'], bot._now()))
    db.commit()
    return {'success': True, 'request_id': rid}, None


def test_webhook_enqueues_voice_without_speech_or_delivery(app, monkeypatch):
    from visbharat.blueprints.api_channels import channels_bp
    app.register_blueprint(channels_bp)
    app.config['TELEGRAM_WEBHOOK_SECRET'] = 'test-secret'
    bot._save(123, dict(stage='issue', language='te', nonce='voice-nonce'))
    speech = Mock(side_effect=AssertionError('Speech must not run in webhook'))
    delivery = Mock(side_effect=AssertionError('Delivery must not run in webhook'))
    monkeypatch.setattr(bot, '_voice_text', speech)
    monkeypatch.setattr(bot.requests, 'post', delivery)
    response = app.test_client().post('/api/channels/telegram/webhook',
        headers={'X-Telegram-Bot-Api-Secret-Token': 'test-secret'}, json=update(1, voice='recording'))
    assert response.status_code == 200
    assert bot._session(123)['stage'] == 'transcribing'
    assert get_db().execute('SELECT COUNT(*) AS n FROM telegram_jobs').fetchone()['n'] == 1
    speech.assert_not_called()
    delivery.assert_not_called()


def test_location_and_combined_consent_then_durable_reference(app):
    ingest = Mock(side_effect=AssertionError('Web path must not perform intake'))
    for item in [update(1, text='/start'), update(2, action='lang:te'),
                 update(3, text='తిరుపతి మా వీధిలో కాలువ మూసుకుపోయింది.')]:
        bot.handle_update(item, ingest)
    assert bot._session(123)['stage'] == 'location_confirm'
    assert not get_db().execute('SELECT * FROM telegram_jobs').fetchone()
    bot.handle_update(update(4, action='location:confirm'), ingest)
    assert bot._session(123)['stage'] == 'review'
    out = get_db().execute("SELECT payload_json FROM telegram_outbox WHERE method='sendMessage' ORDER BY created_at DESC").fetchone()
    buttons = json.loads(out['payload_json'])['reply_markup']['inline_keyboard']
    assert any(b['callback_data'].endswith(':consent') for row in buttons for b in row)
    bot.handle_update(update(5, action='consent'), ingest)
    session = bot._session(123)
    assert session['stage'] == 'processing'
    assert session['request_id'].startswith('NVB-')
    assert not get_db().execute('SELECT * FROM citizen_requests').fetchone()
    assert jobs.process_one(save_ticket)
    assert bot._session(123)['request_id'] == session['request_id']
    assert bot._session(123)['stage'] == 'complete'
    ingest.assert_not_called()


def test_remembers_language_and_suggests_previous_location(app):
    bot._save(123, {**reviewed(), 'stage': 'complete'})
    bot.handle_update(update(1, text='/start'), Mock())
    assert bot._session(123)['stage'] == 'issue'
    bot.handle_update(update(2, text='A drain is blocked.'), Mock())
    assert bot._session(123)['stage'] == 'location_confirm'
    assert bot._session(123)['suggested_district'] == 'Tirupati'
    bot.handle_update(update(3, text='/language'), Mock())
    assert bot._session(123)['stage'] == 'language'


def test_ambiguous_locations_require_manual_selection(app):
    bot._save(123, {**reviewed(), 'stage': 'issue'})
    bot.handle_update(update(1, text='Water problems in Vellore and Tirupati.'), Mock())
    assert bot._session(123)['stage'] == 'state'


def test_duplicate_consent_creates_one_job_and_one_ticket(app):
    bot._save(123, reviewed())
    event = update(1, action='consent')
    bot.handle_update(event, Mock())
    assert bot.handle_update(event, Mock())['duplicate']
    bot.handle_update(update(2, action='consent'), Mock())
    assert get_db().execute('SELECT COUNT(*) AS n FROM telegram_jobs').fetchone()['n'] == 1
    assert jobs.process_one(save_ticket)
    assert jobs.process_one(save_ticket) is False
    assert get_db().execute('SELECT COUNT(*) AS n FROM citizen_requests').fetchone()['n'] == 1


def test_worker_restart_after_ticket_save_reuses_reserved_id(app):
    bot._save(123, reviewed())
    bot.handle_update(update(1, action='consent'), Mock())
    def crash_after_save(**kwargs):
        save_ticket(**kwargs)
        raise KeyboardInterrupt('Simulated process death')
    with pytest.raises(KeyboardInterrupt):
        jobs.process_one(crash_after_save)
    get_db().execute('UPDATE telegram_jobs SET lease_until=0')
    get_db().commit()
    never_repeat = Mock(side_effect=AssertionError('Do not rerun saved intake'))
    assert jobs.process_one(never_repeat)
    never_repeat.assert_not_called()
    assert bot._session(123)['stage'] == 'complete'
    assert get_db().execute('SELECT COUNT(*) AS n FROM citizen_requests').fetchone()['n'] == 1


def test_expired_worker_is_fenced_from_job_completion(app):
    bot._save(123, reviewed())
    bot.handle_update(update(1, action='consent'), Mock())
    first = jobs._claim()
    assert jobs._claim() is None
    get_db().execute('UPDATE telegram_jobs SET lease_until=0')
    get_db().commit()
    second = jobs._claim()
    jobs._finish(first, 'done')
    row = get_db().execute('SELECT * FROM telegram_jobs').fetchone()
    assert row['lease_owner'] == second['lease_owner']
    assert row['status'] == 'processing'


def test_cancelled_voice_cannot_overwrite_new_draft(app, monkeypatch):
    bot._save(123, {**reviewed(), 'stage': 'issue'})
    bot.handle_update(update(1, voice='recording'), Mock())
    def transcribe_then_cancel(*args):
        bot.handle_update(update(2, text='/cancel'), Mock())
        bot.handle_update(update(3, text='/start'), Mock())
        return 'Old transcript'
    monkeypatch.setattr(bot, '_voice_text', transcribe_then_cancel)
    assert jobs.process_one()
    assert bot._session(123)['stage'] == 'language'
    assert 'issue' not in bot._session(123)
    assert get_db().execute('SELECT status FROM telegram_jobs').fetchone()['status'] == 'cancelled'


def test_intake_failure_retains_reference_and_stops_after_three_attempts(app):
    bot._save(123, reviewed())
    bot.handle_update(update(1, action='consent'), Mock())
    reference = bot._session(123)['request_id']
    failure = Mock(side_effect=RuntimeError('private provider error'))
    for _ in range(3):
        assert jobs.process_one(failure)
        get_db().execute('UPDATE telegram_jobs SET available_at=0')
        get_db().commit()
    assert not jobs.process_one(failure)
    row = get_db().execute('SELECT * FROM telegram_jobs').fetchone()
    assert row['status'] == 'failed'
    assert row['request_id'] == reference
    assert row['error_type'] == 'RuntimeError'
    assert bot._session(123)['issue'] == reviewed()['issue']


def test_enqueue_and_consent_roll_back_together(app, monkeypatch):
    bot._save(123, reviewed())
    original = bot._text
    def fail_notification(*args, **kwargs):
        if str(kwargs.get('key', '')).startswith('job:'):
            raise RuntimeError('Database interruption')
        return original(*args, **kwargs)
    monkeypatch.setattr(bot, '_text', fail_notification)
    with pytest.raises(RuntimeError):
        bot.handle_update(update(1, action='consent'), Mock())
    assert not get_db().execute('SELECT * FROM telegram_jobs').fetchone()
    assert not get_db().execute('SELECT * FROM telegram_inbound_events').fetchone()
    assert bot._session(123)['consent_granted'] is False


def test_stale_buttons_cannot_confirm_new_draft(app):
    bot._save(123, reviewed())
    bot.handle_update(update(1, action='n:old-nonce:consent'), Mock())
    assert not get_db().execute('SELECT * FROM telegram_jobs').fetchone()
    assert bot._session(123)['stage'] == 'review'


def test_pending_reference_is_private_to_telegram_chat(app):
    bot._save(123, reviewed())
    bot.handle_update(update(1, action='consent'), Mock())
    rid = bot._session(123)['request_id']
    bot.handle_update(update(2, text='/status '+rid, chat=999), Mock())
    payload = json.loads(get_db().execute("SELECT payload_json FROM telegram_outbox WHERE chat_id='999'").fetchone()['payload_json'])
    assert rid not in payload['text']
    assert 'Tirupati' not in payload['text']


def test_outbox_deduplicates_and_recovers_expired_send(app, monkeypatch):
    bot._text(123, 'One receipt', key='receipt:one')
    bot._text(123, 'One receipt', key='receipt:one')
    get_db().execute("UPDATE telegram_outbox SET status='sending',lease_until_epoch=0,lease_owner='crashed'")
    get_db().commit()
    post = Mock(return_value=Mock(ok=True, json=lambda: {'ok': True}))
    monkeypatch.setattr(bot.requests, 'post', post)
    assert bot.dispatch_outbox()['sent'] == 1
    assert bot.dispatch_outbox()['sent'] == 0
    post.assert_called_once()


def test_dead_delivery_is_visible_and_does_not_store_secret_errors(app, monkeypatch):
    app.config['TELEGRAM_OUTBOX_MAX_ATTEMPTS'] = 1
    bot._text(123, 'Receipt', key='receipt:one')
    monkeypatch.setattr(bot.requests, 'post', Mock(side_effect=RuntimeError('secret-token-in-provider-url')))
    assert bot.dispatch_outbox()['failed'] == 1
    row = get_db().execute('SELECT * FROM telegram_outbox').fetchone()
    assert row['status'] == 'dead'
    assert row['last_error'] == 'RuntimeError'


def test_health_exposes_missing_workers_backlog_and_recent_heartbeats(app):
    assert bot.health()['dedicated_workers_recent'] is False
    bot._save(123, reviewed())
    bot.handle_update(update(1, action='consent'), Mock())
    jobs.heartbeat('jobs')
    jobs.heartbeat('outbox')
    status = bot.health()
    assert status['dedicated_workers_recent'] is True
    assert status['jobs_by_status'] == {'queued': 1}
    assert status['oldest_pending_job_at']
    assert status['outbox_pending'] > 0


def test_callback_has_one_ack_and_does_not_call_telegram_inline(app, monkeypatch):
    bot._save(123, {'stage': 'language', 'nonce': 'fixture'})
    post = Mock(side_effect=AssertionError('No inline outbound calls'))
    monkeypatch.setattr(bot.requests, 'post', post)
    bot.handle_update(update(1, action='lang:te'), Mock())
    rows = get_db().execute("SELECT * FROM telegram_outbox WHERE method='answerCallbackQuery'").fetchall()
    assert len(rows) == 1
    post.assert_not_called()


def test_resending_same_audio_after_edit_creates_a_new_processing_attempt(app, monkeypatch):
    bot._save(123, {**reviewed(), 'stage': 'issue'})
    speech = Mock(return_value='Drain blocked near school.')
    monkeypatch.setattr(bot, '_voice_text', speech)
    bot.handle_update(update(1, voice='same-file'), Mock())
    assert jobs.process_one()
    bot.handle_update(update(2, action='location:confirm'), Mock())
    bot.handle_update(update(3, action='edit'), Mock())
    bot.handle_update(update(4, voice='same-file'), Mock())
    assert jobs.process_one()
    assert speech.call_count == 2
    assert bot._session(123)['stage'] == 'location_confirm'


def test_voice_completion_does_not_overwrite_an_edited_session(app, monkeypatch):
    bot._save(123, {**reviewed(), 'stage': 'issue'})
    bot.handle_update(update(1, voice='old-file'), Mock())
    def stale_result(*args):
        session = bot._session(123)
        session.update(stage='review', issue='New corrected text')
        bot._save(123, session)
        return 'Old transcript'
    monkeypatch.setattr(bot, '_voice_text', stale_result)
    assert jobs.process_one()
    assert bot._session(123)['issue'] == 'New corrected text'
    assert get_db().execute('SELECT status FROM telegram_jobs').fetchone()['status'] == 'cancelled'
