import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "visbharat.db"

def run():
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Update Bengaluru Urban verification_status to proposed
    cursor.execute("""
        UPDATE pilot_locations 
        SET verification_status = 'proposed'
        WHERE district = 'Bengaluru Urban'
    """)
    conn.commit()
    
    cursor.execute("SELECT location_id, state, district, ward, verification_status FROM pilot_locations")
    rows = cursor.fetchall()
    print("Updated pilot_locations:")
    for r in rows:
        print(" ", r)
    conn.close()

if __name__ == '__main__':
    run()
