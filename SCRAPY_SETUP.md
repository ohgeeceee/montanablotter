# Scrapy setup for mtblotter.com

Location: /opt/montanablotter (scrapy.cfg + mtblotter_crawl/ package)
Venv: /opt/montanablotter/venv (scrapy 2.19, scrapy-playwright 0.0.48, pdfminer.six, pypdf)
Chromium: /usr/bin/chromium-browser (system) — Playwright configured to use it

Commands:
  cd /opt/montanablotter
  venv/bin/scrapy list
  venv/bin/scrapy crawl <spider> --loglevel=INFO
  venv/bin/scrapy crawl <spider> -o out.json

Spiders dir: mtblotter_crawl/spiders/
Settings: mtblotter_crawl/settings.py (polite throttling, retry, Playwright, SQLite pipeline)
Items: mtblotter_crawl/items.py (JailBookingItem)
Pipeline: mtblotter_crawl/pipelines.py (SQLitePipeline -> blotter.db records table)

Migrate existing fetchers/* into spiders incrementally. Use meta={"playwright": True} for JS portals.
