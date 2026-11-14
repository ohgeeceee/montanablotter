import scrapy

class ExampleCountySpider(scrapy.Spider):
    """Template spider for a JS-rendered county jail roster portal."""
    name = "example_county"
    # Set meta={"playwright": True} per-request for JS pages
    start_urls = ["https://example.com/roster"]

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(url, meta={"playwright": True})

    def parse(self, response):
        # Example: extract inmate rows from a table
        for row in response.css("table.roster tr"):
            yield {
                "county": "Example",
                "inmate_name": row.css("td.name::text").get(),
                "booking_date": row.css("td.date::text").get(),
                "charges": row.css("td.charges::text").get(),
                "source_url": response.url,
            }
