"""Renew the Gmail API mailbox watch used by the NVB Pub/Sub intake."""

import os
import time

from visbharat import create_app
from visbharat.services.gmail_gateway import renew_watch


def main():
    app = create_app()
    once = os.environ.get('GMAIL_WATCH_ONCE', '').strip().lower() in {'1', 'true', 'yes'}
    interval = max(int(os.environ.get('GMAIL_WATCH_RENEWAL_INTERVAL_SECONDS', '86400') or 86400), 3600)
    while True:
        with app.app_context():
            renew_watch()
        if once:
            return
        time.sleep(interval)


if __name__ == '__main__':
    main()
