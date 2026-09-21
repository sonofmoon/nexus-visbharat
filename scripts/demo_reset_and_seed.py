#!/usr/bin/env python3
import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

DEMO_SOURCE = 'Demo Seeder'


def clear_demo_rows(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM citizen_requests WHERE source_channel = ?", (DEMO_SOURCE,))
        before_requests = cursor.fetchone()[0]

        cursor.execute(
            """
            SELECT COUNT(*) FROM audit_logs
            WHERE (action = 'citizen_request_submitted' OR action = 'citizen_voice_request_submitted')
              AND details_json LIKE ?
            """,
            ('%"source": "Demo Seeder"%',)
        )
        before_audits = cursor.fetchone()[0]

        cursor.execute("DELETE FROM citizen_requests WHERE source_channel = ?", (DEMO_SOURCE,))
        deleted_requests = cursor.rowcount

        cursor.execute(
            """
            DELETE FROM audit_logs
            WHERE (action = 'citizen_request_submitted' OR action = 'citizen_voice_request_submitted')
              AND details_json LIKE ?
            """,
            ('%"source": "Demo Seeder"%',)
        )
        deleted_audits = cursor.rowcount

        conn.commit()

        return {
            'before_requests': before_requests,
            'before_audits': before_audits,
            'deleted_requests': deleted_requests,
            'deleted_audits': deleted_audits,
        }
    finally:
        conn.close()


def run_seed(base_url: str, per_district: int, timeout: int):
    cmd = [
        sys.executable,
        'scripts/seed_demo_complaints.py',
        '--base-url', base_url,
        '--per-district', str(per_district),
        '--timeout', str(timeout),
    ]
    return subprocess.call(cmd)


def main():
    parser = argparse.ArgumentParser(description='Clear only demo-generated rows and reseed for judge demo.')
    parser.add_argument('--db-path', default='visbharat.db', help='Path to SQLite DB file')
    parser.add_argument('--base-url', default='http://localhost:5000', help='API base URL for reseeding')
    parser.add_argument('--per-district', type=int, default=6, help='Complaints per district to reseed')
    parser.add_argument('--timeout', type=int, default=15, help='HTTP timeout in seconds for reseeding')
    parser.add_argument('--skip-seed', action='store_true', help='Only clear demo rows, do not reseed')
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f'DB not found: {db_path}', file=sys.stderr)
        return 2

    summary = clear_demo_rows(db_path)
    print('=== Demo Reset Summary ===')
    print(f"DB: {db_path}")
    print(f"Deleted demo requests: {summary['deleted_requests']} (before: {summary['before_requests']})")
    print(f"Deleted demo audit logs: {summary['deleted_audits']} (before: {summary['before_audits']})")

    if args.skip_seed:
        print('Skip seed enabled. Reset complete.')
        return 0

    print('\n=== Reseeding Demo Data ===')
    return run_seed(args.base_url, args.per_district, args.timeout)


if __name__ == '__main__':
    raise SystemExit(main())
