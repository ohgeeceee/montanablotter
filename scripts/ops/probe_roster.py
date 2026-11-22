#!/usr/bin/env python3
"""Probe county jail roster pages and summarize what a parser would see."""
import re
import sys
import html
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def text_of(fragment):
    txt = re.sub(r"<script.*?</script>", " ", fragment, flags=re.S | re.I)
    txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", html.unescape(txt)).strip()


for url in sys.argv[1:]:
    print(f"=== {url}")
    try:
        status, body = get(url)
        print("status:", status, "bytes:", len(body))
        m = re.search(r"<main[^>]*>(.*?)</main>", body, re.S | re.I)
        seg = m.group(1) if m else body
        print("text:", text_of(seg)[:500])
        links = sorted(set(re.findall(r'href="([^"]*(?:roster|inmate|jms)[^"]*)"', body, re.I)))
        print("roster-ish links:", links[:8])
    except Exception as exc:
        print("ERR", type(exc).__name__, str(exc)[:160])
    print()
