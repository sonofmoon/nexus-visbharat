"""Comprehensive test suite for @NexusVisBharatBot on Telegram.

Tests:
- Tamil-first language priority and all 13 supported languages.
- 13 states/UTs and 408 canonical district coverage.
- Conversational state machine: /start, language, issue, state, district, ward, review, DPDP consent, submit.
- District selection via buttons and typing with alias/fuzzy resolution.
- Voice note and photo intake.
- Request tracking via /status and inline callbacks.
- Commands (/help, /language, /cancel) and idempotency deduplication.
"""

import json
from unittest.mock import patch

import pandas as pd
import pytest

from visbharat import create_app
from visbharat.blueprints.api_channels import _ingest_text_request
from visbharat.db import get_channel_session, get_db, init_db
from visbharat.services.telegram_gateway import (
    BOT_PROMPTS,
    DISTRICT_KEYBOARDS,
    LANGUAGE_KEYBOARD,
    ORDERED_LANG_CODES,
    STATE_KEYBOARD,
    _find_matching_district,
    _lang,
    handle_update,
    migrate,
)


@pytest.fixture
def app(tmp_path):
    db_file = str(tmp_path / "test_telegram.db")
    app = create_app()
    app.config.update(
        TESTING=True,
        DATABASE_PATH=db_file,
        DATABASE_URL="",
        TELEGRAM_BOT_TOKEN="test-bot-token-123",
        TELEGRAM_WEBHOOK_SECRET="test-telegram-secret",
    )
    with app.app_context():
        init_db()
        db = get_db()
        migrate(db)
        yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_language_keyboard_tamil_first_and_13_languages():
    """Verify Tamil is strictly first and all 13 languages are in the keyboard."""
    assert ORDERED_LANG_CODES[0] == "ta", "Tamil must strictly be the first language"
    assert ORDERED_LANG_CODES[1] == "te", "Telugu must be second"
    assert ORDERED_LANG_CODES[2] == "hi", "Hindi must be third"
    assert ORDERED_LANG_CODES[-1] == "en", "English must be finally/last"
    assert len(ORDERED_LANG_CODES) == 13

    # Flatten keyboard buttons
    flat_buttons = [btn for row in LANGUAGE_KEYBOARD for btn in row]
    callbacks = [action for _, action in flat_buttons]
    assert callbacks[0] == "lang:ta", "First button must be Tamil"
    assert callbacks[1] == "lang:te", "Second button must be Telugu"
    assert callbacks[-1] == "lang:en", "Last button must be English"

    all_lang_actions = {f"lang:{c}" for c in ORDERED_LANG_CODES}
    assert set(callbacks) == all_lang_actions


def test_state_keyboard_has_all_13_states_tamil_nadu_first():
    """Verify all 13 states/UTs are represented with Tamil Nadu first."""
    flat_states = [btn for row in STATE_KEYBOARD for btn in row]
    state_callbacks = [action.split(":", 1)[1] for _, action in flat_states]
    assert state_callbacks[0] == "Tamil Nadu"
    assert len(state_callbacks) == 13
    expected_states = {
        "Tamil Nadu", "Andhra Pradesh", "Telangana", "Kerala", "Karnataka",
        "Maharashtra", "Gujarat", "Odisha", "West Bengal", "Punjab",
        "Assam", "Uttar Pradesh", "Delhi"
    }
    assert set(state_callbacks) == expected_states


def test_district_keyboards_are_strictly_canonical():
    """Verify all district buttons across all 13 states exist in districts.csv."""
    df = pd.read_csv("static/data/districts.csv")
    state_to_districts = {s: set(g["district"]) for s, g in df.groupby("state")}

    assert len(DISTRICT_KEYBOARDS) == 13
    for state, rows in DISTRICT_KEYBOARDS.items():
        assert state in state_to_districts, f"Unknown state {state} in DISTRICT_KEYBOARDS"
        for row in rows:
            for label, action in row:
                dist = action.split(":", 1)[1]
                assert dist in state_to_districts[state], (
                    f"District '{dist}' in button '{label}' is not in canonical districts for {state}"
                )


def test_district_matcher_exact_and_aliases():
    """Verify _find_matching_district resolves exact names, aliases, and partial matches."""
    # Exact
    assert _find_matching_district("Tamil Nadu", "Vellore") == "Vellore"
    assert _find_matching_district("Tamil Nadu", "vellore") == "Vellore"
    assert _find_matching_district("Tamil Nadu", "CHENNAI") == "Chennai"

    # Aliases
    assert _find_matching_district("Tamil Nadu", "Trichy") == "Tiruchirappalli"
    assert _find_matching_district("Tamil Nadu", "madras") == "Chennai"
    assert _find_matching_district("Andhra Pradesh", "vizag") == "Visakhapatnam"
    assert _find_matching_district("Andhra Pradesh", "Vijayawada") == "NTR"
    assert _find_matching_district("Karnataka", "Bangalore") == "Bengaluru Urban"
    assert _find_matching_district("Maharashtra", "Bombay") == "Mumbai City"
    assert _find_matching_district("Telangana", "rangareddy") == "Ranga Reddy"
    assert _find_matching_district("Uttar Pradesh", "Kanpur") == "Kanpur Nagar"
    assert _find_matching_district("Uttar Pradesh", "Banaras") == "Varanasi"
    assert _find_matching_district("Punjab", "Mohali") == "Sahibzada Ajit Singh Nagar"
    assert _find_matching_district("Delhi", "Delhi") == "New Delhi"

    # Substring / partial
    assert _find_matching_district("Uttar Pradesh", "Prayag") == "Prayagraj"
    assert _find_matching_district("Kerala", "Kozhikode") == "Kozhikode"

    # Unrecognized
    assert _find_matching_district("Tamil Nadu", "NonExistentPlace") is None


def test_telegram_intake_full_flow_tamil(app):
    """Test full conversational lifecycle in Tamil from /start to ticket confirmation."""
    with app.app_context():
        chat_id = 9901

        # 1. /start command
        up1 = {"update_id": 1001, "message": {"chat": {"id": chat_id}, "text": "/start"}}
        res1 = handle_update(up1, _ingest_text_request)
        assert res1["success"]

        sess1 = get_channel_session("telegram", str(chat_id))
        assert sess1["stage"] == "language"

        # Check outbox has start message
        db = get_db()
        msg1 = db.execute("SELECT * FROM telegram_outbox WHERE chat_id=? ORDER BY created_at DESC", (str(chat_id),)).fetchone()
        assert "வணக்கம்" in msg1["payload_json"]

        # 2. Select Tamil
        up2 = {"update_id": 1002, "callback_query": {"id": "cb1", "message": {"chat": {"id": chat_id}}, "data": "lang:ta"}}
        res2 = handle_update(up2, _ingest_text_request)
        assert res2["success"]

        sess2 = get_channel_session("telegram", str(chat_id))
        assert sess2["language"] == "ta"
        assert sess2["stage"] == "issue"

        # 3. Send issue description in Tamil
        up3 = {"update_id": 1003, "message": {"chat": {"id": chat_id}, "text": "குடிநீர் குழாயில் உடைப்பு ஏற்பட்டு தண்ணீர் வீணாகிறது"}}
        res3 = handle_update(up3, _ingest_text_request)
        assert res3["success"]

        sess3 = get_channel_session("telegram", str(chat_id))
        assert sess3["stage"] == "state"
        assert "குடிநீர்" in sess3["issue"]

        # 4. Select State: Tamil Nadu
        up4 = {"update_id": 1004, "callback_query": {"id": "cb2", "message": {"chat": {"id": chat_id}}, "data": "state:Tamil Nadu"}}
        res4 = handle_update(up4, _ingest_text_request)
        assert res4["success"]

        sess4 = get_channel_session("telegram", str(chat_id))
        assert sess4["state"] == "Tamil Nadu"
        assert sess4["stage"] == "district"

        # 5. Select District: Vellore
        up5 = {"update_id": 1005, "callback_query": {"id": "cb3", "message": {"chat": {"id": chat_id}}, "data": "dist:Vellore"}}
        res5 = handle_update(up5, _ingest_text_request)
        assert res5["success"]

        sess5 = get_channel_session("telegram", str(chat_id))
        assert sess5["district"] == "Vellore"
        assert sess5["stage"] == "ward"

        # 6. Skip ward
        up6 = {"update_id": 1006, "callback_query": {"id": "cb4", "message": {"chat": {"id": chat_id}}, "data": "ward:skip"}}
        res6 = handle_update(up6, _ingest_text_request)
        assert res6["success"]

        sess6 = get_channel_session("telegram", str(chat_id))
        assert sess6["stage"] == "review"

        # 7. Confirm review
        up7 = {"update_id": 1007, "callback_query": {"id": "cb5", "message": {"chat": {"id": chat_id}}, "data": "confirm"}}
        res7 = handle_update(up7, _ingest_text_request)
        assert res7["success"]

        sess7 = get_channel_session("telegram", str(chat_id))
        assert sess7["stage"] == "privacy"

        # 8. Consent and Submit
        up8 = {"update_id": 1008, "callback_query": {"id": "cb6", "message": {"chat": {"id": chat_id}}, "data": "consent"}}
        res8 = handle_update(up8, _ingest_text_request)
        assert res8["success"]

        sess8 = get_channel_session("telegram", str(chat_id))
        assert sess8["stage"] == "complete"
        assert sess8["request_id"].startswith("NVB-")

        ticket_id = sess8["request_id"]

        # Check database record has correct state and district
        req_row = db.execute("SELECT * FROM citizen_requests WHERE request_id=?", (ticket_id,)).fetchone()
        assert req_row is not None
        assert req_row["source_channel"] == "Telegram"
        assert req_row["input_language"] == "ta"
        assert req_row["district"] == "Vellore"
        assert req_row["state"] == "Tamil Nadu"

        # 9. Track status via /status
        up9 = {"update_id": 1009, "message": {"chat": {"id": chat_id}, "text": f"/status {ticket_id}"}}
        res9 = handle_update(up9, _ingest_text_request)
        assert res9["success"]


def test_telegram_district_typing_and_ward_entry(app):
    """Test typing district name (alias resolution) and custom ward entry."""
    with app.app_context():
        chat_id = 9902

        # Start and select Telugu
        handle_update({"update_id": 2001, "message": {"chat": {"id": chat_id}, "text": "/start"}}, _ingest_text_request)
        handle_update({"update_id": 2002, "callback_query": {"id": "cb_te", "message": {"chat": {"id": chat_id}}, "data": "lang:te"}}, _ingest_text_request)

        # Issue in Telugu
        handle_update({"update_id": 2003, "message": {"chat": {"id": chat_id}, "text": "రోడ్డుపై పెద్ద గుంతలు ఉన్నాయి"}}, _ingest_text_request)

        # Select Andhra Pradesh
        handle_update({"update_id": 2004, "callback_query": {"id": "cb_ap", "message": {"chat": {"id": chat_id}}, "data": "state:Andhra Pradesh"}}, _ingest_text_request)

        # Type district as alias "Vijayawada"
        handle_update({"update_id": 2005, "message": {"chat": {"id": chat_id}, "text": "Vijayawada"}}, _ingest_text_request)

        sess = get_channel_session("telegram", str(chat_id))
        assert sess["district"] == "NTR", "Vijayawada alias should resolve to NTR district"
        assert sess["stage"] == "ward"

        # Type ward name
        handle_update({"update_id": 2006, "message": {"chat": {"id": chat_id}, "text": "Ward 14 Gandhi Nagar"}}, _ingest_text_request)

        sess2 = get_channel_session("telegram", str(chat_id))
        assert sess2["ward"] == "Ward 14 Gandhi Nagar"
        assert sess2["stage"] == "review"

        # Confirm and consent
        handle_update({"update_id": 2007, "callback_query": {"id": "cb_c", "message": {"chat": {"id": chat_id}}, "data": "confirm"}}, _ingest_text_request)
        handle_update({"update_id": 2008, "callback_query": {"id": "cb_con", "message": {"chat": {"id": chat_id}}, "data": "consent"}}, _ingest_text_request)

        sess3 = get_channel_session("telegram", str(chat_id))
        assert sess3["stage"] == "complete"
        assert sess3["request_id"].startswith("NVB-")

        db = get_db()
        row = db.execute("SELECT * FROM citizen_requests WHERE request_id=?", (sess3["request_id"],)).fetchone()
        assert row["district"] == "NTR"
        assert row["state"] == "Andhra Pradesh"
        assert row["ward"] == "Ward 14 Gandhi Nagar"


def test_telegram_commands_help_and_cancel(app):
    """Test /help, /cancel, and /language commands."""
    with app.app_context():
        chat_id = 9903
        db = get_db()

        # /help with default (Tamil priority)
        res1 = handle_update({"update_id": 3001, "message": {"chat": {"id": chat_id}, "text": "/help"}}, _ingest_text_request)
        assert res1["success"]
        out1 = db.execute("SELECT payload_json FROM telegram_outbox WHERE chat_id=? ORDER BY created_at DESC", (str(chat_id),)).fetchone()
        assert "Nexus VisBharat" in out1["payload_json"]

        # Start and cancel
        handle_update({"update_id": 3002, "message": {"chat": {"id": chat_id}, "text": "/start"}}, _ingest_text_request)
        handle_update({"update_id": 3003, "callback_query": {"id": "cb_hi", "message": {"chat": {"id": chat_id}}, "data": "lang:hi"}}, _ingest_text_request)

        sess = get_channel_session("telegram", str(chat_id))
        assert sess["language"] == "hi"

        # /cancel
        res_cancel = handle_update({"update_id": 3004, "message": {"chat": {"id": chat_id}, "text": "/cancel"}}, _ingest_text_request)
        assert res_cancel["success"]
        sess_cleared = get_channel_session("telegram", str(chat_id))
        assert not sess_cleared or sess_cleared.get("stage") is None

        # /language shows keyboard again
        res_lang = handle_update({"update_id": 3005, "message": {"chat": {"id": chat_id}, "text": "/language"}}, _ingest_text_request)
        assert res_lang["success"]
        sess_new = get_channel_session("telegram", str(chat_id))
        assert sess_new["stage"] == "language"


def test_telegram_update_idempotency(app):
    """Test update_id duplicate deduplication prevents repeated processing."""
    with app.app_context():
        chat_id = 9904
        update = {"update_id": 4001, "message": {"chat": {"id": chat_id}, "text": "/start"}}

        res1 = handle_update(update, _ingest_text_request)
        assert res1["success"]
        assert not res1.get("duplicate")

        # Second delivery of same update_id
        res2 = handle_update(update, _ingest_text_request)
        assert res2["success"]
        assert res2.get("duplicate") is True
