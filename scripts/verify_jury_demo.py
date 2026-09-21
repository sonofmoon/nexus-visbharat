"""Read-only verification of the published BigQuery/local/demo-page contract."""
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from scripts.publish_jury_demo import cloud_client, cloud_rows


def main():
    load_dotenv(ROOT / '.env')
    manifest = json.loads((ROOT / 'static/data/demo_showcase.json').read_text(encoding='utf-8'))
    with sqlite3.connect(os.environ.get('DATABASE_PATH') or ROOT / 'visbharat.db') as connection:
        connection.row_factory = sqlite3.Row
        local = [dict(row) for row in connection.execute('SELECT * FROM citizen_requests')]
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('SELECT COUNT(*) FROM cluster_members m LEFT JOIN citizen_requests r ON r.request_id=m.request_id WHERE r.request_id IS NULL').fetchone()[0] == 0
    _, client = cloud_client()
    table = f"{client.project}.{os.environ.get('BIGQUERY_DATASET', 'visbharat_analytics')}.{os.environ.get('BIGQUERY_TABLE', 'citizen_requests_fused')}"
    cloud = cloud_rows(client, table, os.environ.get('BIGQUERY_LOCATION', 'asia-south1'))
    assert len(local) == len(cloud) == 12500
    assert {row['request_id'] for row in local} == {row['id'] for row in cloud}
    local_index = {row['request_id']: row for row in local}
    for row in cloud:
        assert re.fullmatch(r'NVB-\d{8}[0-9A-F]{4}', row['id'])
        assert row['id'][4:12] == str(row['created_at'])[:10].replace('-', '')
        assert row['is_synthetic'] is True
        counterpart = local_index[row['id']]
        for field in ('district', 'state', 'category', 'urgency', 'status', 'original_text', 'translated_text', 'ward', 'routed_department'):
            assert row[field] == counterpart[field], (row['id'], field)
        assert row['language'] == counterpart['input_language']
    with urlopen('http://127.0.0.1:5000/demo', timeout=20) as response:
        assert response.status == 200
    for case in manifest['cases']:
        with urlopen(f"http://127.0.0.1:5000/api/v1/requests/{case['request_id']}/track", timeout=15) as response:
            tracker = json.load(response)
        assert tracker['success'] and tracker['status'] == local_index[case['request_id']]['status']
    with urlopen('http://127.0.0.1:5000/api/complaints?limit=2', timeout=30) as response:
        feed = json.load(response)
    assert feed['source'] == 'bigquery'
    assert all(row['routed_department'] and row['ward'] for row in feed['complaints'])
    result = {'verified': True, 'bigquery_table': table, 'local_rows': len(local), 'bigquery_rows': len(cloud),
              'identical_ids_and_content': True, 'correct_ticket_formats': True, 'synthetic_rows': len(cloud),
              'sample_trackers': len(manifest['cases']), 'states': dict(Counter(row['state'] for row in cloud))}
    (ROOT / 'scratch/jury-demo-v2/live-verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
