"""Dispatch Telegram webhook replies that were retained after a transient failure.

Run this as a small Cloud Run worker, service, or scheduled job in production;
the webhook also flushes a bounded batch immediately for low latency.
"""

import os
import time

from visbharat import create_app
from visbharat.services.telegram_gateway import dispatch_outbox


def main():
    app = create_app()
    once = os.environ.get("TELEGRAM_OUTBOX_ONCE", "").strip().lower() in {"1", "true", "yes"}
    interval = max(int(os.environ.get("TELEGRAM_OUTBOX_INTERVAL_SECONDS", "5") or 5), 1)
    while True:
        with app.app_context():
            dispatch_outbox()
        if once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
