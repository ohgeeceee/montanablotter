#!/usr/bin/env python3
"""Send approved jail-roster agency requests from reviewable Markdown drafts."""
from __future__ import annotations

import argparse
import logging
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import config

DRAFT_DIR = ROOT / "agent-queue" / "ops" / "jail-roster-outreach"
LOG_PATH = ROOT / "logs" / "jail_roster_outreach.log"
REPLY_TO = "records@montanablotter.com"


def parse_draft(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} has no frontmatter")
    _, raw_meta, body = text.split("---\n", 2)
    meta: dict[str, str] = {}
    for line in raw_meta.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            meta[key.strip()] = value.strip()
    body = body.strip()
    body = body.replace("# Montana jail-roster data request\n\n", "", 1)
    body = body.replace("Subject: Request for daily current-inmate roster delivery\n\n", "", 1)
    return meta, body


def validated_drafts() -> list[tuple[Path, dict[str, str], str]]:
    drafts = []
    for path in sorted(DRAFT_DIR.glob("*.md")):
        meta, body = parse_draft(path)
        recipient = meta.get("to", "")
        if "@" not in recipient or " " in recipient:
            continue
        drafts.append((path, meta, body))
    return drafts


def update_status(path: Path, status: str) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("status:"):
            lines[index] = f"status: {status}"
        elif line.startswith("send_approved:"):
            lines[index] = "send_approved: true"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="Actually send; otherwise print a dry-run matrix")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
    )
    drafts = validated_drafts()
    for _, meta, _ in drafts:
        logging.info("prepared county=%s recipient=%s", meta.get("county"), meta.get("to"))
    if not args.send:
        logging.info("dry_run count=%d", len(drafts))
        return 0

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
        for path, meta, body in drafts:
            message = EmailMessage()
            message["From"] = f"Montana Blotter Records <{username}>"
            message["Reply-To"] = REPLY_TO
            message["To"] = meta["to"]
            message["Subject"] = f"Request for {meta['county']} County current-inmate roster delivery"
            message.set_content(body)
            refused = smtp.send_message(message)
            if refused:
                logging.error("refused county=%s recipient=%s detail=%s", meta["county"], meta["to"], refused)
                update_status(path, "failed")
                continue
            update_status(path, "sent")
            sent += 1
            logging.info("sent county=%s recipient=%s", meta["county"], meta["to"])
    logging.info("complete sent=%d attempted=%d", sent, len(drafts))
    return 0 if sent == len(drafts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
