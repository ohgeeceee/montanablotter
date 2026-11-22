import scrapy
from services.ingestion.fetchers.rosebud_inmate import fetch_rosebud_bookings as _fetch

class RosebudInmateSpider(scrapy.Spider):
    name = "rosebud_inmate"
    def start_requests(self):
        yield scrapy.Request("data:,", callback=self.run, dont_filter=True)
    def run(self, response):
        try:
            records = _fetch()
        except TypeError:
            records = _fetch(None)
        for r in (records or []):
            yield {
                "county": getattr(r, "county", "rosebud_inmate"),
                "county_slug": getattr(r, "county_slug", "rosebud_inmate".replace("_inmate","")),
                "inmate_name": getattr(r, "name", None) or getattr(r, "inmate_name", None),
                "booking_date": getattr(r, "booking_date", None) or getattr(r, "booking_at", None),
                "charges": getattr(r, "charges", None) or getattr(r, "charges_summary", None),
                "source_url": getattr(r, "source_url", None),
            }
