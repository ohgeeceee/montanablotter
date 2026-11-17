#!/usr/bin/env python3
"""Check which mail credentials actually authenticate (SMTP + IMAP)."""
import smtplib
import ssl
import imaplib
import sys

sys.path.insert(0, "/opt/montanablotter")
import config

print("=== credential lengths ===")
for name in ("EMAIL_PASSWORD", "SMTP_PASSWORD", "ROSTER_SMTP_PASSWORD"):
    val = getattr(config, name, "") or ""
    print(f"  {name}: len={len(val)} looks_like_placeholder={set(val) <= {'*'}}")

print("\n=== SMTP (support@) login test ===")
try:
    ctx = ssl.create_default_context()
    with smtplib.SMTP(config.SMTP_SERVER, int(config.SMTP_PORT or 587), timeout=25) as s:
        s.starttls(context=ctx)
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        print("  SMTP AUTH OK -> mail sending works")
except Exception as exc:
    print("  SMTP AUTH FAILED:", str(exc)[:160])

print("\n=== IMAP candidates with configured creds ===")
user = (config.EMAIL_USER or "").strip()
pw = (config.EMAIL_PASSWORD or "").strip()
for host in ("imap.dreamhost.com", "imap.ionos.com"):
    try:
        with imaplib.IMAP4_SSL(host, 993, timeout=25) as m:
            m.login(user, pw)
            print(f"  {host}: AUTH OK")
    except Exception as exc:
        print(f"  {host}: {str(exc)[:100]}")
