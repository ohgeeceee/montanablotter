"""Generate combined mailing packet for 44 police blotter letters:

- docs/police_blotter_full_packet.html
  Each page: letter body + cut-out envelope address panel
  Final pages: Avery 5160 address labels + return-address labels

Usage:  python3 scripts/ops/generate_full_packet.py
Output: docs/police_blotter_full_packet.html
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "docs" / "police_blotter_contact_form_followup.csv"
OUT = ROOT / "docs" / "police_blotter_full_packet.html"

# Brand colors
RED = "#c3423f"
STEEL = "#29607b"
AMBER = "#ad7a2d"
BG = "#f4f1ea"
TEXT = "#14212b"
MUTED = "#5c6d78"

TOWN = "Great Falls, Montana"
PHONE = "406-290-3337"
EMAIL1 = "records@montanablotter.com"
EMAIL2 = "support@montanablotter.com"
URL = "https://montanablotter.com"
RETURN_ADDR_LINE1 = "Montana Blotter"
RETURN_ADDR_LINE2 = "Great Falls, Montana"
TODAY = date.today().strftime("%B %d, %Y")

# ── Letter body template ────────────────────────────────────────────
LETTER_BODY = """\
    <div class="letterhead">
      <div class="letterhead-title">Montana Blotter</div>
      <div class="letterhead-tagline">Montana Public Records &bull; Open &amp; Free</div>
      <div class="letterhead-contact">
        {TOWN} &nbsp;|&nbsp; {PHONE}<br>
        {EMAIL1} &nbsp;|&nbsp; {EMAIL2}<br>
        {URL}
      </div>
    </div>

    <div class="date">{TODAY}</div>

    <div class="salutation">Dear Records Custodian:</div>

    <div class="body">
      <p>Montana Blotter is a free, open-source statewide public-records service. We aggregate daily police blotters from every Montana county into a single, searchable feed at {URL}&mdash;making arrest and incident data accessible to the public, media, researchers, and civic watchdogs.</p>

      <p>We have been unable to locate a regularly updated, machine-readable police-blotter feed from {COUNTY_TITLE} Sheriff's Office. Would your agency be willing to share a copy of your daily blotter with us through one of these channels?</p>

      <p style="margin-left:0.2in;margin-right:0.2in;margin-bottom:0.15in;">
        <strong>Option 1.</strong> Daily email delivery to {EMAIL1} in PDF, DOCX, CSV, or plain text<br>
        <strong>Option 2.</strong> Nightly FTP or HTTPS upload to a URL your agency provides<br>
        <strong>Option 3.</strong> Direct access to a daily-updated PDF or HTML page on your public website<br>
        <strong>Option 4.</strong> Authenticated API read of your records system
      </p>

      <p>For a daily police blotter, we extract: incident number, date and time, location, agency, incident type, and disposition (when available). We publish the official source, update time, and a direct link back to your agency with every entry. If you prefer we stop drawing from the feed, simply let us know and we will remove {COUNTY_TITLE} from the pipeline.</p>

      <div class="agency-info">
        <span class="agency-info-label">Your records-request contact form:</span><br>
        {CONTACT_URL}<br>
        <span class="agency-info-label">Agency phone:</span> {PHONE_VALUE}
      </div>

      <p>Please reply with any questions or a commitment to share the blotter. Also let us know the best contact person and preferred method for delivery issues or follow-up.</p>
    </div>

    <div class="closing">Sincerely,</div>
    <div class="signature-name">John Currie</div>
    <div class="signature-type">Montana Blotter</div>

    <div class="footer-line">
      Representing Montana&rsquo;s 56 counties. Visit {URL} to search over 1 million public records.
    </div>
"""

# ── HTML template ───────────────────────────────────────────────────
HEADER = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Montana Blotter &mdash; Police Blotter Letters Packet</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=Playfair+Display:wght@700&family=Great+Vibes&display=swap" rel="stylesheet">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: 100%; height: 100%; }}
  body {{
    font-family: "Inter", system-ui, sans-serif;
    background: {BG};
    color: {TEXT};
    line-height: 1.6;
    padding: 0;
  }}

  .page-break {{
    page-break-after: always;
    break-after: page;
    width: 8.5in;
    height: 11in;
    margin: 0 auto;
    padding: 0.5in 0.75in 0.35in 0.75in;
    background: white;
    position: relative;
    box-shadow: 0 0 0 1px #ddd;
  }}
  @media print {{
    body {{ margin: 0; padding: 0; background: white; }}
    .page-break {{ margin: 0; box-shadow: none; page-break-after: always; break-after: page; }}
  }}

  .letterhead {{
    border-bottom: 3px solid {RED};
    padding-bottom: 0.35in;
    margin-bottom: 0.35in;
  }}
  .letterhead-title {{
    font-family: "Playfair Display", serif;
    font-size: 32px;
    font-weight: 700;
    color: {RED};
    margin-bottom: 0;
  }}
  .letterhead-tagline {{
    font-size: 11px;
    color: {STEEL};
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-top: 2px;
  }}
  .letterhead-contact {{
    font-size: 10px;
    color: {MUTED};
    margin-top: 0.25in;
    line-height: 1.4;
  }}
  .date {{ font-size: 12px; color: {TEXT}; margin-bottom: 0.25in; margin-top: 0.25in; }}
  .salutation {{ font-size: 12px; margin-bottom: 0.18in; color: {TEXT}; }}
  .body {{ font-size: 11px; line-height: 1.65; color: {TEXT}; margin-bottom: 0.25in; }}
  .body p {{ margin-bottom: 0.18in; }}
  .closing {{ font-size: 12px; margin-top: 0.25in; margin-bottom: 0.1in; }}
  .signature-name {{ font-family: "Great Vibes", cursive; font-size: 22px; color: {TEXT}; margin-bottom: 0; line-height: 1; }}
  .signature-type {{ font-size: 10px; font-weight: 600; color: {TEXT}; margin-top: 0.05in; }}
  .agency-info {{
    background: rgba({RED_RGB}, 0.05);
    border-left: 3px solid {RED};
    padding: 0.15in 0.12in;
    margin: 0.25in 0 0.15in 0;
    font-size: 10px;
    line-height: 1.4;
  }}
  .agency-info-label {{ font-weight: 600; color: {RED}; }}
  .footer-line {{
    border-top: 1px solid {AMBER};
    padding-top: 0.12in;
    margin-top: 0.3in;
    font-size: 9px;
    color: {MUTED};
  }}

  /* Envelope panel */
  .envelope-panel {{
    margin-top: 0.3in;
    padding-top: 0.18in;
    border-top: 2px dashed #bbb;
  }}
  .envelope-label {{
    font-size: 8px;
    font-weight: 600;
    color: {MUTED};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 0.08in;
  }}
  .envelope-addr {{
    font-family: "Courier New", monospace;
    font-size: 12px;
    line-height: 1.45;
    color: {TEXT};
    padding: 0.08in 0.12in;
    border: 1px solid #ddd;
    display: inline-block;
    min-width: 3in;
  }}
  .envelope-addr strong {{ font-size: 13px; }}

  /* Label sheets */
  .label-sheet {{
    width: 8.5in;
    margin: 0 auto;
    padding: 0.5in 0.469in 0.5in 0.437in;
    background: white;
    page-break-after: always;
    break-after: page;
    box-shadow: 0 0 0 1px #ddd;
  }}
  .label-sheet-title {{
    font-size: 14px;
    font-weight: 700;
    color: {RED};
    margin-bottom: 0.05in;
  }}
  .label-sheet-sub {{
    font-size: 9px;
    color: {MUTED};
    margin-bottom: 0.18in;
  }}
  .label-grid {{
    display: grid;
    grid-template-columns: repeat(3, 2.625in);
    gap: 0;
  }}
  .label {{
    width: 2.625in;
    height: 1in;
    padding: 0.08in 0.1in 0.06in 0.1in;
    display: flex;
    flex-direction: column;
    justify-content: center;
    overflow: hidden;
    border: 0.5px dotted #bbb;
  }}
  .label-name {{
    font-weight: 700;
    font-size: 10px;
    margin-bottom: 1px;
    color: {RED};
  }}
  .label-addr {{
    font-size: 9px;
    color: {TEXT};
    line-height: 1.3;
  }}

  .return-label .label-name {{
    color: {STEEL};
    font-size: 8px;
  }}
  .return-label .label-addr {{
    font-size: 7px;
    color: {MUTED};
  }}
</style>
</head>
<body>
"""

FOOTER = """\
</body>
</html>
"""

ENVELOPE_PANEL = """\
  <div class="envelope-panel">
    <div class="envelope-label">&#9654; Envelope address (cut &amp; tape or use label below)</div>
    <div class="envelope-addr">
      <strong>{NAME}</strong><br>
      {ADDR1}<br>
      {ADDR2}
    </div>
  </div>
"""


def make_labels(rows, label_type="destination"):
    """Return <label> HTML for each row."""
    parts = []
    for r in rows:
        name = r["county"].replace("-", " ").title()
        addr = r["mailing_address"]
        lines = addr.split(", ")
        if len(lines) == 2:
            addr1 = lines[0].strip()
            addr2 = lines[1].strip()
        elif len(lines) >= 3:
            addr1 = ", ".join(lines[:-1]).strip()
            addr2 = lines[-1].strip()
        else:
            addr1 = addr
            addr2 = ""

        if label_type == "destination":
            agency = f"{name} Sheriff's Office"
            parts.append(
                f'<div class="label">'
                f'<div class="label-name">{agency}</div>'
                f'<div class="label-addr">{addr1}<br>{addr2}</div>'
                f"</div>"
            )
        else:
            # Return address label — same as destination but to Montana Blotter
            parts.append(
                f'<div class="label return-label">'
                f'<div class="label-name">Montana Blotter</div>'
                f'<div class="label-addr">Great Falls, Montana<br>{PHONE} &middot; {URL}</div>'
                f"</div>"
            )
    return "".join(parts)


def main() -> None:
    with CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    red_rgb = "195, 66, 63"

    html = HEADER.format(
        BG=BG, TEXT=TEXT, RED=RED, STEEL=STEEL, AMBER=AMBER, RED_RGB=red_rgb, MUTED=MUTED,
    )

    # ── Letters + Envelope panels ────────────────────────────────
    for row in rows:
        if not row.get("county"):
            continue
        county_title = row["county"].replace("-", " ").title()
        addr = row["mailing_address"]
        addr_lines = addr.split(", ")
        addr1 = ", ".join(addr_lines[:-1]).strip() if len(addr_lines) >= 2 else addr
        addr2 = addr_lines[-1].strip() if addr_lines else ""

        letter = LETTER_BODY.format(
            TOWN=TOWN, PHONE=PHONE, EMAIL1=EMAIL1, EMAIL2=EMAIL2, URL=URL,
            TODAY=TODAY,
            COUNTY_TITLE=county_title,
            CONTACT_URL=row["contact_form_url"],
            PHONE_VALUE=row["phone"],
        )

        env = ENVELOPE_PANEL.format(
            NAME=f"{county_title} Sheriff's Office",
            ADDR1=addr1,
            ADDR2=addr2,
        )

        html += f'<div class="page-break">\n{letter}\n{env}\n</div>\n'

    # ── Address labels (Avery 5160, 30 per page) ────────────────
    html += '<div class="label-sheet">\n'
    html += '<div class="label-sheet-title">Montana Blotter &mdash; Address Labels</div>\n'
    html += '<div class="label-sheet-sub">Avery 5160 / 8160 &mdash; 3 cols &times; 10 rows = 30 labels per sheet</div>\n'
    html += '<div class="label-grid">\n'

    chips = []
    for row in rows:
        if not row.get("county"):
            continue
        county_title = row["county"].replace("-", " ").title()
        addr = row["mailing_address"]
        addr_lines = addr.split(", ")
        addr1 = ", ".join(addr_lines[:-1]).strip() if len(addr_lines) >= 2 else addr
        addr2 = addr_lines[-1].strip() if addr_lines else ""
        agency = f"{county_title} Sheriff's Office"
        chips.append(
            f'<div class="label">'
            f'<div class="label-name">{agency}</div>'
            f'<div class="label-addr">{addr1}<br>{addr2}</div>'
            f"</div>"
        )

    # Fill grid: 3 cols x 10 per page
    labels_per_page = 30
    for start in range(0, len(chips), labels_per_page):
        batch = chips[start : start + labels_per_page]
        if start > 0:
            html += '</div>\n</div>\n<div class="label-sheet page-break">\n<div class="label-grid">\n'
        html += "\n".join(batch)

    # Pad last page to full 30
    remaining = labels_per_page - (len(chips) % labels_per_page)
    if remaining and remaining < labels_per_page:
        for _ in range(remaining):
            html += '<div class="label" style="border:none;"></div>\n'

    html += "</div>\n</div>\n"

    # ── Return-address labels ────────────────────────────────────
    html += '<div class="label-sheet page-break">\n'
    html += '<div class="label-sheet-title">Montana Blotter &mdash; Return Address Labels</div>\n'
    html += '<div class="label-sheet-sub">Peel and stick on envelope top-left corner (Avery 5160)</div>\n'
    html += '<div class="label-grid">\n'

    return_chips = []
    return_addr = "Great Falls, Montana"
    return_line2 = f"{PHONE} &middot; {URL}"
    for _ in range(labels_per_page):
        return_chips.append(
            f'<div class="label return-label">'
            f'<div class="label-name">Montana Blotter</div>'
            f'<div class="label-addr">{return_addr}<br>{return_line2}</div>'
            f"</div>"
        )
    html += "\n".join(return_chips)

    html += "</div>\n</div>\n"

    html += FOOTER

    OUT.write_text(html, encoding="utf-8")
    print(f"✓ Wrote {OUT}")
    print(f"  {len(rows)} letter pages with envelope panels")
    print(f"  2 label pages (44 address + 30 return-address)")
    print(f"  Open in browser → Ctrl+P → Save as PDF / Print")


if __name__ == "__main__":
    main()
