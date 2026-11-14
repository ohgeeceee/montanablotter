# Scrapy settings for mtblotter_crawl
BOT_NAME = "mtblotter_crawl"
SPIDER_MODULES = ["mtblotter_crawl.spiders"]
NEWSPIDER_MODULE = "mtblotter_crawl.spiders"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = 1.5
RANDOMIZE_DOWNLOAD_DELAY = True
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 10.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (compatible; MontanaBlotterBot/1.0; +https://montanablotter.com/about)",
}
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {"headless": True, "executable_path": "/snap/bin/chromium"}
ITEM_PIPELINES = {"mtblotter_crawl.pipelines.SQLitePipeline": 300}
MTBLOTTER_DB_PATH = "/opt/montanablotter/blotter.db"
LOG_LEVEL = "INFO"
LOG_FILE = "/opt/montanablotter/logs/scrapy.log"
FEED_EXPORT_ENCODING = "utf-8"
