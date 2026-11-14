import scrapy
from services.ingestion.fetchers.whitefish import ingest_whitefish_blotters as _run

class WhitefishBlotterSpider(scrapy.Spider):
    name = "whitefish_blotter"
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
        self.logger.info("whitefish blotter -> %s", result)
        yield {"result": str(result)}
