"""Generate a printable police-blotter records request packet.

One per county: contact-form URL, phone, mailing address, and the
records-request paragraph. The operator fills the envelope, signs,
and drops the letter in the mail.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "docs" / "police_blotter_contact_form_followup.csv"
OUT = ROOT / "docs" / "poloebloter_printable_packet.txt"
RETURN_TO = "Montana Blotter, PO Box 1174, Helena, MT 59624"
EMAIL = "records@montanablotter.com"


def paragraph() -> str:
    return (
        "Montana Blotter is a free, open-source Montana public-records service. We collect new daily "
        "police-blotter entries from every Montana county and present them in a single, searchable feed at "
        "https://montanablotter.com. We could not locate a daily, machine-readable current police-blotter "
        "feed for your office. Would your agency share a copy of the daily blotter through one of these "
        "channels: (1) a daily email delivery to records@montanablotter.com in PDF, DOCX, CSV, or plain-text; "
        "(2) a nightly FTP or HTTPS upload to a URL you provide; (3) a daily updated PDF or HTML page on the "
        "agency site. For a daily blotter we extract incident number, date and time, location, agency, "
        "incident type, and disposition when available. We publish the official source and update time with the "
        "data, link back to the agency, and stop drawing from the feed if you tell us to. "
        "If a public records request form is the right path, here is the agency link: __CONTACT_URL__\n\n"
        "Please also tell us the best contact for delivery issues and how you would prefer follow-up questions."
    )


def main() -> None:
    today = date.today().isoformat()
    with CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    blocks: list[str] = []
    blocks.append(f"Montana Blotter police-blotter records-request packet — generated {today}\n")
    blocks.append("Return completed or scanned responses to:\n")
    blocks.append(f"  {RETURN_TO}\n  {EMAIL}\n")
    for row in rows:
        body = paragraph().replace("__CONTACT_URL__", row["contact_form_url"])
        address = row["mailing_address"]
        blocks.append("=" * 72)
        blocks.append(f"Section {row['script_section']}: {row['county'].title()} County Sheriff")
        blocks.append("=" * 72)
        blocks.append(f"Mail one printed copy of this section to:\n  {address}")
        blocks.append(f"Agency contact form (if used): {row['contact_form_url']}")
        blocks.append(f"Agency phone (for follow-up): {row['phone']}\n")
        blocks.append("Montana Blotter\n" + RETURN_TO + f"\n{date.today().strftime('%B %d, %Y')}\n")
        blocks.append("Dear Records Custodian,\n")
        blocks.append(body + "\n")
        blocks.append("Sincerely,\nMontana Blotter records team")
        blocks.append("")
    OUT.write_text("\n".join(blocks), encoding="utf-8")
    print(f"wrote {OUT} sections={len(rows)}")


if __name__ == "__main__":
    main()
