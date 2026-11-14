import scrapy
from services.ingestion.fetchers.crime_mapping import fetch_incidents as _fetch

class CrimeMappingSpider(scrapy.Spider):
    name = "crime_mapping"
    def start_requests(self):
        yield scrapy.Request("data:,", callback=self.run, dont_filter=True)
    def run(self, response):
        try:
            records = _fetch()
        except TypeError:
            records = _fetch(None)
        for r in (records or []):
            yield {
                "county": getattr(r, "county", "crime_mapping"),
                "county_slug": getattr(r, "county_slug", "crime_mapping".replace("_inmate","")),
                "inmate_name": getattr(r, "name", None) or getattr(r, "inmate_name", None),
                "booking_date": getattr(r, "booking_date", None) or getattr(r, "booking_at", None),
                "charges": getattr(r, "charges", None) or getattr(r, "charges_summary", None),
                "source_url": getattr(r, "source_url", None),
            }
