"""Seed attorney_referrals with placeholder directory entries.

These are placeholders that show the directory structure works. The site
admin should replace them with real, verified Montana State Bar
admissions. Email attorneys@montanablotter.com to be listed.

Run: source venv/bin/activate && python3 seed_attorney_referrals.py [--reset]

Without --reset the script will refuse to wipe existing rows so that real
listings imported from scripts/attorney_outreach/target_list.csv are not lost.
"""
import argparse
import sqlite3
import sys

PLACEHOLDER = [
    # (county, name, firm, phone, email, website, practice_areas, blurb, sort_order)
    # Single "advertise here" entry per county — keeps the widget visible without fake data
    ('Gallatin',      'Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Gallatin County. Email us to claim this listing.', 10),
    ('Missoula',      'Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Missoula County. Email us to claim this listing.', 10),
    ('Yellowstone',   'Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Yellowstone County. Email us to claim this listing.', 10),
    ('Flathead',      'Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Flathead County. Email us to claim this listing.', 10),
    ('Cascade',       'Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Cascade County. Email us to claim this listing.', 10),
    ('Lewis and Clark','Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Lewis and Clark County. Email us to claim this listing.', 10),
    ('Ravalli',       'Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Ravalli County. Email us to claim this listing.', 10),
    ('Silver Bow',    'Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Silver Bow County. Email us to claim this listing.', 10),
    ('Lake',          'Advertise Your Law Firm Here', 'Available Sponsorship', None, 'record@montanablotter.com', None, 'Criminal Defense, DUI, Family Law, Personal Injury', 'This spot is reserved for a Montana-Bar-verified attorney. Reach local clients actively searching for legal help in Lake County. Email us to claim this listing.', 10),
]


def main():
    parser = argparse.ArgumentParser(description='Seed placeholder attorney listings')
    parser.add_argument('--reset', action='store_true', help='Wipe existing rows before seeding')
    args = parser.parse_args()

    conn = sqlite3.connect('blotter.db')
    existing = conn.execute('SELECT COUNT(*) FROM attorney_referrals').fetchone()[0]

    if existing > 0 and not args.reset:
        print(
            f'WARNING: {existing} attorney_referrals row(s) already exist. '
            'Re-run with --reset to replace them, or use '
            'scripts/attorney_outreach/import_target_list.py to import real attorneys.',
            file=sys.stderr,
        )
        conn.close()
        return 1

    if args.reset:
        conn.execute('DELETE FROM attorney_referrals')
        print('Cleared existing attorney_referrals rows.')

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
    print(f'Seeded {n} placeholder attorney entries.')
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
