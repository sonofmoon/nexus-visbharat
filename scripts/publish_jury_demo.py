"""Prepare, back up, and publish a validated synthetic demo package in explicit steps."""
import argparse
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from scripts.build_jury_demo import validate


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def digest(rows):
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda row: str(row.get('request_id', row.get('id')))), sort_keys=True, default=str).encode()).hexdigest()


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def local_rows(connection):
    connection.row_factory = sqlite3.Row
    return [dict(row) for row in connection.execute('SELECT * FROM citizen_requests')]


def prepare_local(package, rows, events):
    source = Path(os.environ.get('DATABASE_PATH') or ROOT / 'visbharat.db').resolve()
    backup = package / 'backups' / 'database-before.db'
    backup.parent.mkdir(parents=True, exist_ok=True)
    candidate = package / 'database.ready.db'
    if candidate.exists():
        raise RuntimeError('A prepared database already exists; inspect it before retrying.')
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original:
        previous = local_rows(original)
        if any(not (row['request_id'].startswith('NVB-DEMO-') or row['request_id'].startswith('COMP-')) for row in previous):
            raise RuntimeError('Non-legacy requests found. Review and migrate them before replacing demo data.')
        with sqlite3.connect(backup) as saved:
            if saved.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='citizen_requests'").fetchone()[0]:
                if digest(local_rows(saved)) != digest(previous):
                    raise RuntimeError('The existing backup differs from the source; preserve both versions before proceeding.')
            else:
                original.backup(saved)
        with sqlite3.connect(candidate) as prepared:
            original.backup(prepared)
    with sqlite3.connect(candidate) as connection:
        connection.execute('CREATE TEMP TABLE retired_demo_ids (request_id TEXT PRIMARY KEY)')
        connection.executemany('INSERT INTO retired_demo_ids VALUES (?)', [(row['request_id'],) for row in previous])
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        # Preserve audit history; remove references owned by the retired request fixtures.
        for table in tables:
            if table in ('citizen_requests', 'audit_logs'):
                continue
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info({quote(table)})')}
            if 'request_id' in columns:
                connection.execute(f'DELETE FROM {quote(table)} WHERE request_id IN (SELECT request_id FROM retired_demo_ids)')
            if 'sample_request_id' in columns:
                connection.execute(f'DELETE FROM {quote(table)} WHERE sample_request_id IN (SELECT request_id FROM retired_demo_ids)')
        connection.execute('DELETE FROM citizen_requests WHERE request_id IN (SELECT request_id FROM retired_demo_ids)')
        columns = list(rows[0])
        connection.executemany(f"INSERT INTO citizen_requests ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", [[row[column] for column in columns] for row in rows])
        columns = list(events[0])
        connection.executemany(f"INSERT INTO request_lifecycle_events ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", [[row[column] for column in columns] for row in events])
        groups = defaultdict(list)
        for row in rows:
            groups[json.loads(row['ai_metadata_json'])['demo_cluster_id']].append(row)
        for cluster_id, members in groups.items():
            sample = members[0]
            metadata = json.dumps({'is_synthetic': True, 'dataset_version': 'jury-demo-v2', 'district': sample['district'], 'ward': sample['ward'], 'assignment_method': 'synthetic_ground_truth', 'not_live_model_output': True})
            connection.execute('INSERT INTO demand_clusters (cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                               (cluster_id, sample['state'], sample['category'], sample['translated_text'], len(members), sample['request_id'], metadata, sample['created_at'], members[-1]['created_at']))
            connection.executemany('INSERT INTO cluster_members (request_id, cluster_id, similarity_score, created_at) VALUES (?, ?, ?, ?)', [(row['request_id'], cluster_id, 1.0, row['created_at']) for row in members])
        assert connection.execute('SELECT COUNT(*) FROM citizen_requests').fetchone()[0] == 12500
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        orphaned = connection.execute('SELECT COUNT(*) FROM cluster_members m LEFT JOIN citizen_requests r ON r.request_id=m.request_id WHERE r.request_id IS NULL').fetchone()[0]
        # Older seeders left memberships whose requests had already been deleted. They remain in the backup.
        connection.execute('DELETE FROM cluster_members WHERE request_id NOT IN (SELECT request_id FROM citizen_requests)')
        connection.execute('DELETE FROM demand_clusters WHERE cluster_id NOT IN (SELECT DISTINCT cluster_id FROM cluster_members)')
    shutil.copy2(ROOT / 'static/data/complaints.csv', backup.parent / 'complaints-before.csv')
    return {'database_path': str(source), 'backup': str(backup), 'candidate': str(candidate), 'source_digest': digest(previous), 'rows_before': len(previous), 'rows_after': len(rows), 'clusters': len(groups), 'archived_orphan_memberships': orphaned}


def cloud_client():
    from google.cloud import bigquery
    from google.oauth2 import service_account
    project = os.environ.get('BIGQUERY_PROJECT_ID', 'nexus-visbharat')
    credentials = service_account.Credentials.from_service_account_file(os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or str(ROOT / 'sa-key.json'))
    return bigquery, bigquery.Client(project=project, credentials=credentials)


def cloud_rows(client, table, location):
    from google.cloud import bigquery
    return [dict(row) for row in client.query(f'SELECT * FROM `{table}`', location=location,
            job_config=bigquery.QueryJobConfig(maximum_bytes_billed=1024 ** 3)).result(timeout=90)]


def prepare_cloud(package, rows):
    bigquery, client = cloud_client()
    table_id = f"{client.project}.{os.environ.get('BIGQUERY_DATASET', 'visbharat_analytics')}.{os.environ.get('BIGQUERY_TABLE', 'citizen_requests_fused')}"
    original = client.get_table(table_id, timeout=20)
    location = client.get_dataset(f'{original.project}.{original.dataset_id}', timeout=20).location
    existing = cloud_rows(client, table_id, location)
    if len(existing) != 12500 or any(not str(row.get('id', row.get('request_id', ''))).startswith('NVB-DEMO-') for row in existing):
        raise RuntimeError('The active BigQuery table has changed or contains non-legacy requests; review before publication.')
    suffix = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    backup_id = table_id + '_backup_' + suffix
    stage_id = table_id + '_jury_stage_' + suffix
    client.copy_table(table_id, backup_id, location=location, job_config=bigquery.CopyJobConfig(write_disposition='WRITE_EMPTY')).result(timeout=90)
    schema = list(original.schema)
    additions = {'ward': 'STRING', 'routed_department': 'STRING', 'service_type': 'STRING', 'sla_due_at': 'TIMESTAMP',
                 'created_at': 'TIMESTAMP', 'is_synthetic': 'BOOLEAN', 'demo_dataset_version': 'STRING', 'demo_case_key': 'STRING',
                 'demo_cluster_id': 'STRING', 'ai_metadata_json': 'STRING'}
    names = {field.name for field in schema}
    schema.extend(bigquery.SchemaField(name, kind) for name, kind in additions.items() if name not in names)
    payload = []
    for row in rows:
        meta = json.loads(row['ai_metadata_json'])
        item = {key: row[key] for key in ('district', 'state', 'lat', 'lng', 'original_text', 'translated_text', 'category', 'urgency', 'sentiment', 'status', 'ward', 'routed_department', 'service_type', 'sla_due_at', 'created_at', 'ai_metadata_json')}
        item.update(id=row['request_id'], date=row['created_at'][:10], language=row['input_language'], source=row['source_channel'],
                    is_synthetic=True, demo_dataset_version='jury-demo-v2', demo_case_key=meta['case_key'], demo_cluster_id=meta['demo_cluster_id'])
        payload.append(item)
    stage = bigquery.Table(stage_id, schema=schema)
    stage.description = 'Synthetic jury demonstration: fictional citizen demands, AI-assisted authored multilingual scenarios; not verified citizen reports or measured prevalence.'
    stage.labels = {'purpose': 'synthetic-demo', 'version': 'jury-v2'}
    client.create_table(stage)
    client.load_table_from_json(payload, stage_id, location=location, job_config=bigquery.LoadJobConfig(schema=schema, write_disposition='WRITE_EMPTY')).result(timeout=120)
    loaded = cloud_rows(client, stage_id, location)
    if len(loaded) != len(payload) or {row['id'] for row in loaded} != {row['id'] for row in payload}:
        raise RuntimeError('Staged BigQuery count or IDs failed verification')
    result = {'table': table_id, 'backup_table': backup_id, 'staging_table': stage_id, 'location': location,
              'source_digest': digest(existing), 'rows_before': len(existing), 'rows_after': len(loaded)}
    (package / 'cloud-publication.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def apply(package):
    local = json.loads((package / 'local-publication.json').read_text(encoding='utf-8'))
    cloud = json.loads((package / 'cloud-publication.json').read_text(encoding='utf-8'))
    with closing(sqlite3.connect(Path(local['database_path']).as_uri() + '?mode=ro', uri=True)) as connection:
        if digest(local_rows(connection)) != local['source_digest']:
            raise RuntimeError('Local requests changed after preparation; preserve those changes before publishing.')
    bigquery, client = cloud_client()
    if digest(cloud_rows(client, cloud['table'], cloud['location'])) != cloud['source_digest']:
        raise RuntimeError('BigQuery requests changed after preparation; preserve those changes before publishing.')
    client.copy_table(cloud['staging_table'], cloud['table'], location=cloud['location'],
                      job_config=bigquery.CopyJobConfig(write_disposition='WRITE_TRUNCATE')).result(timeout=120)
    try:
        # The server must be stopped so Windows can atomically replace the database file.
        os.replace(local['candidate'], local['database_path'])
    except Exception:
        client.copy_table(cloud['backup_table'], cloud['table'], location=cloud['location'],
                          job_config=bigquery.CopyJobConfig(write_disposition='WRITE_TRUNCATE')).result(timeout=120)
        raise
    shutil.copy2(package / 'complaints.csv', ROOT / 'static/data/complaints.csv')
    shutil.copy2(package / 'showcase.json', ROOT / 'static/data/demo_showcase.json')
    shutil.copy2(package / 'report.json', ROOT / 'docs/demo_dataset_report.json')
    (package / 'published.json').write_text(json.dumps({'published_at': datetime.now(timezone.utc).isoformat(), 'local': local, 'bigquery': cloud}, indent=2), encoding='utf-8')
    print(json.dumps({'published': True, 'rows': 12500, 'local_backup': local['backup'], 'bigquery_backup': cloud['backup_table']}, indent=2))


def refresh_timelines(package, rows, events):
    source = Path(os.environ.get('DATABASE_PATH') or ROOT / 'visbharat.db').resolve()
    backup = package / 'backups' / 'database-before-timing-fix.db'
    if backup.exists():
        raise RuntimeError('The timeline backup already exists; inspect the previous revision before repeating it.')
    with closing(sqlite3.connect(source)) as connection:
        current = local_rows(connection)
        if {row['request_id'] for row in current} != {row['request_id'] for row in rows}:
            raise RuntimeError('The current request set differs from the prepared package; review before revising timelines.')
        with closing(sqlite3.connect(backup)) as saved:
            connection.backup(saved)
        with connection:
            # Retain any real officer updates made after publication.
            connection.execute("DELETE FROM request_lifecycle_events WHERE actor IN ('Synthetic demo citizen', 'Synthetic demo officer') AND json_extract(details_json, '$.dataset_version') = 'jury-demo-v2'")
            columns = list(events[0])
            connection.executemany(f"INSERT INTO request_lifecycle_events ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", [[event[column] for column in columns] for event in events])
    result = {'revised_synthetic_events': len(events), 'backup': str(backup), 'change': 'Urgency-based first response and prompt emergency completion; longer capital delivery remains separate.'}
    (package / 'timeline-revision.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, default=ROOT / 'scratch/jury-demo-v2')
    parser.add_argument('--step', choices=['local', 'cloud', 'apply', 'timelines'], required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    rows = read_jsonl(args.package / 'requests.jsonl')
    events = read_jsonl(args.package / 'lifecycle.jsonl')
    report = json.loads((args.package / 'report.json').read_text(encoding='utf-8'))
    validate(rows, events, datetime.fromisoformat(report['as_of'].replace('Z', '+00:00')))
    if args.step == 'local':
        result = prepare_local(args.package, rows, events)
        (args.package / 'local-publication.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
    elif args.step == 'cloud':
        print(json.dumps(prepare_cloud(args.package, rows), indent=2))
    elif args.step == 'timelines':
        refresh_timelines(args.package, rows, events)
    else:
        apply(args.package)


if __name__ == '__main__':
    main()
