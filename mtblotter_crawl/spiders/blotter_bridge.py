"""Bridge spiders for Montana police-blotter fetchers (run their own ingestion pipeline)."""
import importlib
import scrapy


class BlotterBridgeSpider(scrapy.Spider):
    name = "blotter_bridge"

    def __init__(self, fetcher=None, entry=None, **kwargs):
        super().__init__(**kwargs)
        self.fetcher_name = fetcher
        self.entry_name = entry

    def start_requests(self):
        yield scrapy.Request("data:,", callback=self.run, dont_filter=True)

    def run(self, response):
        mod = importlib.import_module(f"services.ingestion.fetchers.{self.fetcher_name}")
        fn = getattr(mod, self.entry_name)
        try:
            result = fn()
        except TypeError:
            result = fn(dry_run=False)
        self.logger.info("blotter bridge %s -> %s", self.fetcher_name, result)
        yield {"result": str(result)}
