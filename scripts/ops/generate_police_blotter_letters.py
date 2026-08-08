"""Generate 44 professional mailing letters with Montana Blotter letterhead.

Output: docs/police_blotter_letters.html (print to PDF via browser)
Each letter is formatted for a standard #10 window envelope (4.125" x 9.5").
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "docs" / "police_blotter_contact_form_followup.csv"
OUT = ROOT / "docs" / "police_blotter_letters.html"

# Montana Blotter brand colors (from public-redesign.css)
COLOR_RED = "#c3423f"
COLOR_STEEL = "#29607b"
COLOR_AMBER = "#ad7a2d"
COLOR_BG = "#f4f1ea"
COLOR_TEXT = "#14212b"
COLOR_MUTED = "#5c6d78"

LETTERHEAD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Montana Blotter Police Blotter Records Requests</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:wght@700&family=Great+Vibes&display=swap" rel="stylesheet">
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
            page-break-inside: avoid;
            break-after: page;
            width: 8.5in;
            height: 11in;
            margin: 0 auto;
            padding: 0.5in 0.75in;
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
            padding-bottom: 0.4in;
            margin-bottom: 0.4in;
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
            margin-top: 0.3in;
            line-height: 1.4;
        }}
        .date {{
            font-size: 12px;
            color: {TEXT};
            margin-bottom: 0.3in;
            margin-top: 0.3in;
        }}
        .salutation {{
            font-size: 12px;
            margin-bottom: 0.2in;
            color: {TEXT};
        }}
        .body {{
            font-size: 11px;
            line-height: 1.7;
            color: {TEXT};
            margin-bottom: 0.3in;
        }}
        .body p {{
            margin-bottom: 0.2in;
        }}
        .closing {{
            font-size: 12px;
            margin-top: 0.3in;
            margin-bottom: 0.15in;
        }}
        .signature-space {{
            height: 0.4in;
        }}
        .signature-name {{
            font-family: "Great Vibes", cursive;
            font-size: 26px;
            font-weight: 400;
            color: {TEXT};
            margin-top: -0.1in;
        }}
        .agency-info {{
            background: rgba({RED_RGB}, 0.05);
            border-left: 3px solid {RED};
            padding: 0.2in 0.15in;
            margin: 0.3in 0 0.2in 0;
            font-size: 10px;
            line-height: 1.4;
        }}
        .agency-info-label {{
            font-weight: 600;
            color: {RED};
        }}
        .footer-line {{
            border-top: 1px solid {AMBER};
            padding-top: 0.15in;
            margin-top: 0.4in;
            font-size: 9px;
            color: {MUTED};
        }}
    </style>
</head>
<body>
"""

LETTER_TEMPLATE = """
    <div class="page-break">
        <div class="letterhead">
            <div class="letterhead-title">Montana Blotter</div>
            <div class="letterhead-tagline">Montana Public Records • Open & Free</div>
            <div class="letterhead-contact">
                Great Falls, Montana &nbsp;|&nbsp; 406-290-3337<br>
                records@montanablotter.com &nbsp;|&nbsp; support@montanablotter.com<br>
                https://montanablotter.com
            </div>
        </div>

        <div class="date">{DATE}</div>

        <div class="salutation">Dear Records Custodian:</div>

        <div class="body">
            <p>Montana Blotter is a free, open-source statewide public-records service. We aggregate daily police blotters from every Montana county into a single, searchable feed at https://montanablotter.com—making arrest and incident data accessible to the public, media, researchers, and civic watchdogs.</p>

            <p>We have been unable to locate a regularly updated, machine-readable police-blotter feed from {COUNTY} Sheriff's Office. Would your agency be willing to share a copy of your daily blotter with us through one of these channels?</p>

            <p style="margin-left: 0.2in; margin-right: 0.2in; margin-bottom: 0.15in;">
                <strong>Option 1.</strong> Daily email delivery to records@montanablotter.com in PDF, DOCX, CSV, or plain text<br>
                <strong>Option 2.</strong> Nightly FTP or HTTPS upload to a URL your agency provides<br>
                <strong>Option 3.</strong> Direct access to a daily-updated PDF or HTML page on your public website<br>
                <strong>Option 4.</strong> Authenticated API read of your records system
            </p>

            <p>For a daily police blotter, we extract: incident number, date and time, location, agency, incident type, and disposition (when available). We publish the official source, update time, and a direct link back to your agency with every entry. If you prefer we stop drawing from the feed, simply let us know and we will remove {COUNTY} from the pipeline.</p>

            <div class="agency-info">
                <span class="agency-info-label">Your records-request contact form:</span><br>
                {CONTACT_URL}<br>
                <span class="agency-info-label">Agency phone:</span> {PHONE}
            </div>

            <p>Please reply with any questions or a commitment to share the blotter. Also let us know the best contact person and preferred method for delivery issues or follow-up.</p>
        </div>

        <div class="closing">Sincerely,</div>

        <div class="signature-space"></div>

        <div class="signature-name">John Currie</div>

        <div class="footer-line">
            Representing Montana's 56 counties. Visit https://montanablotter.com to search over 1 million public records.
        </div>
    </div>
"""

FOOTER = """
</body>
</html>
"""


def main() -> None:
    today = date.today().strftime("%B %d, %Y")
    
    with CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    
    # Build RGB version for rgba() in CSS
    red_rgb = "195, 66, 63"
    
    html = LETTERHEAD.format(
        BG=COLOR_BG,
        TEXT=COLOR_TEXT,
        RED=COLOR_RED,
        STEEL=COLOR_STEEL,
        AMBER=COLOR_AMBER,
        RED_RGB=red_rgb,
        MUTED=COLOR_MUTED,
    )
    
    for row in rows:
        county_title = row["county"].replace("-", " ").title()
        letter = LETTER_TEMPLATE.format(
            DATE=today,
            COUNTY=county_title,
            CONTACT_URL=row["contact_form_url"],
            PHONE=row["phone"],
        )
        html += letter
    
    html += FOOTER
    
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({len(rows)} letters)")
    print(f"To print: open in Chrome/Firefox, Ctrl+P, select 'Save as PDF'")
    print(f"Print settings: 8.5\" x 11\", 0.5\" margins, scale 100%")


if __name__ == "__main__":
    main()
