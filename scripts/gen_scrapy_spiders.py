import os, re, importlib
FETCHERS = "services/ingestion/fetchers"
OUT = "mtblotter_crawl/spiders/generated"
os.makedirs(OUT, exist_ok=True)
CANDIDATES = ["fetch", "run", "get_roster", "fetch_bookings", "fetch_roster"]

entries = []
for f in sorted(os.listdir(FETCHERS)):
    if not f.endswith(".py") or f.startswith("_"):
        continue
    name = f[:-3]
    try:
        mod = importlib.import_module(f"services.ingestion.fetchers.{name}")
    except Exception as e:
        print("SKIP (import error):", name, str(e)[:80])
        continue
    fn_name = None
    for c in CANDIDATES:
        if hasattr(mod, c):
            fn_name = c; break
    if not fn_name:
        # find any callable starting with fetch_
        for attr in dir(mod):
            if attr.startswith("fetch_") and callable(getattr(mod, attr)):
                fn_name = attr; break
    if not fn_name:
        print("SKIP (no entry):", name)
        continue
    entries.append((name, fn_name))

for name, fn in entries:
    slug = name.replace("_inmate", "").replace("_", "")
    spider_name = name
    code = f'''import scrapy
from services.ingestion.fetchers.{name} import {fn} as _fetch

class {name.title().replace("_", "")}Spider(scrapy.Spider):
    name = "{spider_name}"
    def start_requests(self):
        yield scrapy.Request("data:,", callback=self.run, dont_filter=True)
    def run(self, response):
        try:
            records = _fetch()
        except TypeError:
            records = _fetch(None)
        for r in (records or []):
            yield {{
                "county": getattr(r, "county", "{name}"),
                "county_slug": getattr(r, "county_slug", "{name}".replace("_inmate","")),
                "inmate_name": getattr(r, "name", None) or getattr(r, "inmate_name", None),
                "booking_date": getattr(r, "booking_date", None) or getattr(r, "booking_at", None),
                "charges": getattr(r, "charges", None) or getattr(r, "charges_summary", None),
                "source_url": getattr(r, "source_url", None),
            }}
'''
    with open(os.path.join(OUT, f"{name}_spider.py"), "w") as fh:
        fh.write(code)

# __init__
with open(os.path.join(OUT, "__init__.py"), "w") as fh:
    fh.write("")
print("Generated", len(entries), "spiders")
