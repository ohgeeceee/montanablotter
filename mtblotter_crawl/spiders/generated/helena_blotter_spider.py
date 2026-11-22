import scrapy
from services.ingestion.fetchers.helena import ingest_blotter as _run

class HelenaBlotterSpider(scrapy.Spider):
    name = "helena_blotter"
    def start_requests(self):
        yield scrapy.Request("data:,", callback=self.run, dont_filter=True)
    def run(self, response):
        try:
            result = _run()
        except TypeError:
            try:
                result = _run(dry_run=False)
            except TypeError:
                result = _run(None)
        self.logger.info("helena blotter -> %s", result)
        yield {"result": str(result)}
