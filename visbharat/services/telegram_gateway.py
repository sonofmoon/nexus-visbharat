"""Durable Telegram webhook gateway.

Telegram delivers an update at least once.  This module keeps the inbound
update ledger and outbound send queue in SQL so a web process restart does not
lose a citizen draft or create a second ticket.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import requests
from flask import current_app

from ..db import delete_channel_session, get_channel_session, get_db, save_channel_session

LOGGER = logging.getLogger(__name__)
STRINGS = json.loads(
    (Path(__file__).resolve().parents[2] / "static/data/assistant_i18n.json").read_text(encoding="utf-8-sig")
)
LANGUAGES = {lang: STRINGS[lang] for lang in ("en", "ta", "te")}

STATE_KEYBOARD = [
    [("Tamil Nadu", "state:Tamil Nadu"), ("Andhra Pradesh", "state:Andhra Pradesh")],
    [("Telangana", "state:Telangana")],
]

DISTRICT_KEYBOARDS = {
    "Tamil Nadu": [
        [("Vellore", "dist:Vellore"), ("Ranipet", "dist:Ranipet")],
        [("Tirupathur", "dist:Tirupathur"), ("Chennai", "dist:Chennai")],
        [("Coimbatore", "dist:Coimbatore"), ("Salem", "dist:Salem")],
    ],
    "Andhra Pradesh": [
        [("Tirupati", "dist:Tirupati"), ("Chittoor", "dist:Chittoor")],
        [("Visakhapatnam", "dist:Visakhapatnam"), ("Guntur", "dist:Guntur")],
    ],
    "Telangana": [
        [("Hyderabad", "dist:Hyderabad"), ("Warangal", "dist:Warangal")],
        [("Medchal", "dist:Medchal-Malkajgiri"), ("Karimnagar", "dist:Karimnagar")],
    ],
}


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def migrate(db):
    """Install the Telegram delivery ledger on both SQLite and PostgreSQL."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS telegram_inbound_events (
            update_id TEXT PRIMARY KEY,
            payload_hash TEXT NOT NULL,
            chat_id TEXT,
            status TEXT NOT NULL,
            response_json TEXT,
            received_at TEXT NOT NULL,
            lease_until_epoch INTEGER,
            processed_at TEXT
        )"""
    )
    if getattr(db, 'backend', 'sqlite') == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'telegram_inbound_events'"
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute("PRAGMA table_info(telegram_inbound_events)").fetchall()
        }
    if 'lease_until_epoch' not in cols:
        db.execute("ALTER TABLE telegram_inbound_events ADD COLUMN lease_until_epoch INTEGER")
    db.execute(
        """CREATE TABLE IF NOT EXISTS telegram_outbox (
            message_key TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            method TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at_epoch INTEGER NOT NULL,
            lease_until_epoch INTEGER,
            last_error TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_outbox_due ON telegram_outbox(status, next_attempt_at_epoch)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_inbound_chat ON telegram_inbound_events(chat_id, received_at)")
    db.commit()


def _token():
    configured = current_app.config.get("TELEGRAM_BOT_TOKEN")
    return str(configured if configured is not None else os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def _api_url(method):
    token = _token()
    return f"https://api.telegram.org/bot{token}/{method}" if token else ""


def _keyboard(rows):
    return {"inline_keyboard": [[{"text": label, "callback_data": action} for label, action in row] for row in rows]}


def _queue(message_key, chat_id, method, payload):
    db = get_db()
    now = int(time.time())
    now_iso = _now()
    db.execute(
        """INSERT INTO telegram_outbox
           (message_key, chat_id, method, payload_json, status, attempts,
            next_attempt_at_epoch, lease_until_epoch, last_error, sent_at,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, 'queued', 0, ?, NULL, NULL, NULL, ?, ?)
           ON CONFLICT(message_key) DO NOTHING""",
        (str(message_key), str(chat_id), method, json.dumps(payload, ensure_ascii=False), now, now_iso, now_iso),
    )
    db.commit()


def _queue_text(chat_id, text, reply_markup=None, message_key=None):
    payload = {"chat_id": chat_id, "text": str(text or "")[:4096]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    # Repeated prompts (for example /help or a missing district) are valid
    # separate replies; deduplication belongs to the inbound update ledger.
    _queue(message_key or f"chat:{chat_id}:message:{uuid4().hex}",
           chat_id, "sendMessage", payload)


def _queue_callback_answer(callback_id, message_key):
    if not callback_id:
        return
    _queue(message_key, "callback", "answerCallbackQuery", {"callback_query_id": callback_id})


def dispatch_outbox(limit=None):
    """Try due Telegram sends; failures remain queued for the next worker run."""
    token = _token()
    if not token:
        return {"sent": 0, "failed": 0, "pending": _pending_count()}

    max_items = int(limit or current_app.config.get("TELEGRAM_OUTBOX_BATCH_SIZE", 20) or 20)
    max_attempts = int(current_app.config.get("TELEGRAM_OUTBOX_MAX_ATTEMPTS", 12) or 12)
    sent = failed = 0
    db = get_db()
    now = int(time.time())
    for _ in range(max_items):
        row = db.execute(
            """SELECT * FROM telegram_outbox
               WHERE (status='queued' OR status='failed')
                 AND next_attempt_at_epoch<=?
                 AND (lease_until_epoch IS NULL OR lease_until_epoch<?)
                 AND attempts<?
               ORDER BY created_at LIMIT 1""",
            (now, now, max_attempts),
        ).fetchone()
        if not row:
            break
        key = row["message_key"]
        lease_until = now + 30
        db.execute(
            """UPDATE telegram_outbox SET status='sending', lease_until_epoch=?, updated_at=?
               WHERE message_key=? AND (status='queued' OR status='failed')
                 AND (lease_until_epoch IS NULL OR lease_until_epoch<?)""",
            (lease_until, _now(), key, now),
        )
        db.commit()
        claim = db.execute("SELECT status, lease_until_epoch FROM telegram_outbox WHERE message_key=?", (key,)).fetchone()
        if not claim or claim['status'] != 'sending' or int(claim['lease_until_epoch'] or 0) != lease_until:
            continue

        try:
            response = requests.post(_api_url(row["method"]), json=json.loads(row["payload_json"]), timeout=8)
            body = response.json()
            if not response.ok or not body.get("ok"):
                desc = str(body.get("description") or "").lower()
                if row["method"] == "answerCallbackQuery" and ("too old" in desc or "invalid" in desc or "query id" in desc):
                    db.execute(
                        """UPDATE telegram_outbox SET status='sent', sent_at=?, lease_until_epoch=NULL,
                           last_error=NULL, updated_at=? WHERE message_key=?""",
                        (_now(), _now(), key),
                    )
                    db.commit()
                    continue
                raise RuntimeError(f"Telegram API rejected {row['method']} ({response.status_code}): {body.get('description')}")
            db.execute(
                """UPDATE telegram_outbox SET status='sent', sent_at=?, lease_until_epoch=NULL,
                   last_error=NULL, updated_at=? WHERE message_key=?""",
                (_now(), _now(), key),
            )
            db.commit()
            sent += 1
        except Exception as exc:
            attempts = int(row["attempts"] or 0) + 1
            delay = min(900, 2 ** min(attempts, 8))
            db.execute(
                """UPDATE telegram_outbox SET status='failed', attempts=?, next_attempt_at_epoch=?,
                   lease_until_epoch=NULL, last_error=?, updated_at=? WHERE message_key=?""",
                (attempts, int(time.time()) + delay, str(exc)[:500], _now(), key),
            )
            db.commit()
            failed += 1
            LOGGER.warning("Telegram outbound delivery failed for %s: %s", key, type(exc).__name__)
    return {"sent": sent, "failed": failed, "pending": _pending_count()}


def _pending_count():
    try:
        row = get_db().execute("SELECT COUNT(*) AS n FROM telegram_outbox WHERE status IN ('queued','failed')").fetchone()
        return int(row["n"] or 0)
    except Exception:
        return 0


def health():
    db = get_db()
    inbound = db.execute("SELECT COUNT(*) AS n FROM telegram_inbound_events").fetchone()
    pending = db.execute("SELECT COUNT(*) AS n FROM telegram_outbox WHERE status IN ('queued','failed')").fetchone()
    return {
        "token_configured": bool(_token()),
        "webhook_secret_configured": bool((current_app.config.get("TELEGRAM_WEBHOOK_SECRET") or "").strip()),
        "inbound_events": int(inbound["n"] or 0),
        "outbox_pending": int(pending["n"] or 0),
    }


def _session(chat_id):
    session = get_channel_session("telegram", str(chat_id))
    return session if isinstance(session, dict) and session.get("stage") else {"stage": "language", "nonce": uuid4().hex[:12]}


def _save(chat_id, session):
    save_channel_session("telegram", str(chat_id), session)


def _clear(chat_id):
    delete_channel_session("telegram", str(chat_id))


def _lang(session):
    value = str(session.get("language") or "en").lower()
    return value if value in LANGUAGES else "en"


def _text(chat_id, text, reply_markup=None, key=None):
    _queue_text(chat_id, text, reply_markup, key)


def _start(chat_id):
    session = {"stage": "language", "nonce": uuid4().hex[:12]}
    _save(chat_id, session)
    _text(chat_id, "Welcome to Nexus VisBharat. Choose your language:", _keyboard([[('Tamil', 'lang:ta'), ('Telugu', 'lang:te'), ('English', 'lang:en')]]), f"chat:{chat_id}:start:{session['nonce']}")
    return session


def _help(chat_id):
    _text(chat_id, "Choose Tamil, Telugu or English, describe an infrastructure issue, provide the district, review the draft, and consent before submitting. Use /status NVB-XXXXXXXXXXXX to track a saved request.")


def _status(chat_id, ticket):
    row = get_db().execute("SELECT status, district FROM citizen_requests WHERE request_id=?", (ticket.upper(),)).fetchone()
    if not row:
        _text(chat_id, f"No request matches ticket {ticket.upper()}.")
        return
    _text(chat_id, f"Ticket ID: {ticket.upper()}\nDistrict: {row['district']}\nStatus: {row['status']}")


def _voice_text(file_id, language):
    """Download and transcribe a Telegram voice note through the existing ASR path."""
    if not _token():
        raise ValueError("Telegram bot token is not configured")
    meta = requests.get(_api_url("getFile"), params={"file_id": file_id}, timeout=10).json()
    path = (meta.get("result") or {}).get("file_path")
    if not path:
        raise ValueError("Telegram voice file is unavailable")
    raw = requests.get(f"https://api.telegram.org/file/bot{_token()}/{path}", timeout=30).content
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise ValueError("Telegram voice file is empty or too large")
    from ..blueprints.api import _run_speech_to_text
    result = _run_speech_to_text({"language": language}, language, audio_bytes=raw, mime_type="audio/ogg")
    transcript = result.get("transcript") or result.get("text") or result.get("transcription")
    if not transcript:
        raise ValueError("Speech-to-text returned an empty transcript")
    return str(transcript).strip()


def _submit(chat_id, session, ingest_text):
    language = _lang(session)
    issue = str(session.get("issue") or "").strip()
    if session.get("voice_file_id") and (not issue or issue == "Voice report"):
        try:
            issue = _voice_text(session["voice_file_id"], language)
        except Exception as exc:
            LOGGER.exception("Failed to transcribe Telegram voice note for chat %s: %s", chat_id, exc)
            _text(chat_id, "I could not transcribe that voice note. Please send it again or type the report; your draft is retained.")
            return
    if not issue:
        _text(chat_id, LANGUAGES[language]["issue"])
        return
    district = session.get("district") or session.get("location") or "Vellore"
    payload, error = ingest_text(
        channel="Telegram",
        text=issue,
        language=language,
        district=district,
        sender=str(chat_id),
        endpoint="/api/channels/telegram/conversation",
        idempotency_key=f"telegram:{chat_id}:{session.get('nonce', 'unknown')}",
    )
    if error or not payload.get("success"):
        _text(chat_id, "The service is temporarily unavailable. Your draft is retained; please try again.")
        return
    request_id = payload.get("request_id")
    if request_id:
        session.update(stage="complete", request_id=request_id, issue=issue)
        _save(chat_id, session)
        _text(chat_id, LANGUAGES[language]["saved"].format(ticket=request_id), key=f"chat:{chat_id}:saved:{request_id}")
    else:
        _text(chat_id, "Your report was accepted for processing. Please try /status again shortly.")


def _callback(update, chat_id, ingest_text):
    callback = update.get("callback_query") or {}
    action = str(callback.get("data") or "")
    _queue_callback_answer(callback.get("id"), f"callback:{callback.get('id') or hashlib.sha256(action.encode()).hexdigest()}")
    session = _session(chat_id)
    if action.startswith("lang:") and action[5:] in LANGUAGES:
        language = action[5:]
        session.update(language=language, stage="issue")
        _save(chat_id, session)
        _text(chat_id, LANGUAGES[language]["welcome"])
    elif action.startswith("state:"):
        state = action[6:]
        session.update(state=state, stage="district")
        _save(chat_id, session)
        keyboard = DISTRICT_KEYBOARDS.get(state, DISTRICT_KEYBOARDS["Tamil Nadu"])
        _text(chat_id, f"State: {state}\n\nPlease select your District:", _keyboard(keyboard))
    elif action.startswith("dist:"):
        district = action[5:]
        session.update(district=district, location=district, stage="ward")
        _save(chat_id, session)
        _text(chat_id, f"District: {district}\n\nPlease enter your local Ward, Village or Area name (or type 'Skip'):")
    elif action == "confirm" and session.get("stage") == "review":
        session["stage"] = "privacy"
        _save(chat_id, session)
        language = _lang(session)
        _text(chat_id, LANGUAGES[language]["notice"] + "\n\n" + LANGUAGES[language]["consent"], _keyboard([[('I consent', 'consent')], [('Cancel', 'cancel')]]))
    elif action == "consent" and session.get("stage") == "privacy":
        session["consent_granted"] = True
        _save(chat_id, session)
        _submit(chat_id, session, ingest_text)
    elif action == "edit":
        session["stage"] = "issue"
        _save(chat_id, session)
        _text(chat_id, LANGUAGES[_lang(session)]["issue"])
    elif action == "cancel":
        _clear(chat_id)
        _text(chat_id, "Draft cancelled. No report was submitted.")


def _message(update, chat_id, ingest_text):
    message = update.get("message") or update.get("edited_message") or {}
    text = str(message.get("text") or "").strip()
    if text.startswith("/start"):
        _start(chat_id)
        return
    if text.startswith("/help"):
        _help(chat_id)
        return
    if text.startswith("/status"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            _text(chat_id, "Please provide a ticket ID. Example: /status NVB-20260924ABC1")
        else:
            _status(chat_id, parts[1].strip())
        return

    session = _session(chat_id)
    stage = session.get("stage")
    if stage == "language":
        _start(chat_id)
    elif stage == "issue":
        voice_id = (message.get("voice") or {}).get("file_id")
        if voice_id:
            try:
                transcription = _voice_text(voice_id, _lang(session))
                session.update(issue=transcription, voice_file_id=voice_id, stage="state")
            except Exception as exc:
                LOGGER.warning("Immediate voice transcription failed: %s; using placeholder", exc)
                session.update(issue="Voice report", voice_file_id=voice_id, stage="state")
        elif text:
            session.update(issue=text, stage="state")
        else:
            _text(chat_id, LANGUAGES[_lang(session)]["issue"])
            return
        _save(chat_id, session)
        _text(chat_id, "Please select your State:", _keyboard(STATE_KEYBOARD))
    elif stage == "state":
        _text(chat_id, "Please select your State using the buttons below:", _keyboard(STATE_KEYBOARD))
    elif stage == "district":
        state = session.get("state", "Tamil Nadu")
        keyboard = DISTRICT_KEYBOARDS.get(state, DISTRICT_KEYBOARDS["Tamil Nadu"])
        _text(chat_id, f"Please select your District ({state}) using the buttons below:", _keyboard(keyboard))
    elif stage == "ward":
        ward = "" if text.lower() == "skip" else text
        session.update(ward=ward, stage="review")
        _save(chat_id, session)
        language = _lang(session)
        state = session.get("state", "Tamil Nadu")
        district = session.get("district", "Vellore")
        loc_display = f"{district}, {state}" + (f" (Ward / Area: {ward})" if ward else "")
        review = f"{LANGUAGES[language]['review']}\n\nIssue: {session.get('issue', '')}\nLocation: {loc_display}"
        _text(chat_id, review, _keyboard([[('Confirm & submit', 'confirm'), ('Edit report', 'edit')], [('Cancel', 'cancel')]]))
    elif stage == "location":
        session.update(location=text, district="Vellore", state="Tamil Nadu", stage="review")
        _save(chat_id, session)
        language = _lang(session)
        review = f"{LANGUAGES[language]['review']}\n\nIssue: {session.get('issue', '')}\nLocation: {text}"
        _text(chat_id, review, _keyboard([[('Confirm & submit', 'confirm'), ('Edit report', 'edit')], [('Cancel', 'cancel')]]))
    elif stage in {"review", "privacy"}:
        _text(chat_id, "Please use the buttons above to confirm, edit, or cancel.")
    else:
        _text(chat_id, "This report is already registered. Use /status <ticket ID>, or /start for a new report.")


def handle_update(update, ingest_text):
    """Process one Telegram update and return a safe acknowledgement payload."""
    if not isinstance(update, dict):
        raise ValueError("Telegram update must be an object")
    raw = json.dumps(update, sort_keys=True, ensure_ascii=False).encode("utf-8")
    update_id = str(update.get("update_id") or "legacy-" + hashlib.sha256(raw).hexdigest()[:32])
    payload_hash = hashlib.sha256(raw).hexdigest()
    chat_id = None
    if update.get("callback_query"):
        chat_id = ((update["callback_query"].get("message") or {}).get("chat") or {}).get("id")
    else:
        message = update.get("message") or update.get("edited_message") or {}
        chat_id = (message.get("chat") or {}).get("id") or (message.get("from") or {}).get("id")

    db = get_db()
    existing = db.execute("SELECT payload_hash, response_json, status, lease_until_epoch FROM telegram_inbound_events WHERE update_id=?", (update_id,)).fetchone()
    if existing:
        if existing["payload_hash"] != payload_hash:
            raise ValueError("Telegram update ID was reused for different content")
        if existing["response_json"]:
            response = json.loads(existing["response_json"] or "{}")
            response["duplicate"] = True
            return response
        if int(existing["lease_until_epoch"] or 0) > int(time.time()):
            return {"success": True, "channel": "Telegram", "event_id": update_id, "duplicate": True, "processing": True}
        db.execute(
            "UPDATE telegram_inbound_events SET status='processing', lease_until_epoch=?, received_at=? WHERE update_id=?",
            (int(time.time()) + 30, _now(), update_id),
        )
        db.commit()
    else:
        db.execute(
            """INSERT INTO telegram_inbound_events(update_id,payload_hash,chat_id,status,response_json,received_at,lease_until_epoch)
               VALUES(?,?,?,'processing',NULL,?,?)""",
            (update_id, payload_hash, str(chat_id) if chat_id is not None else None, _now(), int(time.time()) + 30),
        )
        db.commit()

    if chat_id is not None:
        if update.get("callback_query"):
            _callback(update, chat_id, ingest_text)
        else:
            _message(update, chat_id, ingest_text)
    response = {"success": True, "channel": "Telegram", "event_id": update_id, "outbox_pending": _pending_count()}
    db.execute(
        "UPDATE telegram_inbound_events SET status='processed', response_json=?, lease_until_epoch=NULL, processed_at=? WHERE update_id=?",
        (json.dumps(response), _now(), update_id),
    )
    db.commit()
    return response
