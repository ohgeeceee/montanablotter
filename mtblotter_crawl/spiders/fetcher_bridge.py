"""Bridge spider: runs an existing services.ingestion.fetchers module under Scrapy.

Usage:
    venv/bin/scrapy crawl fetcher_bridge -a fetcher=beaverhead_inmate -a county="Beaverhead"
"""
import importlib
import scrapy


class FetcherBridgeSpider(scrapy.Spider):
    name = "fetcher_bridge"

    def __init__(self, fetcher=None, county=None, **kwargs):
        super().__init__(**kwargs)
        self.fetcher_name = fetcher
        self.county = county or fetcher

    def start_requests(self):
        # One synthetic request to trigger the fetcher; actual HTTP done by fetcher
        yield scrapy.Request("data:,", callback=self.run_fetcher, dont_filter=True)

    def run_fetcher(self, response):
        mod = importlib.import_module(f"services.ingestion.fetchers.{self.fetcher_name}")
        # Most fetchers expose a fetch() or run() returning list[JailBookingRecord]
        fn = getattr(mod, "fetch", None) or getattr(mod, "run", None) or getattr(mod, "get_roster", None)
        if fn is None:
            self.logger.error("No fetch/run/get_roster in %s", self.fetcher_name)
            return
        try:
            records = fn()
        except TypeError:
            records = fn(None)
        for r in (records or []):
            yield {
                "county": getattr(r, "county", self.county),
                "county_slug": getattr(r, "county_slug", str(self.county).lower().replace(" ", "_")),
                "inmate_name": getattr(r, "name", None) or getattr(r, "inmate_name", None),
                "booking_date": getattr(r, "booking_date", None) or getattr(r, "booking_at", None),
                "charges": getattr(r, "charges", None) or getattr(r, "charges_summary", None),
                "source_url": getattr(r, "source_url", None),
            }
