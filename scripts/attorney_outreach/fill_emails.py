"""Fill in contact emails for target_list.csv by scraping websites.

Writes target_list_filled.csv next to target_list.csv.
"""
from __future__ import annotations

import csv
import re
import sys
from urllib.parse import urlparse

import requests

SCRIPT_DIR = 'scripts/attorney_outreach'
CSV_PATH = f'{SCRIPT_DIR}/target_list.csv'
OUT_PATH = f'{SCRIPT_DIR}/target_list_filled.csv'

headers = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
}


def find_mailtos(html: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'href=["\']mailto:([^"\']+)["\']', html, re.I):
        addr = m.group(1)
        addr = re.split(r'[?#]', addr)[0].strip()
        if '@' in addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


NOISE = re.compile(
    r'(?i)(^user@domain\.com$|^webmaintenance@|^@sentry\.|^hello@|^noreply@'
    r'|^info@|^contact@|^support@|^office@|^legalasst@|^reception@'
    r'|^jloreception|^dwight@|^jesse@|^brent@|^dd0a55ccb8124b9c9d938e3acf41f8aa'
    r'|sentry\.wixpress)',
)

GENERIC_LOCAL = re.compile(
    r'^(info|contact|support|office|legal|reception|hello|webmaster'
    r'|webmaintenance|admin|staff|team|noreply|subscribe|newsletter'
    r'|enquiry|enquiry|hi|mail|post|send|help|careers|jobs|apply'
    r'|sales|marketing|service|client|clients|business|legal|law'
    r'|firm|attorney|attorneys|lawyer|lawyers|email|web|site'
    r'|domain|user|test|example|dummy|placeholder)$',
    re.I,
)


def is_personish(addr: str) -> bool:
    local = addr.split('@')[0]
    return bool(re.search(r'[a-z]', local)) and not GENERIC_LOCAL.match(local)


def pick_best(mailtos: list[str]) -> list[str]:
    personish = [m for m in mailtos if is_personish(m) and not NOISE.search(m)]
    if personish:
        return personish
    clean = [m for m in mailtos if not NOISE.search(m)]
    if clean:
        return clean
    return []


def resolve_email(website: str) -> tuple[str, str]:
    """Return (email_or_empty, status)."""
    if not website:
        return '', 'no_website'

    parsed = urlparse(website)
    base = f'{parsed.scheme}://{parsed.netloc}'

    urls_to_try = [website]
    for suffix in ['/contact', '/contact-us', '/about', '/team', '/attorneys', '/firm']:
        candidate = base + suffix
        if candidate not in urls_to_try and candidate + '/' not in urls_to_try:
            urls_to_try.append(candidate)

    found: list[str] = []
    last_err: str = ''
    for u in urls_to_try[:4]:
        try:
            r = requests.get(u, timeout=15, headers=headers, allow_redirects=True)
            if r.status_code == 200:
                mailtos = find_mailtos(r.text)
                picked = pick_best(mailtos)
                if picked:
                    found.extend(picked)
                    if found:
                        break
        except Exception as exc:
            last_err = f'{exc.__class__.__name__}: {exc}'[:100]
            continue

    if found:
        return found[0], 'found_via_website'

    # Inferred contact@ if we actually hit a contact-ish page.
    if any('/contact' in u for u in urls_to_try):
        inferred = f'contact@{parsed.netloc.lstrip("www.")}'
        return inferred, 'inferred_contact'

    detail = f'checked {len(urls_to_try)} page(s); no mailto'
    if last_err:
        detail += f'; last err: {last_err}'
    return '', 'no_email_found'


def main() -> int:
    with open(CSV_PATH, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    fieldnames = [
        'firm_name', 'county', 'city', 'website', 'contact_name',
        'contact_email', 'email_status', 'practice_areas', 'notes',
    ]
    filled: list[dict[str, str]] = []

    for row in rows:
        firm = (row.get('firm_name') or '').strip()
        website = (row.get('website') or '').strip()
        contact = (row.get('contact_name') or '').strip()
        old_email = (row.get('contact_email') or '').strip()
        old_status = (row.get('email_status') or '').strip()
        practice = (row.get('practice_areas') or '').strip()
        notes = (row.get('notes') or '').strip()
        city = (row.get('city') or '').strip()
        county = (row.get('county') or '').strip()

        if old_email and old_status == 'found_via_website':
            new_email, new_status = old_email, 'found_via_website'
        else:
            new_email, new_status = resolve_email(website)

        filled.append({
            'firm_name': firm,
            'county': county,
            'city': city,
            'website': website,
            'contact_name': contact,
            'contact_email': new_email,
            'email_status': new_status,
            'practice_areas': practice,
            'notes': notes,
        })

    with open(OUT_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filled)

    from collections import Counter
    counts = Counter(r['email_status'] for r in filled)
    print(f'Wrote {len(filled)} rows to {OUT_PATH}\n')
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f'  {k}: {v}')

    print('\nUsable emails (found_via_website or inferred_contact):')
    for r in filled:
        if r['email_status'] in ('found_via_website', 'inferred_contact'):
            print(f"  {r['firm_name']:<42} | {r['county']:<16} | "
                  f"{r['contact_email']:<35} | {r['email_status']}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
