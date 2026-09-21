import json
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from visbharat import create_app
from visbharat.db import get_db


TOTAL_REQUESTS = 12500

STATE_TARGETS = {
    'Tamil Nadu': 6250,
    'Andhra Pradesh': 3750,
    'Telangana': 2500,
}

LANGUAGE_TARGETS = {
    'en': 5000,
    'ta': 4125,
    'te': 3375,
}

CHANNEL_TARGETS = {
    'Web Form': 2500,
    'Voice IVR': 2250,
    'WhatsApp': 2125,
    'Telegram': 1875,
    'SMS Keyword': 1750,
    'IVR Missed Call': 1500,
    'Web': 200,
    'SMS': 150,
    'Email/Open API': 150,
}

CATEGORY_TARGETS = {
    'Water Supply': 1500,
    'Sanitation': 1375,
    'Road': 1375,
    'Electricity': 1250,
    'Health': 1250,
    'Education': 1125,
    'Housing': 1125,
    'Transport': 1125,
    'Digital Connectivity': 1000,
    'Other': 1375,
}

STATUS_TARGETS = {
    'Resolved': 8500,
    'In Progress': 2500,
    'New': 1500,
}

URGENCY_WEIGHTS = {
    'Emergency': 0.14,
    'Urgent': 0.34,
    'Routine': 0.52,
}

SENTIMENT_WEIGHTS = {
    'Negative': 0.58,
    'Neutral': 0.30,
    'Positive': 0.12,
}

CATEGORY_TO_SERVICE = {
    'Road': ('Road Maintenance', 'Public Works Department'),
    'Water Supply': ('Water Distribution', 'Water Board'),
    'Electricity': ('Power Reliability', 'Electricity Board'),
    'Health': ('Primary Health Access', 'Health Department'),
    'Education': ('School Infrastructure', 'Education Department'),
    'Sanitation': ('Waste & Drainage', 'Municipal Corporation'),
    'Digital Connectivity': ('Digital Access', 'IT & Telecom Cell'),
    'Transport': ('Public Transport', 'Transport Department'),
    'Housing': ('Urban Housing', 'Housing Board'),
    'Other': ('Citizen Services', 'District Collector Office'),
}

CATEGORY_TEXT = {
    'Road': 'Major potholes and unsafe road stretch need urgent repair.',
    'Water Supply': 'Water supply is irregular and pressure is too low in this area.',
    'Electricity': 'Frequent power interruption affecting households and shops.',
    'Health': 'Primary health center is overcrowded and lacks basic supplies.',
    'Education': 'Government school requires classroom and sanitation improvements.',
    'Sanitation': 'Garbage overflow and blocked drainage causing hygiene issues.',
    'Digital Connectivity': 'Mobile/data network is weak and impacts digital services.',
    'Transport': 'Bus frequency is poor and commuters face long waiting times.',
    'Housing': 'Flood-prone low-income housing area needs immediate intervention.',
    'Other': 'Citizen grievance requires district-level administrative attention.',
}


def _expand_targets(targets: dict[str, int]) -> list[str]:
    items = []
    for key, count in targets.items():
        items.extend([key] * int(count))
    random.shuffle(items)
    return items


def _weighted_pick(weights: dict[str, float]) -> str:
    labels = list(weights.keys())
    probs = list(weights.values())
    return random.choices(labels, weights=probs, k=1)[0]


def _allocate_state_district_counts(state_to_districts: dict[str, list[str]]) -> dict[tuple[str, str], int]:
    targets = {}
    for state, total in STATE_TARGETS.items():
        dists = list(state_to_districts.get(state, []))
        if not dists:
            continue

        hub_count = max(1, int(len(dists) * 0.12))
        hubs = dists[:hub_count]
        others = dists[hub_count:]

        hub_pool = int(total * 0.46)
        other_pool = total - hub_pool

        per_hub = hub_pool // len(hubs)
        rem_hub = hub_pool - (per_hub * len(hubs))
        for idx, district in enumerate(hubs):
            targets[(state, district)] = per_hub + (1 if idx < rem_hub else 0)

        if others:
            per_other = other_pool // len(others)
            rem_other = other_pool - (per_other * len(others))
            for idx, district in enumerate(others):
                targets[(state, district)] = per_other + (1 if idx < rem_other else 0)
        else:
            targets[(state, hubs[0])] += other_pool
    return targets


def _make_rows(app):
    repo = app.extensions['reference_repo']
    state_to_districts = app.config['ALLOWED_STATE_TO_DISTRICTS']
    district_targets = _allocate_state_district_counts(state_to_districts)

    language_pool = _expand_targets(LANGUAGE_TARGETS)
    channel_pool = _expand_targets(CHANNEL_TARGETS)
    category_pool = _expand_targets(CATEGORY_TARGETS)
    status_pool = _expand_targets(STATUS_TARGETS)

    rows = []
    start = datetime.now(timezone.utc) - timedelta(days=180)
    idx = 0

    for (state, district), count in district_targets.items():
        district_row = repo.get_district_row(district)
        base_lat = float(district_row['lat']) if district_row is not None else 20.5937
        base_lng = float(district_row['lng']) if district_row is not None else 78.9629

        for _ in range(count):
            category = category_pool[idx]
            language = language_pool[idx]
            channel = channel_pool[idx]
            status = status_pool[idx]
            urgency = _weighted_pick(URGENCY_WEIGHTS)
            sentiment = _weighted_pick(SENTIMENT_WEIGHTS)
            ward_num = (idx % 36) + 1

            created_at = (start + timedelta(minutes=(idx * 17))).isoformat().replace('+00:00', 'Z')
            request_id = f"NVB-DEMO-{idx + 1:05d}"

            service_type, routed_department = CATEGORY_TO_SERVICE[category]
            text = f"{CATEGORY_TEXT[category]} District: {district}."

            sla_due = (datetime.fromisoformat(created_at.replace('Z', '+00:00')) + timedelta(days=3)).isoformat().replace('+00:00', 'Z')

            ai_meta = {
                'mode': 'jury_demo_seed',
                'seed_version': 'v1',
                'classifier_confidence': round(random.uniform(0.82, 0.97), 3),
            }

            rows.append(
                {
                    'request_id': request_id,
                    'source_channel': channel,
                    'input_language': language,
                    'district': district,
                    'state': state,
                    'lat': round(base_lat + random.uniform(-0.025, 0.025), 6),
                    'lng': round(base_lng + random.uniform(-0.025, 0.025), 6),
                    'original_text': text,
                    'translated_text': text,
                    'category': category,
                    'urgency': urgency,
                    'sentiment': sentiment,
                    'status': status,
                    'submitted_by': 'citizen_demo',
                    'ward': f'Ward {ward_num:02d}',
                    'service_type': service_type,
                    'routed_department': routed_department,
                    'sla_due_at': sla_due,
                    'sla_breached_at': None,
                    'sla_escalation_level': 0,
                    'ai_metadata_json': json.dumps(ai_meta),
                    'created_at': created_at,
                }
            )
            idx += 1

    if len(rows) != TOTAL_REQUESTS:
        raise RuntimeError(f'Generated {len(rows)} rows, expected {TOTAL_REQUESTS}')

    random.shuffle(rows)
    return rows


def _seed_local_db(rows):
    db = get_db()
    db.execute('DELETE FROM citizen_requests')

    sql = '''
    INSERT INTO citizen_requests (
        request_id, source_channel, input_language, district, state,
        lat, lng, original_text, translated_text, category, urgency,
        sentiment, status, submitted_by, ward, service_type, routed_department,
        sla_due_at, sla_breached_at, sla_escalation_level, ai_metadata_json, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    '''

    for row in rows:
        db.execute(
            sql,
            (
                row['request_id'],
                row['source_channel'],
                row['input_language'],
                row['district'],
                row['state'],
                row['lat'],
                row['lng'],
                row['original_text'],
                row['translated_text'],
                row['category'],
                row['urgency'],
                row['sentiment'],
                row['status'],
                row['submitted_by'],
                row['ward'],
                row['service_type'],
                row['routed_department'],
                row['sla_due_at'],
                row['sla_breached_at'],
                row['sla_escalation_level'],
                row['ai_metadata_json'],
                row['created_at'],
            ),
        )
    db.commit()


def _sync_bigquery(rows, app):
    bq = app.extensions.get('google_bigquery_client')
    if not bq:
        return 'skipped'

    bq._run(f'DELETE FROM {bq.table_ref} WHERE TRUE')

    table_ref = f"{bq.project_id}.{bq.dataset}.{bq.table}"
    batch_size = 500
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        payload = []
        row_ids = []
        for row in batch:
            payload.append(
                {
                    bq.col_id: row['request_id'],
                    'district': row['district'],
                    'state': row['state'],
                    'urgency': row['urgency'],
                    'category': row['category'],
                    bq.col_source: row['source_channel'],
                    bq.col_date: row['created_at'] if bq.col_date != 'date' else row['created_at'][:10],
                    bq.col_lang: row['input_language'],
                    'original_text': row['original_text'],
                    'translated_text': row['translated_text'],
                    'sentiment': row['sentiment'],
                    'status': row['status'],
                    'lat': row['lat'],
                    'lng': row['lng'],
                }
            )
            row_ids.append(row['request_id'])

        errors = bq.client.insert_rows_json(table_ref, payload, row_ids=row_ids)
        if errors:
            raise RuntimeError(f'BigQuery insert error in batch {start}: {errors[:2]}')

    return 'synced'


def _summarize(rows):
    by_state = defaultdict(int)
    by_lang = defaultdict(int)
    by_channel = defaultdict(int)
    by_category = defaultdict(int)
    resolved = 0
    for row in rows:
        by_state[row['state']] += 1
        by_lang[row['input_language']] += 1
        by_channel[row['source_channel']] += 1
        by_category[row['category']] += 1
        if row['status'].lower() == 'resolved':
            resolved += 1
    return {
        'total': len(rows),
        'states': dict(by_state),
        'languages': dict(by_lang),
        'channels': dict(by_channel),
        'categories': dict(by_category),
        'resolution_rate_pct': round((resolved / len(rows)) * 100, 2) if rows else 0.0,
    }


def main():
    random.seed(20260918)
    app = create_app()
    with app.app_context():
        rows = _make_rows(app)
        _seed_local_db(rows)
        bigquery_status = _sync_bigquery(rows, app)
        summary = _summarize(rows)
        print(json.dumps({'success': True, 'bigquery': bigquery_status, 'summary': summary}, indent=2))


if __name__ == '__main__':
    main()
