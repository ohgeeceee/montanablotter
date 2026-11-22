import scrapy
from services.ingestion.fetchers.missoula_inmate import fetch_missoula_bookings as _fetch

class MissoulaInmateSpider(scrapy.Spider):
    name = "missoula_inmate"
    def start_requests(self):
        yield scrapy.Request("data:,", callback=self.run, dont_filter=True)
    def run(self, response):
        try:
            records = _fetch()
        except TypeError:
            records = _fetch(None)
        for r in (records or []):
            yield {
                "county": getattr(r, "county", "missoula_inmate"),
                "county_slug": getattr(r, "county_slug", "missoula_inmate".replace("_inmate","")),
                "inmate_name": getattr(r, "name", None) or getattr(r, "inmate_name", None),
                "booking_date": getattr(r, "booking_date", None) or getattr(r, "booking_at", None),
                "charges": getattr(r, "charges", None) or getattr(r, "charges_summary", None),
                "source_url": getattr(r, "source_url", None),
            }
