"""
RH Radar - Verify the database directly, written to a file
(no terminal copy/paste) to confirm addresses are stored correctly.
"""

import sqlite3

conn = sqlite3.connect("rh_radar.db")
rows = conn.execute("""
    SELECT symbol, token_address, pair_address
    FROM raw_pairs
    ORDER BY id DESC
    LIMIT 20
""").fetchall()
conn.close()

with open("db_check.txt", "w", encoding="utf-8") as f:
    for row in rows:
        f.write(f"{row}\n")

print("Done. Open db_check.txt directly in VS Code to verify.")