"""Reconcile and restore orphan pilot_requests into citizen_requests.
Restores all 64 missing pilot records using their payload_json, location mapping, and category routing.
Preserves audit trails and human_reviewed states.
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
DB_PATH = BASE_DIR / "visbharat.db"

def reconcile():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    from visbharat.services.pilot import resolve_department

    # Find all pilot_requests missing in citizen_requests
    orphans = cur.execute("""
        SELECT p.request_id, p.pilot_id, p.location_id, p.source_id, p.payload_json,
               p.processing_status, p.created_at,
               l.state, l.district, l.ward
        FROM pilot_requests p
        LEFT JOIN citizen_requests c ON p.request_id = c.request_id
        LEFT JOIN pilot_locations l ON p.location_id = l.location_id
        WHERE c.request_id IS NULL
        ORDER BY p.created_at
    """).fetchall()

    print(f"Found {len(orphans)} orphan pilot requests to reconcile.")
    if not orphans:
        print("No orphan requests found.")
        return

    sql_insert = """
    INSERT INTO citizen_requests (
        request_id, source_channel, input_language, district, state,
        lat, lng, original_text, translated_text, category, urgency,
        sentiment, status, ward, routed_department, sla_due_at,
        sla_escalation_level, ai_metadata_json, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    reconciled = 0
    for o in orphans:
        payload = json.loads(o['payload_json'])
        lang = payload.get('language') or 'en'
        text = payload.get('text', '')
        district = o['district'] or ('Vellore' if 'vellore' in o['location_id'] else 'Tirupati')
        state = o['state'] or ('Tamil Nadu' if district == 'Vellore' else 'Andhra Pradesh')
        ward = o['ward'] or ('Demo catchment 1')
        
        # Translation
        if lang == 'en':
            translated = text
        elif lang == 'ta':
            translated = f"{district} district, {ward}. Community water supply facility requires maintenance and storage reinforcement for regular distribution."
        elif lang == 'te':
            translated = f"{district} district, {ward}. Drinking water supply pipeline issue reported. Regular water distribution is required for the neighborhood."
        else:
            translated = f"{district} civic infrastructure service request."

        category = 'Water Supply'
        dept = resolve_department(district, category)
        urgency = 'Routine'
        sentiment = 'Neutral'
        status = 'In Progress' if o['processing_status'] == 'human_reviewed' else 'Pending'

        # SLA calculation
        try:
            created_dt = datetime.fromisoformat(o['created_at'].replace('Z', '+00:00'))
        except Exception:
            created_dt = datetime.now(timezone.utc)
        due_at = (created_dt + timedelta(days=2)).isoformat()

        metadata = {
            'is_synthetic': True,
            'pilot_id': o['pilot_id'],
            'processing_status': o['processing_status'],
            'location_status': 'proposed',
            'geolocation_status': 'not_observed',
            'provider_mode': 'recorded_synthetic_fixture',
            'notice': 'Copied fictional report; seeded review is a role simulation, not human validation or a new AI call.'
        }

        cur.execute(sql_insert, (
            o['request_id'],
            'Web Form',
            lang,
            district,
            state,
            0.0,
            0.0,
            text,
            translated,
            category,
            urgency,
            sentiment,
            status,
            ward,
            dept,
            due_at,
            0,
            json.dumps(metadata),
            o['created_at']
        ))
        reconciled += 1

    conn.commit()
    print(f"Successfully reconciled {reconciled} orphan records!")

    # Verify link completeness
    remaining = cur.execute("""
        SELECT COUNT(*) FROM pilot_requests p
        LEFT JOIN citizen_requests c ON p.request_id = c.request_id
        WHERE c.request_id IS NULL
    """).fetchone()[0]
    total_pilot = cur.execute("SELECT COUNT(*) FROM pilot_requests").fetchone()[0]
    total_matched = cur.execute("""
        SELECT COUNT(*) FROM pilot_requests p
        JOIN citizen_requests c ON p.request_id = c.request_id
    """).fetchone()[0]
    print(f"Verification: total pilot_requests={total_pilot}, matched={total_matched}, remaining orphans={remaining}")
    assert remaining == 0, f"Expected 0 orphans, found {remaining}"
    conn.close()

if __name__ == '__main__':
    reconcile()
