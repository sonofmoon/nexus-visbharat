#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys

import requests

TARGET_DISTRICTS = [
    'Chennai',
    'Vellore',
    'Karur',
    'Visakhapatnam',
    'Tirupati',
    'Hyderabad',
]


def run_reset_and_seed(db_path: str, base_url: str, per_district: int, timeout: int) -> int:
    cmd = [
        sys.executable,
        'scripts/demo_reset_and_seed.py',
        '--db-path', db_path,
        '--base-url', base_url,
        '--per-district', str(per_district),
        '--timeout', str(timeout),
    ]
    return subprocess.call(cmd)


def fetch_hotspots(base_url: str, timeout: int):
    url = f"{base_url.rstrip('/')}/api/hotspots"
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    if isinstance(payload, dict) and 'hotspots' in payload:
        return payload['hotspots']

    if isinstance(payload, list):
        return payload

    raise ValueError(f'Unexpected hotspot response shape: {json.dumps(payload)[:400]}')


def print_hotspot_summary(hotspots):
    filtered = [item for item in hotspots if item.get('district') in TARGET_DISTRICTS]

    by_priority = sorted(
        filtered,
        key=lambda x: (x.get('priority_score', 0), x.get('request_count', 0)),
        reverse=True,
    )

    print('\n=== Judge Demo Hotspot Summary ===')
    print(f'Target districts: {", ".join(TARGET_DISTRICTS)}')
    print(f'Found in hotspot response: {len(filtered)} / {len(TARGET_DISTRICTS)}')

    if not by_priority:
        print('No hotspot rows found for target districts.')
        return

    print('\nDistrict ranking (priority_score, request_count):')
    for row in by_priority:
        district = row.get('district', 'Unknown')
        state = row.get('state', 'Unknown')
        priority = row.get('priority_score', 0)
        count = row.get('request_count', 0)
        print(f'- {district}, {state}: priority={priority}, requests={count}')


def main():
    parser = argparse.ArgumentParser(
        description='Run reset+seed and print hotspot summary for judge demo in one command.'
    )
    parser.add_argument('--db-path', default='visbharat.db', help='Path to SQLite DB file')
    parser.add_argument('--base-url', default='http://localhost:5000', help='API base URL')
    parser.add_argument('--per-district', type=int, default=6, help='Complaints per district for seeding')
    parser.add_argument('--timeout', type=int, default=15, help='HTTP timeout in seconds')
    args = parser.parse_args()

    if args.per_district < 1:
        print('per-district must be >= 1', file=sys.stderr)
        return 2

    print('=== Step 1: Reset + Seed ===')
    rc = run_reset_and_seed(args.db_path, args.base_url, args.per_district, args.timeout)
    if rc != 0:
        print(f'Failed during reset+seed phase with code {rc}', file=sys.stderr)
        return rc

    print('\n=== Step 2: Fetch Hotspots ===')
    try:
        hotspots = fetch_hotspots(args.base_url, args.timeout)
    except Exception as err:
        print(f'Failed to fetch hotspots: {err}', file=sys.stderr)
        return 1

    print_hotspot_summary(hotspots)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
