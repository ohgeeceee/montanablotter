from __future__ import annotations

import sqlite3


BOARD_SOURCES = [
    {
        'board_key': 'mt_medical_examiners',
        'board_name': 'Montana Board of Medical Examiners',
        'board_url': 'https://boards.bsd.dli.mt.gov/medical-examiners/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_dentistry',
        'board_name': 'Montana Board of Dentistry',
        'board_url': 'https://boards.bsd.dli.mt.gov/dentistry/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_nursing',
        'board_name': 'Montana Board of Nursing',
        'board_url': 'https://boards.bsd.dli.mt.gov/nursing/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_bar',
        'board_name': 'Montana State Bar',
        'board_url': 'https://www.montanabar.org/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_realty_regulation',
        'board_name': 'Montana Board of Realty Regulation',
        'board_url': 'https://boards.bsd.dli.mt.gov/realty-regulation/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_chiropractors',
        'board_name': 'Montana Board of Chiropractors',
        'board_url': 'https://boards.bsd.dli.mt.gov/chiropractors/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_public_accountants',
        'board_name': 'Montana Board of Public Accountants',
        'board_url': 'https://boards.bsd.dli.mt.gov/public-accountants/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_pharmacy',
        'board_name': 'Montana Board of Pharmacy',
        'board_url': 'https://boards.bsd.dli.mt.gov/pharmacy/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_veterinary_medicine',
        'board_name': 'Montana Board of Veterinary Medicine',
        'board_url': 'https://boards.bsd.dli.mt.gov/veterinary-medicine/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_psychologists',
        'board_name': 'Montana Board of Psychologists',
        'board_url': 'https://boards.bsd.dli.mt.gov/psychologists/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_physical_rehab_health_care',
        'board_name': 'Montana Board of Physical, Rehabilitative, and Developmental Health Care Professionals',
        'board_url': 'https://boards.bsd.dli.mt.gov/Physical-Rehabilitative-and-Developmental-Health-Care-Professionals/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_plumbers',
        'board_name': 'Montana Board of Plumbers',
        'board_url': 'https://boards.bsd.dli.mt.gov/plumbers/',
        'source_type': 'html',
    },
    {
        'board_key': 'mt_electrical',
        'board_name': 'Montana Board of Electrical',
        'board_url': 'https://boards.bsd.dli.mt.gov/electrical/',
        'source_type': 'html',
    },
]


def seed_license_sanction_sources(db_path: str = 'blotter.db') -> None:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        for source in BOARD_SOURCES:
            conn.execute(
                '''
                INSERT INTO license_sanction_sources
                (board_key, board_name, board_url, source_type)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(board_key) DO UPDATE SET
                    board_name = excluded.board_name,
                    board_url = excluded.board_url,
                    source_type = excluded.source_type
                ''',
                (source['board_key'], source['board_name'], source['board_url'], source['source_type']),
            )
        conn.commit()
        print(f"Seeded {len(BOARD_SOURCES)} license sanction sources")
    finally:
        conn.close()


if __name__ == '__main__':
    seed_license_sanction_sources()
