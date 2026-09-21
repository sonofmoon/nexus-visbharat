import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from visbharat import create_app
from visbharat.db import init_db, seed_default_users, get_db
from visbharat.services.pipeline_queue import (
    enqueue_ingestion_job,
    run_pipeline_once,
    get_pipeline_metrics,
    retry_pipeline_job,
    list_worker_heartbeats,
)


def main():
    db_path = os.path.join(os.getcwd(), 'tests', 'burnin_phase2.db')
    if os.path.exists(db_path):
        os.remove(db_path)

    app = create_app()
    app.config.update(
        TESTING=True,
        DATABASE_PATH=db_path,
        DATABASE_URL='',
        ASYNC_PIPELINE_ENABLED=True,
        PIPELINE_MAX_RETRIES=2,
        PIPELINE_RETRY_BACKOFF_SECONDS=0,
        PIPELINE_POLL_INTERVAL_SECONDS=1,
        WEBHOOK_SHARED_TOKEN='burnin-token',
        WEBHOOK_REQUIRE_REPLAY_PROTECTION=True,
        WEBHOOK_REPLAY_WINDOW_SECONDS=300,
        WEBHOOK_REPLAY_STORE='db',
        WEBHOOK_ALLOWED_IPS='',
        META_APP_SECRET='',
        TELEGRAM_WEBHOOK_SECRET='',
        TWILIO_AUTH_TOKEN='',
    )

    report = {
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'db_path': db_path,
        'checks': {},
        'summary': {},
    }

    with app.app_context():
        init_db()
        seed_default_users()

        # 1) Worker restart simulation using real worker process with same worker-id
        env = os.environ.copy()
        env['DATABASE_PATH'] = db_path
        env['ASYNC_PIPELINE_ENABLED'] = 'true'
        env['WEBHOOK_SHARED_TOKEN'] = 'burnin-token'
        env['WEBHOOK_REQUIRE_REPLAY_PROTECTION'] = 'true'
        env['WEBHOOK_REPLAY_STORE'] = 'db'

        p1 = subprocess.run([
            sys.executable,
            'scripts/pipeline_worker.py',
            '--once',
            '--worker-id',
            'burnin-worker',
            '--heartbeat-interval',
            '1',
        ], env=env, capture_output=True, text=True)

        time.sleep(1)

        p2 = subprocess.run([
            sys.executable,
            'scripts/pipeline_worker.py',
            '--once',
            '--worker-id',
            'burnin-worker',
            '--heartbeat-interval',
            '1',
        ], env=env, capture_output=True, text=True)

        workers = list_worker_heartbeats(limit=20)
        burnin_worker = next((w for w in workers if w.get('worker_id') == 'burnin-worker'), None)

        report['checks']['worker_restart'] = {
            'pass': p1.returncode == 0 and p2.returncode == 0 and burnin_worker is not None,
            'run1_rc': p1.returncode,
            'run2_rc': p2.returncode,
            'final_worker_status': (burnin_worker or {}).get('status'),
            'final_last_seen_at': (burnin_worker or {}).get('last_seen_at'),
        }

        # 2) Replay load simulation (same nonce/timestamp repeated)
        client = app.test_client()
        replay_headers = {
            'X-Webhook-Token': 'burnin-token',
            'X-Webhook-Timestamp': str(int(time.time())),
            'X-Webhook-Nonce': 'burnin-replay-1',
        }

        accepted = 0
        rejected = 0
        total = 200
        for i in range(total):
            resp = client.post(
                '/api/channels/sms/webhook',
                headers=replay_headers,
                data={
                    'body': f'replay payload {i}',
                    'district': 'Chennai',
                    'language': 'en',
                    'from': '+911000000000',
                },
            )
            if resp.status_code == 200:
                accepted += 1
            elif resp.status_code == 401:
                rejected += 1

        report['checks']['replay_load'] = {
            'pass': accepted == 1 and rejected == (total - 1),
            'total_requests': total,
            'accepted': accepted,
            'rejected': rejected,
        }

        # 3) Retry storm simulation (bad payloads -> retries -> dead_letter)
        bad_jobs = []
        for i in range(30):
            jid = enqueue_ingestion_job({
                'channel': 'SMS',
                'text': '',  # invalid on purpose
                'language': 'en',
                'district': 'Chennai',
                'sender': f'storm-{i}',
            }, idempotency_key=f'storm-{i}-{time.time()}')
            bad_jobs.append(jid)

        # run worker loops enough times to push to dead_letter
        for _ in range(120):
            res = run_pipeline_once()
            if res is None:
                break

        metrics_after_storm = get_pipeline_metrics()
        dead_letter_count = metrics_after_storm['by_status'].get('dead_letter', 0)

        report['checks']['retry_storm'] = {
            'pass': dead_letter_count >= 30,
            'dead_letter_count': dead_letter_count,
            'metrics': metrics_after_storm,
        }

        # 4) Dead-letter recovery workflow
        recover_job = bad_jobs[0]
        db = get_db()
        fixed_payload = {
            'channel': 'SMS',
            'text': 'Recovered valid payload after DLQ triage',
            'language': 'en',
            'district': 'Chennai',
            'sender': 'recovery-bot',
        }
        db.execute('UPDATE processing_jobs SET payload_json = ? WHERE job_id = ?', (json.dumps(fixed_payload), recover_job))
        db.commit()

        retry_result, retry_err = retry_pipeline_job(recover_job)
        if retry_result:
            for _ in range(10):
                r = run_pipeline_once()
                if r and r.get('job_id') == recover_job:
                    break

        recovered_row = db.execute('SELECT status, result_json, last_error FROM processing_jobs WHERE job_id = ?', (recover_job,)).fetchone()
        recovered_status = recovered_row['status'] if recovered_row else None

        report['checks']['dead_letter_recovery'] = {
            'pass': retry_err is None and recovered_status == 'succeeded',
            'retry_err': retry_err,
            'final_status': recovered_status,
        }

        final_metrics = get_pipeline_metrics()

    # overall readiness
    passes = [v['pass'] for v in report['checks'].values()]
    report['summary'] = {
        'all_passed': all(passes),
        'passed_checks': sum(1 for p in passes if p),
        'total_checks': len(passes),
        'final_metrics': final_metrics,
    }

    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()



