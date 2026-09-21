"""Build a reproducible synthetic demo package without changing live databases."""
import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from visbharat.config import Config
from visbharat.demo_scenarios import JURY_CASES, PROJECT_RESPONSES, SCENARIOS

VERSION = 'jury-demo-v2'
STATE_TOTALS = {'Tamil Nadu': 6250, 'Andhra Pradesh': 3750, 'Telangana': 2500}
URBAN = {'Chennai', 'Coimbatore', 'Madurai', 'Tiruchirappalli', 'Hyderabad', 'Medchal-Malkajgiri', 'Ranga Reddy', 'Visakhapatnam', 'NTR', 'Guntur'}
COASTAL = {'Chennai', 'Cuddalore', 'Nagapattinam', 'Mayiladuthurai', 'Ramanathapuram', 'Thoothukudi', 'Kanyakumari', 'Visakhapatnam', 'Srikakulam', 'Kakinada', 'Bapatla', 'Krishna', 'West Godavari', 'East Godavari', 'Dr. B.R. Ambedkar Konaseema'}
HILL = {'Nilgiris', 'Alluri Sitharama Raju', 'Parvathipuram Manyam', 'Adilabad', 'Kumuram Bheem Asifabad', 'Mulugu', 'Bhadradri Kothagudem'}
SERVICES = {'Water Supply': ('water', 'Water Board'), 'Road': ('roads', 'Public Works Department'), 'Sanitation': ('sanitation', 'Sanitation Department'), 'Electricity': ('electricity', 'Electricity Board'), 'Health': ('health', 'Health Department'), 'Education': ('education', 'Education Department'), 'Transport': ('transport', 'Transport Department'), 'Housing': ('housing', 'Housing Board'), 'Digital Connectivity': ('digital', 'IT Department'), 'Other': ('general', 'District Grievance Cell')}
STATUS_WEIGHTS = {'Pending': 8, 'Acknowledged': 10, 'Prioritized': 16, 'Capex Allocated': 12, 'In Progress': 24, 'Escalated': 3, 'Reopened': 2, 'Resolved': 17, 'Closed': 8}
LANG_INDEX = {'en': 0, 'ta': 1, 'te': 2}


def iso(value):
    return value.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def allocate(total, weights, minimum=0):
    remaining = total - minimum * len(weights)
    if remaining < 0:
        raise ValueError('Minimum allocation exceeds total')
    weight_sum = sum(weights.values())
    raw = {key: remaining * weight / weight_sum for key, weight in weights.items()}
    result = {key: minimum + math.floor(value) for key, value in raw.items()}
    for key in sorted(raw, key=lambda key: (-(raw[key] % 1), key))[:total - sum(result.values())]:
        result[key] += 1
    return result


def pick(rng, weights):
    return rng.choices(list(weights), list(weights.values()))[0]


def category_weights(district):
    name = district['district']
    weights = {'Water Supply': 22, 'Road': 18, 'Sanitation': 15, 'Electricity': 10, 'Health': 9, 'Education': 8, 'Transport': 6, 'Housing': 5, 'Digital Connectivity': 5, 'Other': 2}
    weights['Water Supply'] += max(0, 80 - float(district['water_coverage'])) / 3
    weights['Road'] += max(0, 80 - float(district['road_coverage'])) / 4
    if name in URBAN:
        weights.update({'Sanitation': 24, 'Transport': 12, 'Digital Connectivity': 8, 'Housing': 4})
    if name in COASTAL:
        weights['Sanitation'] += 6
        weights['Electricity'] += 5
        weights['Housing'] += 3
    if name in HILL:
        weights['Health'] += 10
        weights['Road'] += 8
        weights['Digital Connectivity'] += 5
    return weights


def language_weights(district):
    native = 'ta' if district['state'] == 'Tamil Nadu' else 'te'
    return {native: 70 if district['district'] in URBAN else 88, 'en': 30 if district['district'] in URBAN else 12}


def request_text(category, urgency, language, district, ward, households, variant):
    body = SCENARIOS[category][urgency][LANG_INDEX[language]]
    if language == 'ta':
        contexts = [f'{district} மாவட்டம், வார்டு {ward}. எங்கள் பகுதியில் சுமார் {households} குடும்பங்கள் இந்த வசதியை நம்பியுள்ளன.', f'வார்டு {ward}, {district} மாவட்டத்திலிருந்து தெரிவிக்கிறோம். இங்கு {households} குடும்பங்கள் வசிக்கின்றன.']
        endings = ['எங்கள் கோரிக்கையின் நிலையை தெரிவிக்கவும்.', 'கள ஆய்வு செய்து நடவடிக்கை எடுக்குமாறு கேட்டுக்கொள்கிறோம்.', 'இந்த பிரச்சினைக்கு நிலையான தீர்வு வேண்டும்.']
    elif language == 'te':
        contexts = [f'{district} జిల్లా, వార్డు {ward}. మా ప్రాంతంలో సుమారు {households} కుటుంబాలు ఈ సదుపాయంపై ఆధారపడ్డాయి.', f'వార్డు {ward}, {district} జిల్లా నుంచి తెలియజేస్తున్నాం. ఇక్కడ {households} కుటుంబాలు నివసిస్తున్నాయి.']
        endings = ['మా అభ్యర్థన స్థితిని తెలియజేయండి.', 'స్థలాన్ని పరిశీలించి చర్య తీసుకోవాలని కోరుతున్నాం.', 'ఈ సమస్యకు శాశ్వత పరిష్కారం కావాలి.']
    else:
        contexts = [f'Ward {ward}, {district} district. About {households} households depend on this service.', f'We are writing from Ward {ward} in {district} district, where {households} households live.']
        endings = ['Please keep us informed about this request.', 'Please inspect the site and arrange action.', 'We need a lasting solution to this problem.']
    original = f'{contexts[variant % 2]} {body} {endings[variant % 3]}'
    english_contexts = [f'Ward {ward}, {district} district. About {households} households depend on this service.', f'We are writing from Ward {ward} in {district} district, where {households} households live.']
    english_endings = ['Please keep us informed about this request.', 'Please inspect the site and arrange action.', 'We need a lasting solution to this problem.']
    translated = f'{english_contexts[variant % 2]} {SCENARIOS[category][urgency][0]} {english_endings[variant % 3]}'
    return original, translated


def make_timeline(row, as_of):
    created = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
    status = row['status']
    sequence = [('submitted', 'Pending', 'Synthetic demo intake recorded; the citizen receives this ticket ID.')]
    path = [
        ('acknowledged', 'Acknowledged', f"Synthetic demo review: {row['category']} request routed to {row['routed_department']}."),
        ('prioritized', 'Prioritized', 'Synthetic demo planning: related ward requests reviewed with district population, deprivation and service-coverage reference data.'),
        ('capex_allocated', 'Capex Allocated', 'Synthetic demo funding milestone: an illustrative allocation is recorded; no actual public money has been committed.'),
        ('in_progress', 'In Progress', 'Synthetic demo field note: work package assigned, site inspection recorded and execution milestone under review.'),
        ('resolved', 'Resolved', 'Synthetic demo completion: service restored in the scenario. Independent field verification would be required in production.'),
        ('closed', 'Closed', 'Synthetic demo closure: example citizen feedback recorded and the follow-up demand-monitoring window opened.'),
    ]
    if status in ('Escalated', 'Reopened'):
        sequence.extend(path[:1] if status == 'Escalated' else path[:5])
        sequence.append((status.lower(), status, 'Synthetic demo escalation: SLA missed; duty officer asked for immediate action.' if status == 'Escalated' else 'Synthetic demo follow-up: the service issue recurred after completion; the ticket is reopened.'))
    elif status != 'Pending':
        for item in path:
            sequence.append(item)
            if item[1] == status:
                break
    events = []
    previous = None
    # Initial response follows urgency, while capital execution can take longer.
    # Do not stretch emergency repairs or acknowledgements across a months-long history.
    span = max((as_of - created).total_seconds() * .88, 0)
    response_hours = {'Routine': 72, 'Urgent': 24, 'Emergency': 4}[row['urgency']]
    first_response = min(span, response_hours * 3600 * .25)
    if status in ('Resolved', 'Closed'):
        completion_hours = {'Routine': 45 * 24, 'Urgent': 72, 'Emergency': 6}[row['urgency']]
        span = min(span, (completion_hours + (24 if status == 'Closed' else 0)) * 3600)
    for index, (event_type, target, reason) in enumerate(sequence):
        if index == 0:
            seconds = 0
        elif index == 1:
            seconds = min(first_response, span)
        elif status == 'Closed' and index == len(sequence) - 1:
            seconds = span
        else:
            work_span = min(span, completion_hours * 3600) if status == 'Closed' else span
            steps = len(sequence) - (3 if status == 'Closed' else 2)
            seconds = first_response + max(0, work_span - first_response) * (index - 1) / max(1, steps)
        moment = created + timedelta(seconds=seconds)
        events.append({'request_id': row['request_id'], 'event_type': event_type, 'from_status': previous,
                       'to_status': target, 'actor': 'Synthetic demo officer' if index else 'Synthetic demo citizen',
                       'channel': row['source_channel'], 'reason': reason, 'sla_due_at': row['sla_due_at'],
                       'details_json': json.dumps({'is_synthetic': True, 'dataset_version': VERSION}), 'created_at': iso(moment)})
        previous = target
    return events


def build(as_of, seed=20260919):
    rng = random.Random(seed)
    with (ROOT / 'static/data/districts.csv').open(encoding='utf-8-sig', newline='') as handle:
        references = {(row['state'], row['district']): row for row in csv.DictReader(handle)}
    case_by_district = {case['district']: case for case in JURY_CASES}
    rows, events, cases, used_ids = [], [], [], set()
    for state, total in STATE_TOTALS.items():
        weights = {}
        for district in Config.PILOT_STATE_TO_DISTRICTS[state]:
            ref = references[(state, district)]
            # Concave population weighting preserves smaller districts; lower access reduces observed voices.
            weights[district] = math.sqrt(float(ref['population'])) * (1.15 - .4 * float(ref['deprivation_index']))
        district_counts = allocate(total, weights, minimum=30)
        for district, count in district_counts.items():
            ref = references[(state, district)]
            case = case_by_district.get(district)
            for index in range(count):
                in_case = case is not None and index < 36
                category = case['category'] if in_case else pick(rng, category_weights(ref))
                urgency = pick(rng, {'Routine': 68, 'Urgent': 28, 'Emergency': 4})
                status = pick(rng, STATUS_WEIGHTS)
                language = pick(rng, language_weights(ref))
                ward = case['ward'] if in_case else rng.choice([number for number in range(1, 25) if not case or number != case['ward']])
                if urgency == 'Emergency':
                    status = pick(rng, {'Pending': 15, 'Acknowledged': 20, 'Escalated': 15, 'Resolved': 40, 'Closed': 10})
                if in_case and index == 0:
                    urgency, status, language = case['urgency'], case['status'], case['language']
                age_ranges = {'Pending': (.005, .8), 'Acknowledged': (1, 4), 'Prioritized': (4, 14), 'Capex Allocated': (8, 30), 'In Progress': (14, 55), 'Escalated': (3, 12), 'Reopened': (40, 90), 'Resolved': (35, 120), 'Closed': (55, 120)}
                age = rng.uniform(*age_ranges[status])
                if urgency == 'Emergency' and status in ('Pending', 'Acknowledged', 'Escalated'):
                    age = (rng.uniform(5, 9) if status == 'Escalated' else rng.uniform(.2, 3)) / 24
                if in_case and case['key'] == 'nalgonda_water':
                    # 32 reports in the 28-day pre-delivery window; 4 in the comparable post window.
                    age = rng.uniform(31, 58) if index < 32 else rng.uniform(2, 29)
                    urgency = 'Routine'
                    status = 'Closed' if index < 32 else 'Acknowledged'
                created = as_of - timedelta(days=age)
                while True:
                    suffix = f'{rng.randrange(65536):04X}'
                    ticket = f'NVB-{created:%Y%m%d}{suffix}'
                    if ticket not in used_ids:
                        used_ids.add(ticket)
                        break
                channel = pick(rng, {'Voice IVR': 28, 'IVR Missed Call': 12, 'WhatsApp': 22, 'Web Form': 18, 'SMS Keyword': 12, 'Telegram': 6, 'Email/Open API': 2})
                households = rng.randrange(25, 401)
                original, translated = request_text(category, urgency, language, district, ward, households, rng.randrange(6))
                # A shared ward centre creates deliberate geographic hotspots, with modest address jitter.
                ward_lat = float(ref['lat']) + ((ward % 5) - 2) * .004
                ward_lng = float(ref['lng']) + ((ward // 5) - 2) * .004
                service, department = SERVICES[category]
                due = created + timedelta(hours={'Routine': 72, 'Urgent': 24, 'Emergency': 4}[urgency])
                scope = 'response'
                if status in ('Prioritized', 'Capex Allocated', 'In Progress', 'Reopened'):
                    due = as_of + timedelta(days=rng.randint(3, 35))
                    scope = 'execution milestone after initial response'
                elif status == 'Escalated':
                    due = as_of - timedelta(hours=rng.uniform(1, 4))
                cluster = 'DEMO-CL-' + hashlib.sha256(f'{state}|{district}|{ward}|{category}'.encode()).hexdigest()[:10].upper()
                metadata = {
                    'is_synthetic': True, 'dataset_version': VERSION,
                    'generation_method': 'AI-assisted authored multilingual scenarios with deterministic sampling',
                    'seed': seed, 'scenario_severity': urgency, 'case_key': case['key'] if in_case else None,
                    'demo_cluster_id': cluster, 'cluster_assignment': 'synthetic ground truth; not a live model inference',
                    'deadline_scope': scope, 'households_in_service_area': households,
                    'evidence_status': 'fictional scenario; not a verified citizen complaint',
                    'reference_data': 'bundled district population, deprivation and coverage; not independently validated',
                    'project_response': PROJECT_RESPONSES[category][0],
                }
                row = {'request_id': ticket, 'source_channel': channel, 'input_language': language, 'district': district, 'state': state,
                       'lat': round(ward_lat + rng.uniform(-.001, .001), 6), 'lng': round(ward_lng + rng.uniform(-.001, .001), 6),
                       'original_text': original, 'translated_text': translated, 'category': category, 'urgency': urgency,
                       'sentiment': 'Negative' if urgency != 'Routine' else pick(rng, {'Neutral': 65, 'Negative': 35}),
                       'status': status, 'submitted_by': 'Synthetic demo citizen', 'ward': f'Ward {ward:02}', 'service_type': service,
                       'routed_department': department, 'sla_due_at': iso(due), 'sla_breached_at': iso(due) if status == 'Escalated' else None,
                       'sla_escalation_level': 1 if status == 'Escalated' else 0, 'ai_metadata_json': json.dumps(metadata, ensure_ascii=False),
                       'created_at': iso(created)}
                rows.append(row)
                row_events = make_timeline(row, as_of)
                if in_case and case['key'] == 'nalgonda_water' and index < 32:
                    # Keep completion before the post-delivery measurement window.
                    row_events = make_timeline(row, as_of - timedelta(days=30))
                events.extend(row_events)
                if in_case and index == 0:
                    cases.append({**case, **row, 'proposal': PROJECT_RESPONSES[category][0], 'possible_funding': PROJECT_RESPONSES[category][1],
                                  'reference': {key: ref[key] for key in ('population', 'deprivation_index', 'road_coverage', 'water_coverage', 'electricity_coverage')},
                                  'cohort_size': 36, 'cluster_id': cluster})
    rows.sort(key=lambda row: (row['created_at'], row['request_id']))
    events.sort(key=lambda row: (row['created_at'], row['request_id']))
    return rows, events, cases


def validate(rows, events, as_of):
    import re
    assert len(rows) == 12500
    assert len({row['request_id'] for row in rows}) == len(rows)
    index = {row['request_id']: row for row in rows}
    assert dict(Counter(row['state'] for row in rows)) == STATE_TOTALS
    assert len({(row['state'], row['district']) for row in rows}) == 97
    for row in rows:
        assert re.fullmatch(r'NVB-\d{8}[0-9A-F]{4}', row['request_id'])
        assert row['request_id'][4:12] == row['created_at'][:10].replace('-', '')
        assert row['district'] in Config.PILOT_STATE_TO_DISTRICTS[row['state']]
        language = row['input_language']
        if language == 'ta':
            assert row['state'] == 'Tamil Nadu' and re.search('[\u0b80-\u0bff]', row['original_text'])
        if language == 'te':
            assert row['state'] in ('Andhra Pradesh', 'Telangana') and re.search('[\u0c00-\u0c7f]', row['original_text'])
        assert SCENARIOS[row['category']][row['urgency']][LANG_INDEX[language]] in row['original_text']
        assert row['routed_department'] and row['sla_due_at'] and json.loads(row['ai_metadata_json'])['is_synthetic']
        assert datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')) <= as_of
    latest = {}
    for event in events:
        row = index[event['request_id']]
        assert row['created_at'] <= event['created_at'] <= iso(as_of)
        latest[event['request_id']] = event['to_status']
    assert all(latest[row['request_id']] == row['status'] for row in rows)
    return {'checks_passed': ['12500 unique date-valid NVB tickets', '97 valid state/district pairs', 'native script and state match language',
                              'text severity matches urgency', 'all requests have routing and deadlines', 'every lifecycle ends at the current status', 'no future events']}


def write_package(output, as_of, seed):
    from scripts.audit_demo_demands import summarize
    output.mkdir(parents=True, exist_ok=True)
    rows, events, cases = build(as_of, seed)
    validation = validate(rows, events, as_of)
    for filename, records in (('requests.jsonl', rows), ('lifecycle.jsonl', events)):
        (output / filename).write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records), encoding='utf-8')
    csv_rows = [{**row, 'id': row['request_id'], 'date': row['created_at'][:10], 'language': row['input_language'], 'source': row['source_channel']} for row in rows]
    with (output / 'complaints.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    summary = summarize(rows)
    report = {'dataset_version': VERSION, 'as_of': iso(as_of), 'seed': seed, 'is_synthetic': True,
              'purpose': 'Build with AI: Code for Communities (2nd Edition): AI for Digital Public Infrastructure & Governance; India pilot demonstration',
              'distribution_basis': 'Illustrative scenario design, not measured complaint prevalence. State quotas 50/30/20; district weights use square-root population, a minimum of 30 and an access adjustment; category weights use bundled coverage and geographic profiles.',
              **summary, **validation, 'lifecycle_events': len(events)}
    showcase = {'dataset_version': VERSION, 'as_of': iso(as_of), 'summary': report, 'cases': cases,
                'impact_example': {'case_key': 'nalgonda_water', 'before': 32, 'after': 4, 'window_days': 28, 'delivery_date': (as_of - timedelta(days=30)).date().isoformat(), 'note': 'Synthetic before/after fixture, not a causal estimate or real-world outcome.'}}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'showcase.json').write_text(json.dumps(showcase, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('district_counts',)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'scratch/jury-demo-v2')
    parser.add_argument('--as-of', default=iso(datetime.now(timezone.utc)))
    parser.add_argument('--seed', type=int, default=20260919)
    args = parser.parse_args()
    write_package(args.output, datetime.fromisoformat(args.as_of.replace('Z', '+00:00')), args.seed)
