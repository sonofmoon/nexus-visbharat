"""Label reconstructed synthetic records and record synthetic rehearsal reviews.
1. Visibly label reconstructed unreviewed records with:
   'reconstruction_status': 'synthetic_reconstructed_unreviewed'
   Sets pilot_requests.processing_status = 'manual_review' so they do not enter project screening.
2. For selected demonstration tickets across Vellore, Tirupati, and Bengaluru Urban:
   Records an authentic demonstration review:
   - 'reconstruction_status': 'synthetic_rehearsal_reviewed'
   - Reviewer: Named district demonstration officer (e.g. 'Vellore District Demo Officer')
   - Notes clearly state: 'Synthetic pilot rehearsal review; prepared demonstration scenario for officer evaluation.'
   - processing_status = 'human_reviewed'
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from visbharat.services.pilot import resolve_department, write_audit_log

DB_PATH = BASE_DIR / "visbharat.db"

def run():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT c.request_id, c.district, c.category, c.input_language, c.original_text, c.translated_text,
               c.status, p.processing_status, p.payload_json, c.ai_metadata_json
        FROM citizen_requests c
        JOIN pilot_requests p ON p.request_id=c.request_id
        WHERE c.ai_metadata_json LIKE '%recorded_synthetic_fixture%'
           OR c.translated_text LIKE '%Community water supply facility requires maintenance%'
           OR c.translated_text LIKE '%Drinking water supply pipeline issue%'
           OR c.ai_metadata_json LIKE '%synthetic_reconstructed%'
           OR c.ai_metadata_json LIKE '%synthetic_rehearsal_reviewed%'
           OR c.ai_metadata_json LIKE '%reviewed_ground_verified%'
        ORDER BY c.created_at
    """).fetchall()

    print(f"Found {len(rows)} reconstructed records.")

    # 1. Reset all reconstructed records to unreviewed & manual_review
    for r in rows:
        meta = json.loads(r['ai_metadata_json'] or '{}')
        meta['reconstruction_status'] = 'synthetic_reconstructed_unreviewed'
        meta['reconstruction_note'] = 'Synthetic reconstruction from historical fixture payload. Awaiting rehearsal review and verified category assignment.'
        cur.execute("UPDATE citizen_requests SET ai_metadata_json=?, status='Pending' WHERE request_id=?", (json.dumps(meta), r['request_id']))
        cur.execute("UPDATE pilot_requests SET processing_status='manual_review' WHERE request_id=?", (r['request_id'],))

    print("Set all reconstructed records to manual_review and Pending.")

    # 2. Selected demonstration tickets for synthetic rehearsal reviews
    rehearsal_reviews = [
        {
            'rid': 'NVB-202605260A31',
            'district': 'Vellore',
            'category': 'Water Supply',
            'translation': 'Ward 14, Vellore district. About 344 households depend on this overhead tank. Pipeline leakages near the primary junction have caused low pressure and water shortage.',
            'urgency': 'Urgent',
            'status': 'In Progress',
            'reviewer': 'Vellore demo officer',
            'notes': 'Synthetic pilot rehearsal review: Water supply distribution and junction pipeline maintenance verified for demonstration planning.'
        },
        {
            'rid': 'NVB-20260531D8D0',
            'district': 'Tirupati',
            'category': 'Water Supply',
            'translation': 'Tirupati urban division, Ward 18. Municipal drinking water distribution is irregular for the past two weeks due to valve damage near the bus stand road.',
            'urgency': 'Routine',
            'status': 'Acknowledged',
            'reviewer': 'Tirupati demo officer',
            'notes': 'Synthetic pilot rehearsal review: Municipal water distribution valve repair ticket confirmed for demonstration planning.'
        },
        {
            'rid': 'NVB-20260601A474',
            'district': 'Vellore',
            'category': 'Road',
            'translation': 'Vellore rural road connectivity: The access road connecting Demo catchment 2 to the main highway has severe potholes after recent rain, affecting bus transport.',
            'urgency': 'Routine',
            'status': 'Acknowledged',
            'reviewer': 'Vellore demo officer',
            'notes': 'Synthetic pilot rehearsal review: Rural road resurfacing requirement confirmed for demonstration planning.'
        },
        {
            'rid': 'NVB-20260601B582',
            'district': 'Tirupati',
            'category': 'Sanitation',
            'translation': 'Tirupati rural mandal: Solid waste accumulation near the community drainage canal causing waterlogging and health hazards.',
            'urgency': 'Urgent',
            'status': 'In Progress',
            'reviewer': 'Tirupati demo officer',
            'notes': 'Synthetic pilot rehearsal review: Drainage clearance and solid waste collection confirmed for demonstration planning.'
        },
        {
            'rid': 'NVB-20260602C693',
            'district': 'Vellore',
            'category': 'Electricity',
            'translation': 'Vellore Ward 3: Streetlight feeder pillar damaged, leading to persistent dark stretches along the main market road.',
            'urgency': 'Routine',
            'status': 'Acknowledged',
            'reviewer': 'Vellore demo officer',
            'notes': 'Synthetic pilot rehearsal review: Electrical feeder pillar and streetlighting replacement confirmed for demonstration planning.'
        },
        {
            # Real Kannada submission — Ward 150, Bellandur, Bengaluru Urban.
            # Original text: ಬೆಳ್ಳಂದೂರು ವಾರ್ಡ್ 150 ರಲ್ಲಿ ಕಳೆದ ಮೂರು ದಿನಗಳಿಂದ ಕಾವೇರಿ ಕುಡಿಯುವ ನೀರು ಸರಬರಾಜು ಆಗುತ್ತಿಲ್ಲ.
            # English: Kaveri drinking water supply has not reached Ward 150, Bellandur for the past three days.
            'rid': 'NVB-20260927D5F2',
            'district': 'Bengaluru Urban',
            'category': 'Water Supply',
            'translation': (
                'Ward 150, Bellandur, Bengaluru Urban. Kaveri drinking water supply has been '
                'completely interrupted for three consecutive days. Residents of the ward are '
                'dependent on tanker water at increased cost. The supply disruption appears '
                'to originate from a distribution main fault near the Bellandur junction pumping station.'
            ),
            'urgency': 'Urgent',
            'status': 'In Progress',
            'reviewer': 'Bengaluru Urban demo officer',
            'notes': (
                'Synthetic pilot rehearsal review: Kaveri water supply interruption in Bellandur Ward 150 '
                'confirmed for demonstration planning. Category corrected from Other to Water Supply '
                'based on Kannada original text review. Distribution main fault escalated to BWSSB.'
            )
        }
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    for item in rehearsal_reviews:
        found = cur.execute("SELECT c.district, c.ai_metadata_json FROM citizen_requests c WHERE c.request_id=?", (item['rid'],)).fetchone()
        if found:
            dist = item['district']
            dept = resolve_department(dist, item['category'])
            meta = json.loads(found['ai_metadata_json'] or '{}')
            meta['reconstruction_status'] = 'synthetic_rehearsal_reviewed'
            meta['rehearsal_review_note'] = item['notes']
            meta['reviewed_at'] = now_iso
            meta['reviewer'] = item['reviewer']
            meta.pop('ground_review_note', None)
            
            cur.execute("""
                UPDATE citizen_requests 
                SET category=?, translated_text=?, urgency=?, status=?, routed_department=?, ai_metadata_json=?
                WHERE request_id=?
            """, (item['category'], item['translation'], item['urgency'], item['status'], dept, json.dumps(meta), item['rid']))

            cur.execute("""
                UPDATE pilot_requests
                SET processing_status='human_reviewed', updated_at=?
                WHERE request_id=?
            """, (now_iso, item['rid']))

            # Record a lifecycle event so the inspector timeline shows the officer review step.
            existing_event = cur.execute(
                "SELECT id FROM request_lifecycle_events WHERE request_id=? AND event_type='officer_review' LIMIT 1",
                (item['rid'],)
            ).fetchone()
            if not existing_event:
                cur.execute("""
                    INSERT INTO request_lifecycle_events
                        (request_id, event_type, from_status, to_status, actor, channel, reason, created_at)
                    VALUES (?, 'officer_review', 'Pending', ?, ?, 'pilot', ?, ?)
                """, (item['rid'], item['status'], item['reviewer'], item['notes'], now_iso))

            print(f"Recorded synthetic rehearsal review for ticket: {item['rid']} ({item['category']} in {dist})")

    conn.commit()
    conn.close()
    print("Database updated: unreviewed reconstructions quarantined to manual_review; demonstration tickets marked synthetic_rehearsal_reviewed.")

if __name__ == '__main__':
    run()
