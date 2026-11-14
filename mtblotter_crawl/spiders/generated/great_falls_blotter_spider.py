import scrapy
from services.ingestion.fetchers.great_falls import ingest_blotter as _run

class GreatFallsBlotterSpider(scrapy.Spider):
    name = "great_falls_blotter"
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
        self.logger.info("great_falls blotter -> %s", result)
        yield {"result": str(result)}
