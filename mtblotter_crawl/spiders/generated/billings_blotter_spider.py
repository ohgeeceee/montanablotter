import scrapy
from services.ingestion.fetchers.billings import ingest_blotter as _run

class BillingsBlotterSpider(scrapy.Spider):
    name = "billings_blotter"
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
        self.logger.info("billings blotter -> %s", result)
        yield {"result": str(result)}
