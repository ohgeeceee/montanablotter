#!/usr/bin/env python3
import requests, re, html as ihtml

url = "https://www.yellowstonecountymt.gov/sheriff/detention/dcsearch.asp"
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; MontanaBlotter/1.0; +https://montanablotter.com)"})
page = s.get(url, timeout=45).text

def _text_from_html(fragment):
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = ihtml.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

m = re.search(r'<table class="table table-striped _table-sm caption-top data-table">(.*?)</table>', page, re.I | re.S)
if m:
    print("found table in initial GET")
else:
    print("no table in initial GET")
    label_match = re.search(r'<label for="Answer"[^>]*>([^<]+)</label>', page)
    if label_match:
        label = _text_from_html(label_match.group(1))
        print("prompt:", label)
    resp = s.post(url, data={"ViewFullRoster": "True", "Answer": "16", "action": "Search"}, timeout=45)
    m2 = re.search(r'<table class="table table-striped _table-sm caption-top data-table">(.*?)</table>', resp.text, re.I | re.S)
    if m2:
        print("found table after POST")
        rows = re.findall(r"<tr>(.*?)</tr>", m2.group(1), re.I | re.S)
        print("rows:", len(rows))
        if rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", rows[0], re.I | re.S)
            print("first row cells:", [_text_from_html(c) for c in cells])
    else:
        print("no table after POST")
