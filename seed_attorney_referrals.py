"""Seed attorney_referrals with placeholder directory entries.

These are placeholders that show the directory structure works. The site
admin should replace them with real, verified Montana State Bar
admissions. Email attorneys@montanablotter.com to be listed.

Run: source venv/bin/activate && python3 seed_attorney_referrals.py
"""
import sqlite3

PLACEHOLDER = [
    # (county, name, firm, phone, email, website, practice_areas, blurb, sort_order)
    ('Gallatin', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
    ('Gallatin', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 20),
    ('Missoula', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
    ('Yellowstone', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
    ('Flathead', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
    ('Cascade', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
    ('Lewis and Clark', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
    ('Ravalli', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
    ('Silver Bow', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
    ('Lake', 'PLACEHOLDER — admin to fill in', 'PLACEHOLDER — admin to fill in', None, None, None, 'PLACEHOLDER', 'Placeholder row. The site admin must replace this with a real, Montana-Bar-verified attorney before publication.', 10),
]


def main():
    conn = sqlite3.connect('blotter.db')
    conn.execute('DELETE FROM attorney_referrals')
    for row in PLACEHOLDER:
        conn.execute(
            '''
            INSERT INTO attorney_referrals
              (county, name, firm, phone, email, website, practice_areas, blurb, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            row,
        )
    conn.commit()
    n = conn.execute('SELECT COUNT(*) FROM attorney_referrals').fetchone()[0]
    print(f'Seeded {n} placeholder attorney entries. Edit the DB or re-run this script to update.')
    conn.close()


if __name__ == '__main__':
    main()
