from pathlib import Path
from datetime import datetime
import re

import pdfplumber

from services.ingestion.models import JailBookingRecord
from services.ingestion.jail_bookings import _connect_db, _sync_records

pdf_path = Path('/root/montanablotter/uploads/manual/CascadeCountyJailRoster8.1.2026.pdf')


def words_text(words):
    return ' '.join(w['text'] for w in sorted(words, key=lambda w: (round(w['top'], 1), w['x0']))).strip()


def normalize_dt(d, t):
    return datetime.strptime(f'{d} {t}', '%m/%d/%y %H:%M').strftime('%Y-%m-%d %H:%M:%S')


def title_name(s):
    return re.sub(r'\s+', ' ', s).strip().title()


records = []
with pdfplumber.open(pdf_path) as pdf:
    page_count = len(pdf.pages)
    for page_num, page in enumerate(pdf.pages, 1):
        words = page.extract_words(x_tolerance=1.5, y_tolerance=2) or []
        words = [w for w in words if 150 <= w['top'] <= 585]
        lines = []
        for w in sorted(words, key=lambda w: (w['top'], w['x0'])):
            if not lines or abs(lines[-1]['top'] - w['top']) > 2.5:
                lines.append({'top': w['top'], 'words': [w]})
            else:
                lines[-1]['words'].append(w)
        starts = []
        for li, line in enumerate(lines):
            ws = line['words']
            has_age = any(170 <= w['x0'] <= 195 and re.fullmatch(r'\d{1,3}', w['text']) for w in ws)
            has_jacket = any(220 <= w['x0'] <= 275 and re.fullmatch(r'\d{5,8}', w['text']) for w in ws)
            has_date = any(305 <= w['x0'] <= 350 and re.fullmatch(r'\d{2}/\d{2}/\d{2}', w['text']) for w in ws)
            has_time = any(340 <= w['x0'] <= 375 and re.fullmatch(r'\d{2}:\d{2}', w['text']) for w in ws)
            if has_age and has_jacket and has_date and has_time:
                starts.append(li)
        for si, li in enumerate(starts):
            end_li = starts[si + 1] if si + 1 < len(starts) else len(lines)
            block = lines[li:end_li]
            first_line = block[0]['words']
            name_words = [w for w in first_line if w['x0'] < 170]
            age_word = next(w for w in first_line if 170 <= w['x0'] <= 195 and re.fullmatch(r'\d{1,3}', w['text']))
            jacket_word = next(w for w in first_line if 220 <= w['x0'] <= 275 and re.fullmatch(r'\d{5,8}', w['text']))
            date_word = next(w for w in first_line if 305 <= w['x0'] <= 350 and re.fullmatch(r'\d{2}/\d{2}/\d{2}', w['text']))
            time_word = next(w for w in first_line if 340 <= w['x0'] <= 375 and re.fullmatch(r'\d{2}:\d{2}', w['text']))
            cont = []
            for bline in block[1:4]:
                left = [w for w in bline['words'] if w['x0'] < 170]
                if left:
                    txt = words_text(left)
                    if re.fullmatch(r"[A-Z][A-Z' -]+", txt):
                        cont.append(txt)
            synthetic = [{'text': c, 'top': 999 + idx, 'x0': 0} for idx, c in enumerate(cont)]
            raw_name = words_text(name_words + synthetic)
            if ',' in raw_name:
                last, first = raw_name.split(',', 1)
                person_name = f"{title_name(last)}, {title_name(first)}"
            else:
                person_name = title_name(raw_name)
            charge_lines = []
            for bline in block:
                cwords = [w for w in bline['words'] if 398 <= w['x0'] < 585]
                if cwords:
                    charge_lines.append(words_text(cwords))
            charges = []
            cur = ''
            for cl in charge_lines:
                if re.match(r'^(?:\d{2}\.\d{2}\.[A-Z]+|\d{2}-\d+-\d+)', cl):
                    if cur:
                        charges.append(cur.strip())
                    cur = cl
                elif cur:
                    cur += ' ' + cl
            if cur:
                charges.append(cur.strip())
            charges = [re.sub(r'\s+', ' ', c).strip() for c in charges]
            charges_summary = '; '.join(charges[:12]) or 'Charge details available on the official Cascade County inmate roster.'
            booking_at = normalize_dt(date_word['text'], time_word['text'])
            jacket = jacket_word['text']
            records.append(JailBookingRecord(
                source_record_id=f'cascade:{jacket}:{booking_at}',
                person_name=person_name,
                age=int(age_word['text']),
                booking_number=jacket,
                booking_at=booking_at,
                charges_summary=charges_summary[:1000],
                source_url='manual:' + str(pdf_path),
            ))

conn = _connect_db()
try:
    source = conn.execute("SELECT * FROM jail_booking_sources WHERE county_slug='cascade'").fetchone()
    current = conn.execute("SELECT COUNT(*) FROM jail_bookings WHERE county_slug='cascade' AND COALESCE(is_current,1)=1").fetchone()[0]
    stats = _sync_records(conn, source, records, dry_run=True)
    print(f'file={pdf_path}')
    print(f'pages={page_count} parsed={len(records)} current_in_db={current}')
    print(f'dry_run_sync: fetched={stats.fetched_count} new={stats.new_count} updated={stats.updated_count} would_mark_released={stats.missing_count}')
    for r in records[:15]:
        print(f'{r.person_name}|age={r.age}|jacket={r.booking_number}|booking_at={r.booking_at}|charges={r.charges_summary[:140]}')
finally:
    conn.close()
