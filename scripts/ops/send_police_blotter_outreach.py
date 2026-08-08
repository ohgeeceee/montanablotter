"""Montana police-blotter records-request queue.

Sends a records-request email to every county with a verified email address.
Produces reviewable drafts for counties that need phone or contact-form follow-up.

Run:
    ./venv/bin/python3 scripts/ops/send_police_blotter_outreach.py --send
"""
from __future__ import annotations

import argparse
import csv
import logging
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import config

TEMPLATE = ROOT / "docs" / "templates" / "police_blotter_data_request.md"
DRAFT_DIR = ROOT / "agent-queue" / "ops" / "police-blotter-outreach"
LOG_PATH = ROOT / "logs" / "police_blotter_outreach.log"
REPLY_TO = "records@montanablotter.com"

COUNTY_DIRECTORY = {
    "beaverhead": "Beaverhead County Sheriff", "big-horn": "Big Horn County Sheriff",
    "blaine": "Blaine County Sheriff/Coroner", "broadwater": "Broadwater County Sheriff",
    "carbon": "Carbon County Sheriff", "carter": "Carter County Sheriff",
    "cascade": "Cascade County Sheriff", "chouteau": "Chouteau County Sheriff",
    "custer": "Custer County Sheriff", "daniels": "Daniels County Sheriff/Coroner",
    "dawson": "Dawson County Sheriff", "deer-lodge": "Deer Lodge County Sheriff",
    "fallon": "Fallon County Sheriff", "fergus": "Fergus County Sheriff",
    "flathead": "Flathead County Sheriff", "gallatin": "Gallatin County Sheriff",
    "garfield": "Garfield County Sheriff", "glacier": "Glacier County Sheriff",
    "golden-valley": "Golden Valley County Sheriff", "granite": "Granite County Sheriff",
    "hill": "Hill County Sheriff", "jefferson": "Jefferson County Sheriff",
    "judith-basin": "Judith Basin County Sheriff", "lake": "Lake County Sheriff",
    "lewis-and-clark": "Lewis and Clark County Sheriff", "liberty": "Liberty County Sheriff",
    "lincoln": "Lincoln County Sheriff", "madison": "Madison County Sheriff",
    "mccone": "McCone County Sheriff", "meagher": "Meagher County Sheriff",
    "mineral": "Mineral County Sheriff", "missoula": "Missoula County Sheriff",
    "musselshell": "Musselshell County Sheriff", "park": "Park County Sheriff",
    "petroleum": "Petroleum County Sheriff", "phillips": "Phillips County Sheriff",
    "pondera": "Pondera County Sheriff", "powder-river": "Powder River County Sheriff",
    "powell": "Powell County Sheriff", "prairie": "Prairie County Sheriff",
    "ravalli": "Ravalli County Sheriff", "richland": "Richland County Sheriff",
    "roosevelt": "Roosevelt County Sheriff", "rosebud": "Rosebud County Sheriff",
    "sanders": "Sanders County Sheriff", "sheridan": "Sheridan County Sheriff",
    "silver-bow": "Butte-Silver Bow Sheriff", "stillwater": "Stillwater County Sheriff",
    "sweet-grass": "Sweet Grass County Sheriff", "teton": "Teton County Sheriff",
    "toole": "Toole County Sheriff", "treasure": "Treasure County Sheriff",
    "valley": "Valley County Sheriff", "wheatland": "Wheatland County Sheriff",
    "wibaux": "Wibaux County Sheriff", "yellowstone": "Yellowstone County Sheriff",
}

# Confirmed from the previous research delegations. Counties without an email
# fall through to the contact-form / phone follow-up draft path.
KNOWN_EMAILS = {
    "carter": "ccso@midrivers.com",
    "daniels": "sheriff@danielscomt.us",
    "garfield": "clerk@garfieldcountymt.gov",
    "granite": "chrisr@granitecosheriff.org",
    "hill": "moxley@hillso.org",
    "liberty": "sheriff@libertycountymt.gov",
    "mccone": "mcso@mcconecountymt.gov",
    "petroleum": "sheriff@petroleummt.gov",
    "pondera": "robert.skorupa@ponderacountymt.gov",
    "prairie": "info@prairiecountymt.gov",
    "sweet-grass": "aronneberg@sgcountymt.gov",
}


def build_body(county_slug: str, contact_form_url: str) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    return (template
            .replace("[COUNTY]", COUNTY_DIRECTORY[county_slug])
            .replace("[CONTACT_FORM_URL]", contact_form_url or ""))


def generate_drafts() -> int:
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for slug, name in COUNTY_DIRECTORY.items():
        target = DRAFT_DIR / f"{slug}.md"
        frontmatter = (
            "---\n"
            f"county: {name}\n"
            f"slug: {slug}\n"
            f"to: {KNOWN_EMAILS.get(slug, '')}\n"
            "phone_only: false\n"
            "status: draft\n"
            "send_approved: false\n"
            "---\n\n"
        )
        target.write_text(frontmatter + build_body(slug, "https://www.lakemt.gov/directory.aspx") + "\n", encoding="utf-8")
        written += 1
    return written


def send_queued() -> int:
    server = str(config.SMTP_SERVER or "").strip()
    port = int(config.SMTP_PORT or 587)
    username = str(config.SMTP_USER or "").strip()
    password = str(config.SMTP_PASSWORD or "")
    if not all((server, username, password)):
        logging.error("SMTP configuration is incomplete")
        return 2

    sent = 0
    with smtplib.SMTP(server, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(username, password)
        for slug, recipient in KNOWN_EMAILS.items():
            target = DRAFT_DIR / f"{slug}.md"
            body = build_body(slug, "https://www.lakemt.gov/directory.aspx")
            message = EmailMessage()
            message["From"] = f"Montana Blotter Records <{username}>"
            message["Reply-To"] = REPLY_TO
            message["To"] = recipient
            message["Subject"] = f"Request for {COUNTY_DIRECTORY[slug]} daily police blotter feed"
            message.set_content(body)
            refused = smtp.send_message(message)
            if refused:
                logging.error("refused slug=%s recipient=%s detail=%s", slug, recipient, refused)
                continue
            sent += 1
            logging.info("sent slug=%s recipient=%s", slug, recipient)
    return sent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="Actually send; otherwise just regenerate drafts")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
    )
    drafts = generate_drafts()
    logging.info("drafted count=%d", drafts)
    if not args.send:
        return 0
    sent = send_queued()
    logging.info("complete sent=%d eligible=%d", sent, len(KNOWN_EMAILS))
    return 0 if sent == len(KNOWN_EMAILS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
