import argparse
import os
import signal
import sys
import time

from visbharat import create_app
from visbharat.services.pipeline_queue import run_pipeline_once, upsert_worker_heartbeat


SHOULD_STOP = False


def _signal_handler(signum, frame):
    global SHOULD_STOP
    SHOULD_STOP = True


def main():
    parser = argparse.ArgumentParser(description='VisBharat pipeline worker')
    parser.add_argument('--once', action='store_true', help='Run only one queue tick and exit')
    parser.add_argument('--max-loops', type=int, default=0, help='Optional max loop count (0 = unlimited)')
    parser.add_argument('--worker-id', default='', help='Worker identifier for heartbeat rows')
    parser.add_argument('--heartbeat-interval', type=int, default=10, help='Heartbeat interval seconds')
    args = parser.parse_args()

    app = create_app()
    pid = os.getpid()
    worker_id = (args.worker_id or f'pipeline-worker-{pid}').strip()

    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _signal_handler)

    loops = 0
    last_heartbeat = 0

    with app.app_context():
        upsert_worker_heartbeat(worker_id, 'starting', pid=pid, metadata={'once': args.once})
        try:
            while True:
                if SHOULD_STOP:
                    upsert_worker_heartbeat(worker_id, 'stopping', pid=pid)
                    break

                result = run_pipeline_once()
                if result:
                    print(f"[worker] {result['job_id']} -> {result['status']}")
                    hb_meta = {'last_job_id': result.get('job_id'), 'last_status': result.get('status')}
                else:
                    print('[worker] no jobs available')
                    hb_meta = {'idle': True}

                now = int(time.time())
                if now - last_heartbeat >= max(int(args.heartbeat_interval), 1):
                    upsert_worker_heartbeat(worker_id, 'running', pid=pid, metadata=hb_meta)
                    last_heartbeat = now

                loops += 1
                if args.once:
                    break
                if args.max_loops > 0 and loops >= args.max_loops:
                    break

                sleep_s = int(app.config.get('PIPELINE_POLL_INTERVAL_SECONDS', 2))
                time.sleep(max(sleep_s, 1))

            upsert_worker_heartbeat(worker_id, 'stopped', pid=pid)
        except Exception as err:
            upsert_worker_heartbeat(worker_id, 'error', pid=pid, last_error=str(err))
            raise


if __name__ == '__main__':
    main()
