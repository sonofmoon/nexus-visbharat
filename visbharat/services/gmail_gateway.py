"""Enterprise Gmail mailbox intake for NVB.

Gmail ``watch`` notifications contain a mailbox history cursor, not the full
email.  This service advances that cursor, fetches newly-added Inbox messages,
deduplicates them by Gmail message ID, submits them to the existing intake
pipeline, and optionally sends a ticket confirmation through Gmail API.
"""

import base64
import hashlib
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr

import requests
from flask import current_app

from ..db import get_db

LOGGER = logging.getLogger(__name__)
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users"


class GmailTransientError(RuntimeError):
    """A Pub/Sub delivery should be retried for this error."""


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _secret(name, fallback=""):
    value = str(fallback or "").strip()
    if not bool(current_app.config.get("GMAIL_SECRET_PROVIDER_ENABLED", current_app.config.get("NOTIFICATION_SECRET_PROVIDER_ENABLED", False))):
        return value
    provider = current_app.extensions.get("secret_provider")
    if provider is None:
        return value
    prefix = str(current_app.config.get("GMAIL_SECRET_PROVIDER_PREFIX", "visbharat/gmail") or "visbharat/gmail").strip().strip("/")
    full_name = f"{prefix}/{name}" if prefix else name
    try:
        if callable(provider):
            resolved = provider(full_name)
        elif hasattr(provider, "get_secret"):
            resolved = provider.get_secret(full_name)
        else:
            resolved = ""
        return str(resolved or "").strip() or value
    except Exception:
        return value


def _mailbox():
    return str(current_app.config.get("GMAIL_MAILBOX") or "").strip()


def _topic():
    return str(current_app.config.get("GMAIL_PUBSUB_TOPIC") or "").strip()


def _decode_b64(value):
    try:
        return base64.urlsafe_b64decode(str(value or "") + "=" * (-len(str(value or "")) % 4))
    except Exception:
        return b""


def _decode_header(value):
    try:
        return str(make_header(decode_header(str(value or ""))))
    except Exception:
        return str(value or "")


def _strip_html(value):
    clean = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", str(value or ""))
    clean = re.sub(r"(?s)<[^>]+>", " ", clean)
    return re.sub(r"\s+", " ", html.unescape(clean)).strip()


def _headers(payload):
    return {
        str(item.get("name") or "").lower(): _decode_header(item.get("value"))
        for item in (payload or {}).get("headers", [])
        if item.get("name")
    }


def _body_from_part(part, plain_parts, html_parts):
    mime = str(part.get("mimeType") or "").lower()
    body = (part.get("body") or {}).get("data")
    if body:
        decoded = _decode_b64(body).decode("utf-8", errors="replace")
        if mime == "text/plain":
            plain_parts.append(decoded)
        elif mime == "text/html":
            html_parts.append(decoded)
    for child in part.get("parts") or []:
        _body_from_part(child, plain_parts, html_parts)


def _message_text(payload):
    plain_parts, html_parts = [], []
    _body_from_part(payload or {}, plain_parts, html_parts)
    text = "\n\n".join(x.strip() for x in plain_parts if x.strip())
    if not text:
        text = _strip_html("\n\n".join(html_parts))
    return text[:12000].strip()


def _json_error(response):
    try:
        body = response.json()
        return str((body.get("error") or {}).get("message") or body)[:500]
    except Exception:
        return str(response.text or "")[:500]


class GmailClient:
    def __init__(self):
        self.mailbox = _mailbox()
        if not self.mailbox or "@" not in self.mailbox:
            raise ValueError("GMAIL_MAILBOX must be a valid mailbox")
        self._credentials = None

    def _get_credentials(self):
        if self._credentials is not None:
            return self._credentials
        mode = str(current_app.config.get("GMAIL_AUTH_MODE", "service_account") or "service_account").strip().lower()
        scopes = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
        ]
        try:
            if mode in {"service_account", "domain_wide_delegation", "dwd"}:
                from google.oauth2 import service_account
                raw = _secret("service_account_json", current_app.config.get("GMAIL_SERVICE_ACCOUNT_JSON", ""))
                if not raw:
                    raise ValueError("GMAIL_SERVICE_ACCOUNT_JSON is not configured")
                info = json.loads(raw)
                self._credentials = service_account.Credentials.from_service_account_info(
                    info, scopes=scopes, subject=self.mailbox
                )
            elif mode in {"oauth", "oauth_refresh_token", "refresh_token"}:
                from google.oauth2.credentials import Credentials
                self._credentials = Credentials(
                    token=None,
                    refresh_token=_secret("refresh_token", current_app.config.get("GMAIL_REFRESH_TOKEN", "")),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=_secret("oauth_client_id", current_app.config.get("GMAIL_OAUTH_CLIENT_ID", "")),
                    client_secret=_secret("oauth_client_secret", current_app.config.get("GMAIL_OAUTH_CLIENT_SECRET", "")),
                    scopes=scopes,
                )
            else:
                raise ValueError("Unsupported GMAIL_AUTH_MODE")
            return self._credentials
        except Exception as error:
            raise GmailTransientError(f"Gmail credentials unavailable: {type(error).__name__}") from error

    def _token(self):
        from google.auth.transport.requests import Request

        credentials = self._get_credentials()
        try:
            if not credentials.valid or credentials.expired or not credentials.token:
                credentials.refresh(Request())
            if not credentials.token:
                raise ValueError("Gmail access token was empty")
            return credentials.token
        except Exception as error:
            raise GmailTransientError(f"Gmail authentication failed: {type(error).__name__}") from error

    def request(self, method, path, *, params=None, body=None, retry_auth=True):
        url = f"{GMAIL_API}/{self.mailbox}/{path.lstrip('/')}"
        response = requests.request(
            method,
            url,
            params=params or {},
            json=body,
            headers={"Authorization": f"Bearer {self._token()}"},
            timeout=int(current_app.config.get("GMAIL_API_TIMEOUT_SECONDS", 15) or 15),
        )
        if response.status_code == 401 and retry_auth:
            self._credentials = None
            return self.request(method, path, params=params, body=body, retry_auth=False)
        if response.status_code >= 500 or response.status_code == 429:
            raise GmailTransientError(f"Gmail API temporarily unavailable ({response.status_code})")
        if response.status_code >= 400:
            raise ValueError(f"Gmail API rejected request: {_json_error(response)}")
        return response.json() if response.content else {}

    def watch(self):
        if not _topic():
            raise ValueError("GMAIL_PUBSUB_TOPIC is not configured")
        return self.request(
            "POST",
            "watch",
            body={
                "topicName": _topic(),
                "labelIds": list(current_app.config.get("GMAIL_WATCH_LABEL_IDS", ["INBOX"])),
                "labelFilterAction": "include",
            },
        )

    def history(self, start_history_id):
        messages = []
        page_token = None
        while True:
            params = {
                "startHistoryId": str(start_history_id),
                "historyTypes": "messageAdded",
                "maxResults": int(current_app.config.get("GMAIL_HISTORY_PAGE_SIZE", 100) or 100),
            }
            if page_token:
                params["pageToken"] = page_token
            response = self.request("GET", "history", params=params)
            for event in response.get("history") or []:
                for added in event.get("messagesAdded") or []:
                    message_id = str((added.get("message") or {}).get("id") or "").strip()
                    if message_id:
                        messages.append(message_id)
            page_token = response.get("nextPageToken")
            if not page_token or len(messages) >= int(current_app.config.get("GMAIL_MAX_MESSAGES_PER_PUSH", 50) or 50):
                break
        return list(dict.fromkeys(messages))[: int(current_app.config.get("GMAIL_MAX_MESSAGES_PER_PUSH", 50) or 50)]

    def message(self, message_id):
        return self.request("GET", f"messages/{message_id}", params={"format": "full"})

    def send_confirmation(self, recipient, subject, body):
        from email.message import EmailMessage

        message = EmailMessage()
        message["To"] = recipient
        message["From"] = self.mailbox
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
        return self.request("POST", "messages/send", body={"raw": raw})


def migrate(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS gmail_watch_state (
            mailbox TEXT PRIMARY KEY,
            history_id TEXT NOT NULL,
            expiration_epoch INTEGER,
            updated_at TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS gmail_inbound_events (
            message_id TEXT PRIMARY KEY,
            mailbox TEXT NOT NULL,
            history_id TEXT,
            thread_id TEXT,
            sender TEXT,
            subject TEXT,
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            request_id TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_gmail_events_status ON gmail_inbound_events(status, updated_at)")
    db.commit()


def _state():
    row = get_db().execute("SELECT * FROM gmail_watch_state WHERE mailbox=?", (_mailbox(),)).fetchone()
    return dict(row) if row else None


def renew_watch(client=None):
    client = client or GmailClient()
    result = client.watch()
    history_id = str(result.get("historyId") or "").strip()
    if not history_id:
        raise GmailTransientError("Gmail watch returned no historyId")
    expiration_ms = int(result.get("expiration") or 0)
    expiration_epoch = expiration_ms // 1000 if expiration_ms else None
    db = get_db()
    db.execute(
        """INSERT INTO gmail_watch_state(mailbox,history_id,expiration_epoch,updated_at)
           VALUES(?,?,?,?) ON CONFLICT(mailbox) DO UPDATE SET history_id=excluded.history_id,
           expiration_epoch=excluded.expiration_epoch,updated_at=excluded.updated_at""",
        (_mailbox(), history_id, expiration_epoch, _now()),
    )
    db.commit()
    return {"mailbox": _mailbox(), "history_id": history_id, "expiration_epoch": expiration_epoch}


def _save_event(message_id, history_id, parsed, payload_hash, status="processing", request_id=None, error=""):
    db = get_db()
    db.execute(
        """INSERT INTO gmail_inbound_events(message_id,mailbox,history_id,thread_id,sender,subject,payload_hash,status,request_id,last_error,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET status=excluded.status,
           request_id=COALESCE(excluded.request_id,gmail_inbound_events.request_id),last_error=excluded.last_error,
           updated_at=excluded.updated_at""",
        (
            message_id, _mailbox(), str(history_id or ""), str(parsed.get("thread_id") or ""),
            str(parsed.get("sender") or ""), str(parsed.get("subject") or ""), payload_hash,
            status, request_id, str(error or "")[:500], _now(), _now(),
        ),
    )
    db.commit()


def _parse_message(raw):
    payload = raw.get("payload") or {}
    header = _headers(payload)
    sender = parseaddr(header.get("from", ""))[1].strip().lower()
    subject = header.get("subject", "(No subject)").strip()[:300]
    text = _message_text(payload)
    return {
        "sender": sender,
        "subject": subject,
        "text": f"Subject: {subject}\n\n{text}"[:12500].strip(),
        "thread_id": raw.get("threadId") or "",
    }


def _confirmation(request_id, subject):
    return (
        "Your NVB request has been received.\n\n"
        f"Ticket ID: {request_id}\n"
        f"Subject: {subject}\n\n"
        "The request is now queued for municipal review. Please keep this Ticket ID for tracking.\n"
        "Do not reply with passwords, Aadhaar numbers, bank details, or other sensitive information."
    )


def _process_message(client, message_id, history_id, ingest_text):
    db = get_db()
    prior = db.execute("SELECT * FROM gmail_inbound_events WHERE message_id=?", (message_id,)).fetchone()
    if prior and prior["status"] == "reply_sent":
        return {"message_id": message_id, "request_id": prior["request_id"], "status": "duplicate"}

    raw = client.message(message_id)
    parsed = _parse_message(raw)
    if not parsed["sender"] or "@" not in parsed["sender"]:
        _save_event(message_id, history_id, parsed, hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(), status="ignored", error="sender missing")
        return {"message_id": message_id, "status": "ignored"}

    payload_hash = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    request_id = prior["request_id"] if prior and prior["request_id"] else None
    _save_event(message_id, history_id, parsed, payload_hash, status="processing", request_id=request_id)
    if not request_id:
        result, error = ingest_text(
            channel="Email",
            text=parsed["text"],
            language="en",
            district="",
            sender=parsed["sender"],
            endpoint="/api/channels/email/gmail",
            idempotency_key=f"gmail:{_mailbox()}:{message_id}",
        )
        if error:
            _save_event(message_id, history_id, parsed, payload_hash, status="failed", error=error[0])
            raise GmailTransientError(str(error[0]))
        request_id = result.get("request_id")
        if not request_id:
            _save_event(message_id, history_id, parsed, payload_hash, status="ticket_queued", request_id=result.get("job_id"))
            return {"message_id": message_id, "request_id": result.get("job_id"), "status": "queued"}
        _save_event(message_id, history_id, parsed, payload_hash, status="ticket_created", request_id=request_id)

    if bool(current_app.config.get("GMAIL_SEND_CONFIRMATIONS", True)):
        client.send_confirmation(parsed["sender"], f"Nexus VisBharat Ticket {request_id}", _confirmation(request_id, parsed["subject"]))
    _save_event(message_id, history_id, parsed, payload_hash, status="reply_sent", request_id=request_id)
    return {"message_id": message_id, "request_id": request_id, "status": "processed"}


def process_notification(notification, ingest_text, client=None):
    if not isinstance(notification, dict):
        raise ValueError("Gmail notification must be an object")
    email_address = str(notification.get("emailAddress") or "").strip().lower()
    if email_address and _mailbox().lower() != email_address:
        raise ValueError("Gmail notification mailbox does not match configured mailbox")
    current_history_id = str(notification.get("historyId") or "").strip()
    if not current_history_id:
        raise ValueError("Gmail notification historyId is required")
    client = client or GmailClient()
    state = _state()
    if not state:
        # A watch call establishes the cursor. Never ingest the existing Inbox
        # from an uninitialised worker by guessing a history boundary.
        return {"processed": [], "status": "awaiting_watch_cursor"}
    start_history_id = str(state["history_id"])
    try:
        message_ids = client.history(start_history_id)
    except ValueError as error:
        if "404" in str(error) or "history" in str(error).lower():
            renew_watch(client)
            raise GmailTransientError("Gmail history cursor expired; watch renewed, notification will be retried") from error
        raise
    processed = []
    for message_id in message_ids:
        processed.append(_process_message(client, message_id, current_history_id, ingest_text))
    db = get_db()
    db.execute("UPDATE gmail_watch_state SET history_id=?,updated_at=? WHERE mailbox=?", (current_history_id, _now(), _mailbox()))
    db.commit()
    return {"processed": processed, "history_id": current_history_id, "status": "ok"}


def decode_pubsub_message(body):
    message = (body or {}).get("message") or {}
    encoded = message.get("data") or ""
    decoded = _decode_b64(encoded)
    if not decoded:
        raise ValueError("Pub/Sub message data is required")
    try:
        return json.loads(decoded.decode("utf-8"))
    except Exception as error:
        raise ValueError("Pub/Sub message data must contain JSON") from error


def status():
    state = _state()
    return {
        "enabled": bool(current_app.config.get("GMAIL_ENABLED", False)),
        "mailbox_configured": bool(_mailbox()),
        "topic_configured": bool(_topic()),
        "auth_mode": str(current_app.config.get("GMAIL_AUTH_MODE", "service_account")),
        "watch": state or {},
    }
