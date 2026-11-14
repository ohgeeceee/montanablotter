import scrapy
from services.ingestion.fetchers.missoula import ingest_missoula_public_report as _run

class MissoulaBlotterSpider(scrapy.Spider):
    name = "missoula_blotter"
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
        self.logger.info("missoula blotter -> %s", result)
        yield {"result": str(result)}
