"""Run durable Telegram processing/delivery outside the webhook request."""

import os
import time
import argparse

from visbharat import create_app
from visbharat.services.telegram_gateway import dispatch_outbox
from visbharat.services.telegram_jobs import process_one, heartbeat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=('all', 'jobs', 'outbox'), default='all')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    app = create_app()
    once = args.once or os.environ.get("TELEGRAM_OUTBOX_ONCE", "").strip().lower() in {"1", "true", "yes"}
    interval = max(float(os.environ.get("TELEGRAM_OUTBOX_INTERVAL_SECONDS", "1") or 1), 0.1)
    while True:
        with app.app_context():
            heartbeat(args.role)
            if args.role in {'all', 'outbox'}:
                dispatch_outbox()
            if args.role in {'all', 'jobs'}:
                process_one()
            if args.role == 'all':
                dispatch_outbox()
        if once:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
