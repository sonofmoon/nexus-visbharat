"""Refresh bounded public reference snapshots without opening the operational DB."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flask import Flask
from visbharat.config import Config
from visbharat.services.public_data import SOURCES, load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=list(SOURCES)+['all'], required=True)
    args = parser.parse_args()
    app = Flask(__name__)
    app.config.from_object(Config)
    reports = []
    with app.app_context():
        for source in SOURCES if args.source == 'all' else [args.source]:
            result = load(source, refresh=True)
            reports.append({key:result.get(key) for key in ('source','status','resource_id','record_count',
                'retrieved_at','source_updated_at','sha256','complete','error','publisher_verified')})
    print(json.dumps(reports, indent=2))
    return 0 if all(row['status']=='retrieved' and row['complete'] for row in reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())
