#!/usr/bin/env python3
"""
Ingest Rebelytics LND foods into NickelTrack database.

The Rebelytics data uses µg/serving (mean).
We convert to points using the Mislankar formula (points = µg / 10).
Category is based on nickel content: low <50, medium 50-150, high >150 µg/serving.
"""

import json
import psycopg2
import os

# Load from parsed JSON
with open('/tmp/lnd_foods_parsed.json', 'r') as f:
    foods = json.load(f)

# Connect to database
conn = psycopg2.connect(
    host=os.getenv('PGVECTOR_HOST', '192.168.4.105'),
    database='openclaw_memory',
    user='openclaw_user',
    password=os.getenv('PGVECTOR_PASSWORD', 'OpenClaw2026!Secure'),
    options='-c search_path=nickeltrack'
)

cur = conn.cursor()

# Insert foods
inserted = 0
skipped = 0

for food in foods:
    name = food['name']
    category = food['nickel_category']  # low/medium/high based on µg
    serving_desc = food['serving']
    serving_grams = food.get('serving_grams')
    nickel_ug = food['nickel_ug_per_serving']
    
    # Calculate points (Mislankar formula: 1 point = 10 µg, max 10, NULL for >100 = avoid)
    if nickel_ug > 100:
        points = None  # NULL = avoid
    else:
        points = round(nickel_ug / 10, 1)
    
    # Skip if already exists (by name)
    cur.execute("SELECT id FROM foods WHERE LOWER(name) = LOWER(%s)", (name,))
    if cur.fetchone():
        print(f"Skipping (exists): {name}")
        skipped += 1
        continue
    
    # Create or get serving entry
    serving_id = None
    if serving_grams:
        cur.execute("""
            INSERT INTO servings (description, grams, source)
            VALUES (%s, %s, %s)
            ON CONFLICT (description, source) DO UPDATE SET grams = EXCLUDED.grams
            RETURNING id
        """, (serving_desc, serving_grams, 'rebelytics_lnd_r9.1.1_2025'))
        serving_id = cur.fetchone()[0]
    
    # Insert food
    cur.execute("""
        INSERT INTO foods (name, category, nickel_ug_per_serving, points, serving_id, source, source_ref)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (name, category, round(nickel_ug, 2), points, serving_id, 'rebelytics_lnd_r9.1.1_2025', 
          f"Rebelytics LND r9.1.1, {food['sources']} sources, mean={nickel_ug:.2f} µg"))
    
    new_id = cur.fetchone()[0]
    print(f"Inserted: {name} (id={new_id}, {nickel_ug:.1f} µg, {points} pts, {category})")
    inserted += 1

conn.commit()
cur.close()
conn.close()

print(f"\n=== Summary ===")
print(f"Inserted: {inserted}")
print(f"Skipped (duplicates): {skipped}")
print(f"Total processed: {inserted + skipped}")
