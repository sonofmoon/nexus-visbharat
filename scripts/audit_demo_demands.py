"""Read-only inventory of the configured demo demand stores; retain an audit snapshot."""
import csv
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv


def summarize(rows):
    def value(row, *names):
        return next((str(row[name]) for name in names if row.get(name) is not None), '')
    ids = [value(row, 'request_id', 'id') for row in rows]
    dates = [value(row, 'created_at', 'date')[:10] for row in rows]
    summary = {
        'rows': len(rows), 'unique_ids': len(set(ids)),
        'standard_ticket_ids': sum(bool(re.fullmatch(r'NVB-\d{8}[0-9A-F]{4}', ticket)) for ticket in ids),
        'id_prefixes': dict(Counter(ticket.split('-')[0] for ticket in ids)),
        'nonstandard_id_examples': [ticket for ticket in ids if not re.fullmatch(r'NVB-\d{8}[0-9A-F]{4}', ticket)][:8],
        'districts': len({(row.get('state'), row.get('district')) for row in rows}),
        'date_range': [min(dates), max(dates)] if dates else [],
        'unique_original_texts': len({row.get('original_text') for row in rows}),
        'missing_department': sum(not row.get('routed_department') for row in rows),
        'missing_sla': sum(not row.get('sla_due_at') for row in rows),
        'native_language_script_mismatches': sum(
            (value(row, 'input_language', 'language') == 'ta' and not re.search('[\u0b80-\u0bff]', row.get('original_text', '')))
            or (value(row, 'input_language', 'language') == 'te' and not re.search('[\u0c00-\u0c7f]', row.get('original_text', '')))
            for row in rows),
    }
    for label, names in {
        'state': ('state',), 'language': ('input_language', 'language'), 'urgency': ('urgency',),
        'category': ('category',), 'status': ('status',), 'channel': ('source_channel', 'source'),
    }.items():
        summary[label] = dict(Counter(value(row, *names) for row in rows).most_common())
    summary['state_language'] = dict(Counter(f"{row.get('state')} / {value(row, 'input_language', 'language')}" for row in rows))
    summary['district_counts'] = dict(sorted(Counter(f"{row.get('state')} / {row.get('district')}" for row in rows).items()))
    return summary


def main():
    load_dotenv(ROOT / '.env')
    output = ROOT / 'scratch' / 'demo-demand-audit'
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    with (ROOT / 'static/data/complaints.csv').open(encoding='utf-8-sig', newline='') as handle:
        report['csv'] = summarize(list(csv.DictReader(handle)))
    db_path = Path(os.environ.get('DATABASE_PATH') or ROOT / 'visbharat.db').resolve()
    with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute('SELECT * FROM citizen_requests')]
        report['local'] = {'path': str(db_path), **summarize(rows)}
        report['local']['tables'] = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    (output / 'local-before.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False, default=str) + '\n' for row in rows), encoding='utf-8')
    (output / 'local-summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: {k: v for k, v in info.items() if k not in ('district_counts', 'tables')} for key, info in report.items()}, ensure_ascii=False, indent=2), flush=True)

    from google.cloud import bigquery
    from google.oauth2 import service_account
    project = os.environ.get('BIGQUERY_PROJECT_ID', 'nexus-visbharat')
    dataset = os.environ.get('BIGQUERY_DATASET', 'visbharat_analytics')
    table_name = os.environ.get('BIGQUERY_TABLE', 'citizen_requests_fused')
    key_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or str(ROOT / 'sa-key.json')
    credentials = service_account.Credentials.from_service_account_file(key_path)
    client = bigquery.Client(project=project, credentials=credentials)
    table_id = f'{project}.{dataset}.{table_name}'
    table = client.get_table(table_id, timeout=20)
    inventory = [item.table_id for item in client.list_tables(f'{project}.{dataset}', timeout=20)]
    config = bigquery.QueryJobConfig(maximum_bytes_billed=1024 ** 3)
    rows = [dict(row) for row in client.query(f'SELECT * FROM `{table_id}`', job_config=config, location=client.get_dataset(f'{project}.{dataset}', timeout=20).location, timeout=20).result(timeout=60)]
    report['bigquery'] = {'table': table_id, 'tables': inventory, 'schema': [field.to_api_repr() for field in table.schema], **summarize(rows)}
    (output / 'bigquery-before.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False, default=str) + '\n' for row in rows), encoding='utf-8')
    (output / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report['bigquery'].items() if k not in ('district_counts',)}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
