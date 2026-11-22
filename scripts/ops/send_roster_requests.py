#!/usr/bin/env python3
"""Send personalized jail-roster / police-log request emails to MT agencies.

CSV input (default ./roster_request_targets.csv) with columns:
    agency,email,county,contact_name (optional)

SMTP creds come from env (DreamHost):
    MB_ROSTER_SMTP_SERVER (default smtp.dreamhost.com)
    MB_ROSTER_SMTP_PORT    (default 587)
    MB_ROSTER_SMTP_USER    (support@mtblotter.com)
    MB_ROSTER_SMTP_PASSWORD

Usage:
    python scripts/ops/send_roster_requests.py --csv targets.csv --dry-run
    python scripts/ops/send_roster_requests.py --csv targets.csv
"""
import argparse, csv, os, smtplib, ssl, time
from email.message import EmailMessage

TEMPLATE = """Hello {contact},

I operate Montana Blotter (mtblotter.com), a free public-records aggregator that publishes Montana jail bookings, incidents, and missing-persons information to help families, attorneys, and journalists find people quickly.

I'm writing to request that {agency} add support@mtblotter.com to the distribution list for your daily jail roster and/or police blotter / call log. We already receive feeds from many Montana counties and would like to include {county} County so residents have complete coverage.

Preferred format: any export you already produce (PDF, DOCX, CSV, or a public web link) — we ingest all of them. If your roster is published online, even a URL is enough.

This is a public-records request under Montana's right-to-know laws (MCA 2-6-1001 et seq.); we only republish information already available to the public and do not alter or editorialize the data.

Thank you,
Montana Blotter
support@mtblotter.com
https://mtblotter.com
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="roster_request_targets.csv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--delay", type=float, default=2.0, help="seconds between sends")
    ap.add_argument("--from-addr", default=os.getenv("MB_ROSTER_SMTP_USER", "support@mtblotter.com"))
    args = ap.parse_args()

    server = os.getenv("MB_ROSTER_SMTP_SERVER", "smtp.dreamhost.com")
    port = int(os.getenv("MB_ROSTER_SMTP_PORT", "587"))
    user = os.getenv("MB_ROSTER_SMTP_USER", args.from_addr)
    pwd = os.getenv("MB_ROSTER_SMTP_PASSWORD")
    if not pwd and not args.dry_run:
        raise SystemExit("MB_ROSTER_SMTP_PASSWORD not set")

    rows = list(csv.DictReader(open(args.csv, newline="", encoding="utf-8")))
    print(f"Loaded {len(rows)} targets from {args.csv}")

    if args.dry_run:
        for r in rows[:5]:
            print("---", r.get("agency"), r.get("email"))
            print(TEMPLATE.format(contact=r.get("contact_name") or "records team",
                                  agency=r.get("agency"), county=r.get("county")))
        print(f"... ({len(rows)} total, dry-run, no mail sent)")
        return

    ctx = ssl.create_default_context()
    with smtplib.SMTP(server, port, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(user, pwd)
        for i, r in enumerate(rows, 1):
            to = r["email"].strip()
            subj = f"Request for daily jail roster / police log feed — {r['county']} County"
            body = TEMPLATE.format(contact=r.get("contact_name") or "records team",
                                   agency=r["agency"], county=r["county"])
            msg = EmailMessage()
            msg["From"] = args.from_addr
            msg["To"] = to
            msg["Subject"] = subj
            msg.set_content(body)
            try:
                s.send_message(msg)
                print(f"[{i}/{len(rows)}] sent -> {to}")
            except Exception as e:
                print(f"[{i}/{len(rows)}] FAILED {to}: {e}")
            time.sleep(args.delay)
    print("Done.")

if __name__ == "__main__":
    main()
