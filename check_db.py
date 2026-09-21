import sqlite3

conn = sqlite3.connect('visbharat.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT source_channel, COUNT(*) 
    FROM citizen_requests 
    GROUP BY source_channel
""")
channel_counts = cursor.fetchall()
print("Requests Count by Channel:")
for channel, count in channel_counts:
    print(f"  • {channel}: {count}")

cursor.execute("""
    SELECT request_id, source_channel, original_text, category, urgency, routed_department, created_at 
    FROM citizen_requests 
    WHERE source_channel = 'Telegram' 
    ORDER BY id DESC 
    LIMIT 5
""")
telegram_rows = cursor.fetchall()
print("\nRecent Telegram Requests Stored:")
for r in telegram_rows:
    print(f"Request ID: {r[0]} | Original Text: '{r[2]}' | Category: {r[3]} | Urgency: {r[4]} | Dept: {r[5]}")
