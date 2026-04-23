"""
blotter_analytics.py — Charge classification and county trend aggregation.

Provides:
- classify_charge(text) → normalized category string
- backfill_categories()  → classify all unclassified records in bulk
- aggregate_by_county(county, days) → charge counts + trend deltas
- CLI: python blotter_analytics.py [--backfill] [--report CASCADE] [--days 30]
"""

import re
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional
import config

DB_PATH = config.DB_PATH
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ---------------------------------------------------------------------------
# Category definitions — ordered by priority (first match wins)
# ---------------------------------------------------------------------------

CATEGORIES: list[tuple[str, list[str]]] = [
    ('dui', [
        r'\bdui\b', r'\bduii\b', r'driving under', r'drunk driving',
        r'impaired driving', r'alcohol.*driving', r'driving.*alcohol',
    ]),
    ('violent', [
        r'\bassault\b', r'\battack\b', r'\brobbery\b', r'\brobberies\b',
        r'\bhomicide\b', r'\bmurder\b', r'\bmanslaughter\b', r'\bstab\b',
        r'\bshooting\b', r'\bshot\b', r'\bthreat\b', r'\bmenacing\b',
        r'\bintimidation\b', r'\bbattery\b', r'\bkidnap\b', r'\bstrangl',
        r'aggravated', r'domestic.*violence', r'violence.*domestic',
        r'\bsexual assault\b', r'\brape\b', r'\bchild abuse\b',
    ]),
    ('domestic', [
        r'domestic', r'\bdv\b', r'partner.*disturbance', r'family.*disturbance',
        r'disturbance.*family',
    ]),
    ('drug', [
        r'\bdrug\b', r'\bdrugs\b', r'\bnarcotics\b', r'\bmeth\b',
        r'\bheroin\b', r'\bcocaine\b', r'\bfentanyl\b', r'\bmarijuana\b',
        r'\bcannabis\b', r'controlled substance', r'possession.*substance',
        r'deliver.*controlled', r'distribution.*drug', r'\bparaphernalia\b',
        r'alcohol violation', r'minor.*alcohol', r'alcohol.*minor',
    ]),
    ('weapons', [
        r'\bweapon', r'\bfirearm\b', r'\bgun\b', r'\bpistol\b',
        r'\brifle\b', r'\bshotgun\b', r'\bknife\b', r'\bknives\b',
        r'concealed carry', r'\bexplosive\b', r'\bambush\b',
    ]),
    ('property', [
        r'\bburglary\b', r'\bburglaries\b', r'\btheft\b', r'\bshoplift',
        r'\bvandalism\b', r'\bgraffiti\b', r'\barson\b', r'\bfraud\b',
        r'\bforger\b', r'\bembezzl', r'\bextortion\b', r'\btrespass',
        r'\bmalicious mischief\b', r'\blarceny\b', r'\bauto.*theft\b',
        r'\bvehicle.*theft\b', r'\bstolen vehicle\b', r'stolen.*property',
        r'\bidentity theft\b', r'\bcheck fraud\b', r'\bwire fraud\b',
    ]),
    ('traffic', [
        r'\btraffic\b', r'\baccident\b', r'\bcollision\b', r'\bcrash\b',
        r'\bhit.*run\b', r'\bspeeding\b', r'\breckless.*driv',
        r'\bvehicle stop\b', r'\bmotor vehicle\b', r'\bno.*insurance\b',
        r'\bsuspended.*license\b', r'\bno.*license\b', r'\bregistration\b',
    ]),
    ('welfare', [
        r'welfare check', r'well.*check', r'missing person', r'missing.*child',
        r'\brunaway\b', r'person.*need.*assist', r'assist.*citizen',
        r'public assist', r'mental health', r'\bsuicid', r'\boverdose\b',
        r'medical.*assist', r'assist.*medical',
    ]),
    ('public_order', [
        r'\bdisturb', r'\btrespass', r'\bdisorderly\b', r'\bpublic intox',
        r'\bnoise\b', r'\bfight\b', r'\bbrawl\b', r'\bprostitut',
        r'\bsoliciting\b', r'\bgambling\b', r'\bcurfew\b',
        r'patrol check', r'follow up', r'investigation',
        r'suspicious', r'complaint',
    ]),
]

_COMPILED: list[tuple[str, list[re.Pattern]]] = [
    (cat, [re.compile(p, re.IGNORECASE) for p in patterns])
    for cat, patterns in CATEGORIES
]


def classify_charge(text: str) -> str:
    """Return a normalized category for an incident_type string."""
    if not text:
        return 'other'
    for category, patterns in _COMPILED:
        for pattern in patterns:
            if pattern.search(text):
                return category
    return 'other'


# ---------------------------------------------------------------------------
# Bulk backfill
# ---------------------------------------------------------------------------

def backfill_categories(db_path: str = DB_PATH) -> int:
    """Classify all records where charge_category IS NULL. Returns count updated."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, incident_type, incident FROM records WHERE charge_category IS NULL"
    ).fetchall()

    updated = 0
    for row_id, incident_type, incident in rows:
        text = incident_type or incident or ''
        category = classify_charge(text)
        conn.execute(
            "UPDATE records SET charge_category = ? WHERE id = ?",
            (category, row_id),
        )
        updated += 1

    conn.commit()
    conn.close()
    logging.info(f"Classified {updated} records")
    return updated


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

CATEGORY_LABELS = {
    'violent':      'Violent Crime',
    'domestic':     'Domestic Incidents',
    'drug':         'Drug / Alcohol',
    'dui':          'DUI',
    'weapons':      'Weapons',
    'property':     'Property Crime',
    'traffic':      'Traffic',
    'welfare':      'Welfare / Mental Health',
    'public_order': 'Public Order',
    'other':        'Other',
}


def aggregate_by_county(
    county: str,
    days: int = 7,
    compare_days: int = 90,
    db_path: str = DB_PATH,
) -> dict:
    """
    Return charge counts for `county` over the last `days`, plus trend deltas
    vs. a rolling `compare_days` baseline.

    Returns dict with keys:
      county, period_start, period_end, counts, baseline_avg, deltas, elevated
    """
    conn = sqlite3.connect(db_path)
    now = datetime.utcnow()
    period_end = now.date()
    period_start = (now - timedelta(days=days)).date()
    baseline_start = (now - timedelta(days=compare_days)).date()

    # Dates are stored as MM/DD/YY — convert to ISO for comparison:
    # '20' || substr(date,7,2) || '-' || substr(date,1,2) || '-' || substr(date,4,2)
    ISO_DATE_EXPR = "(CASE WHEN length(date)=8 THEN '20'||substr(date,7,2)||'-'||substr(date,1,2)||'-'||substr(date,4,2) ELSE date END)"

    # Current period counts
    rows = conn.execute(
        f"""
        SELECT charge_category, COUNT(*) as n
        FROM records
        WHERE county LIKE ?
          AND {ISO_DATE_EXPR} >= ?
          AND {ISO_DATE_EXPR} <= ?
          AND charge_category IS NOT NULL
        GROUP BY charge_category
        """,
        (f"%{county}%", str(period_start), str(period_end)),
    ).fetchall()
    counts = {cat: 0 for cat in CATEGORY_LABELS}
    for cat, n in rows:
        if cat in counts:
            counts[cat] = n

    # Baseline: average per same-length period over compare_days
    baseline_rows = conn.execute(
        f"""
        SELECT charge_category, COUNT(*) as n
        FROM records
        WHERE county LIKE ?
          AND {ISO_DATE_EXPR} >= ?
          AND {ISO_DATE_EXPR} < ?
          AND charge_category IS NOT NULL
        GROUP BY charge_category
        """,
        (f"%{county}%", str(baseline_start), str(period_start)),
    ).fetchall()

    periods_in_baseline = max(compare_days / days, 1)
    baseline_total: dict[str, float] = {cat: 0.0 for cat in CATEGORY_LABELS}
    for cat, n in baseline_rows:
        if cat in baseline_total:
            baseline_total[cat] = n

    baseline_avg = {
        cat: round(baseline_total[cat] / periods_in_baseline, 1)
        for cat in CATEGORY_LABELS
    }

    # Delta: pct change vs baseline avg (None if no baseline data)
    deltas: dict[str, Optional[float]] = {}
    for cat in CATEGORY_LABELS:
        b = baseline_avg[cat]
        c = counts[cat]
        if b == 0:
            deltas[cat] = None
        else:
            deltas[cat] = round(((c - b) / b) * 100, 1)

    # "Elevated" = >20% above baseline with at least 2 incidents
    elevated = [
        cat for cat in CATEGORY_LABELS
        if deltas[cat] is not None and deltas[cat] > 20 and counts[cat] >= 2
    ]

    conn.close()
    return {
        'county': county,
        'period_start': str(period_start),
        'period_end': str(period_end),
        'days': days,
        'counts': counts,
        'baseline_avg': baseline_avg,
        'deltas': deltas,
        'elevated': elevated,
        'labels': CATEGORY_LABELS,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse, json

    parser = argparse.ArgumentParser(description='Montana Blotter Analytics')
    parser.add_argument('--backfill', action='store_true',
                        help='Classify all unclassified records')
    parser.add_argument('--report', metavar='COUNTY',
                        help='Print aggregated trend report for a county')
    parser.add_argument('--days', type=int, default=7,
                        help='Period length in days (default: 7)')
    args = parser.parse_args()

    if args.backfill:
        n = backfill_categories()
        print(f"Classified {n} records.")

    if args.report:
        data = aggregate_by_county(args.report, days=args.days)
        print(f"\n=== {data['county']} — {data['period_start']} to {data['period_end']} ===\n")
        print(f"{'Category':<22} {'This period':>12} {'Baseline avg':>13} {'Delta':>8}")
        print('-' * 60)
        for cat, label in CATEGORY_LABELS.items():
            c = data['counts'][cat]
            b = data['baseline_avg'][cat]
            d = data['deltas'][cat]
            delta_str = f"{d:+.1f}%" if d is not None else "  N/A"
            flag = " ▲ ELEVATED" if cat in data['elevated'] else ""
            print(f"{label:<22} {c:>12} {b:>13.1f} {delta_str:>8}{flag}")
        if data['elevated']:
            print(f"\nElevated categories: {', '.join(data['elevated'])}")
