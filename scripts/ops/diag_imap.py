#!/usr/bin/env python3
"""Diagnose IMAP connectivity/authentication for the configured accounts."""
import imaplib
import ssl
import sys

sys.path.insert(0, "/opt/montanablotter")
import config

user = (config.EMAIL_USER or "").strip()
password = (config.EMAIL_PASSWORD or "").strip()
print(f"user={user!r} password_len={len(password)}")

targets = []
if user:
    targets += [
        ("configured", config.IMAP_SERVER or "imap.ionos.com", int(config.IMAP_PORT or 993)),
        ("dreamhost", "imap.dreamhost.com", 993),
        ("mail.mtblotter.com", "mail.mtblotter.com", 993),
    ]

for label, host, port in targets:
    print(f"--- {label}: {host}:{port}")
    try:
        ctx = ssl.create_default_context()
        with imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=25) as m:
            print("   connected")
            try:
                m.login(user, password)
                print("   AUTH OK")
                typ, boxes = m.list()
                print("   folders:", len(boxes) if typ == "OK" else typ)
            except Exception as exc:
                print("   AUTH FAILED:", str(exc)[:160])
    except Exception as exc:
        print("   connect failed:", type(exc).__name__, str(exc)[:160])
