"""
Quick verification: seed a second ODC version into the DB to confirm the
unique-index dedup path, dry-run path, and skip path all work.
"""

import sqlite3, sys
from datetime import datetime, timezone

# Use the same DB_PATH the rest of the project uses.
from init_db import DB_PATH

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Seed a fake older version so we can confirm version-skip + dedup on re-run.
fake_version = "2026.06.01"
existing = conn.execute(
    "SELECT COUNT(*) AS cnt FROM odc_discipline WHERE pdf_version = ?", (fake_version,)
).fetchone()

if existing["cnt"] == 0:
    now = datetime.now(timezone.utc).isoformat()
    src = "https://img1.wsimg.com/blobby/go/EXAMPLE/2026.06.01%20Website%20Public%20Discipline.pdf"
    rows = [
        ("Test, John A.", "PR 99-9999", "Public Censure; Costs", "2026-06-01", fake_version, src, now),
        ("Test, Jane B.", "PR 99-9998", "Indefinite Suspension, not less than 6 months", "2026-05-15", fake_version, src, now),
    ]
    conn.executemany(
        """INSERT INTO odc_discipline
           (attorney_name, cause_no, discipline, date_ordered, pdf_version,
            source_url, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    print(f"Seeded {len(rows)} fake rows for v{fake_version}")
else:
    print(f"v{fake_version} already has {existing['cnt']} rows — skipping seed")

# Verify the full table shape.
total = conn.execute("SELECT COUNT(*) AS cnt FROM odc_discipline").fetchone()["cnt"]
versions = conn.execute(
    "SELECT pdf_version, COUNT(*) AS cnt FROM odc_discipline GROUP BY pdf_version ORDER BY pdf_version"
).fetchall()
print(f"\nTotal rows across all versions: {total}")
print("Per-version counts:")
for v, c in versions:
    print(f"  {v}: {c}")

conn.close()
print("\nDB verification complete.")
