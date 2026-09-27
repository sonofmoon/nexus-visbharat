"""Local development poller for @NexusVisBharatBot.

Fetches updates via Telegram getUpdates long-polling and forwards them to
the local Flask webhook endpoint or processes them in-process.

Usage:
  Set TELEGRAM_ENABLE_LEGACY_POLLER=true in .env or shell, then run:
  python telegram_poller.py
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SECRET_TOKEN = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "nexus_visbharat_secret_2026").strip()
BASE_URL = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
WEBHOOK_URL = os.environ.get("TELEGRAM_INTERNAL_WEBHOOK_URL", "http://127.0.0.1:5000/api/channels/telegram/webhook").strip()


def delete_webhook():
    """Clear webhook registration so getUpdates polling can receive updates."""
    if not BASE_URL:
        return
    try:
        resp = requests.get(f"{BASE_URL}/deleteWebhook", timeout=10)
        logging.info("deleteWebhook result: %s", resp.json())
    except Exception as exc:
        logging.error("Error deleting webhook: %s", exc)


def process_update(update):
    """Deliver the Telegram update to the VisBharat gateway directly in-process."""
    try:
        from visbharat import create_app
        from visbharat.services.telegram_gateway import dispatch_outbox, handle_update
        from visbharat.blueprints.api_channels import _ingest_text_request

        app = create_app()
        with app.app_context():
            handle_update(update, _ingest_text_request)
            dispatch_outbox()
        logging.info("Processed update %s directly in-process", update.get("update_id"))
    except Exception as exc:
        logging.exception("In-process dispatch failed for update %s: %s", update.get("update_id"), exc)


def main():
    if os.environ.get("TELEGRAM_ENABLE_LEGACY_POLLER", "").strip().lower() not in {"1", "true", "yes"}:
        logging.error("Legacy Telegram polling is disabled by default. Set TELEGRAM_ENABLE_LEGACY_POLLER=true for local polling development.")
        return

    if not TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN is not configured. Cannot start poller.")
        return

    delete_webhook()

    offset = None
    logging.info("Starting Telegram Bot Poller for @NexusVisBharatBot...")
    while True:
        try:
            url = f"{BASE_URL}/getUpdates?timeout=20"
            if offset:
                url += f"&offset={offset}"
            resp = requests.get(url, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    for item in data.get("result", []):
                        offset = item["update_id"] + 1
                        process_update(item)
            else:
                logging.warning("getUpdates returned status %s: %s", resp.status_code, resp.text)
        except Exception as exc:
            logging.error("Poller error: %s", exc)
            time.sleep(3)


if __name__ == "__main__":
    main()
