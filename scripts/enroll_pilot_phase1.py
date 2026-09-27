"""Enroll Phase 1 Pilot locations (Bengaluru Urban, Karnataka) and expand languages/categories.
Non-destructive script preserving all existing data and chain integrity.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from visbharat.security import hash_api_token, token_last4

DB_PATH = BASE_DIR / "visbharat.db"

def now():
    return datetime.now(timezone.utc).isoformat()

def run():
    print(f"Opening database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Enrol Bengaluru Urban Locations
    bengaluru_locations = [
        ("bengaluru_urban-1", "vellore-tirupati-water", "Karnataka", "Bengaluru Urban",
         "Bruhat Bengaluru Mahanagara Palike (BBMP)", "Ward 150 Bellandur", "urban_ward",
         "LGD-BBMP-150-DEMO", "proposed", "https://bbmp.gov.in/wards/150"),
        ("bengaluru_urban-2", "vellore-tirupati-water", "Karnataka", "Bengaluru Urban",
         "Bruhat Bengaluru Mahanagara Palike (BBMP)", "Ward 174 HSR Layout", "urban_ward",
         "LGD-BBMP-174-DEMO", "proposed", "https://bbmp.gov.in/wards/174"),
        ("bengaluru_urban-3", "vellore-tirupati-water", "Karnataka", "Bengaluru Urban",
         "Bengaluru East Taluk Panchayat", "Anekal Rural Panchayat 1", "rural_panchayat",
         "LGD-ANK-001-DEMO", "proposed", "https://panchatantra.karnataka.gov.in"),
    ]

    for loc in bengaluru_locations:
        cursor.execute(
            """INSERT INTO pilot_locations(
                location_id, pilot_id, state, district, local_body, ward,
                location_kind, official_code, verification_status, reference_uri
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(location_id) DO UPDATE SET
                state=excluded.state,
                district=excluded.district,
                local_body=excluded.local_body,
                ward=excluded.ward,
                location_kind=excluded.location_kind,
                official_code=excluded.official_code,
                verification_status=excluded.verification_status,
                reference_uri=excluded.reference_uri
            """,
            loc
        )
        print(f"Enrolled location: {loc[0]} ({loc[2]} - {loc[3]} - {loc[5]})")

    # 2. Update Programme Config for Cross-State Demo (5 languages, 10 categories, routing)
    row = cursor.execute("SELECT config_json, version FROM pilot_programmes WHERE pilot_id='vellore-tirupati-water'").fetchone()
    if row:
        config = json.loads(row["config_json"])
        
        # Strictly preserve Tamil-first priority
        target_languages = ["ta", "te", "kn", "hi", "en"]
        config["languages"] = target_languages
        
        all_categories = [
            "Water Supply", "Road", "Sanitation", "Electricity", "Health",
            "Education", "Transport", "Housing", "Digital Connectivity", "Other"
        ]
        config["categories"] = all_categories
        
        if "routing" not in config:
            config["routing"] = {}
        config["routing"]["Bengaluru Urban"] = "Bengaluru Urban civic services review team (demo)"
        if "Vellore" not in config["routing"]:
            config["routing"]["Vellore"] = "Vellore water services review team (demo)"
        if "Tirupati" not in config["routing"]:
            config["routing"]["Tirupati"] = "Tirupati water services review team (demo)"

        new_version = row["version"] + 1
        new_title = "NVB Cross-State Development Pilot"
        stamp = now()

        cursor.execute(
            "UPDATE pilot_programmes SET title=?, config_json=?, version=?, updated_at=? WHERE pilot_id='vellore-tirupati-water'",
            (new_title, json.dumps(config), new_version, stamp)
        )
        print(f"Updated pilot_programmes to version {new_version} with title '{new_title}', 5 languages, and 10 categories.")

    # 3. Create or Update Demo Officer for Bengaluru Urban
    officer_name = "Bengaluru Urban demo officer"
    officer_token = "bengaluru-test"
    officer_hash = hash_api_token(officer_token)
    officer_last4 = token_last4(officer_token)

    existing_user = cursor.execute("SELECT id FROM users WHERE name=?", (officer_name,)).fetchone()
    if not existing_user:
        cursor.execute(
            "INSERT INTO users(name, api_token, api_token_hash, token_last4, role, created_at) VALUES(?,?,?,?,'analyst',?)",
            (officer_name, officer_token, officer_hash, officer_last4, now())
        )
        user_id = cursor.lastrowid
        print(f"Created user: {officer_name} (ID {user_id})")
    else:
        user_id = existing_user["id"]
        cursor.execute(
            "UPDATE users SET api_token=?, api_token_hash=?, token_last4=? WHERE id=?",
            (officer_token, officer_hash, officer_last4, user_id)
        )
        print(f"Updated user token for: {officer_name} (ID {user_id})")

    # 4. Enroll Membership for Bengaluru Urban Officer
    cursor.execute(
        """INSERT INTO pilot_memberships(pilot_id, user_id, state, district, active)
        VALUES('vellore-tirupati-water', ?, 'Karnataka', 'Bengaluru Urban', 1)
        ON CONFLICT(pilot_id, user_id) DO UPDATE SET
            state='Karnataka', district='Bengaluru Urban', active=1
        """,
        (user_id,)
    )
    print(f"Enrolled pilot membership for {officer_name} in Karnataka - Bengaluru Urban")

    conn.commit()
    conn.close()
    print("Phase 1 pilot enrolment completed successfully!")

if __name__ == "__main__":
    run()
