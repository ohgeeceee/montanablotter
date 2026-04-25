import unittest


class NewsSourceRegistryTests(unittest.TestCase):
    def test_registry_includes_montanablotter_source(self) -> None:
        from news_source_registry import APPROVED_NEWS_SOURCES

        self.assertTrue(any(item["source_type"] == "montanablotter" for item in APPROVED_NEWS_SOURCES))

    def test_registry_includes_public_records_source(self) -> None:
        from news_source_registry import APPROVED_NEWS_SOURCES

        self.assertTrue(any(item["source_type"] == "montana_public_records" for item in APPROVED_NEWS_SOURCES))

    def test_is_allowed_source_url_accepts_government_records_domain(self) -> None:
        from news_source_registry import is_allowed_source_url

        self.assertTrue(is_allowed_source_url("https://courts.mt.gov/external/orders/dailyorders"))

    def test_is_allowed_source_url_rejects_unknown_domain(self) -> None:
        from news_source_registry import is_allowed_source_url

        self.assertFalse(is_allowed_source_url("https://example.com/story"))

    def test_is_allowed_source_url_rejects_commercial_news_domain(self) -> None:
        from news_source_registry import is_allowed_source_url

        self.assertFalse(is_allowed_source_url("https://www.ktvq.com/news/montana-news/story"))


if __name__ == "__main__":
    unittest.main()
