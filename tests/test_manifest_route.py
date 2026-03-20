"""Verify /manifest.json route returns correct content and headers."""
import json
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class ManifestRouteTest(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_manifest_returns_200(self):
        resp = self.client.get("/manifest.json")
        self.assertEqual(resp.status_code, 200)

    def test_manifest_content_type(self):
        resp = self.client.get("/manifest.json")
        self.assertIn("manifest", resp.content_type)

    def test_manifest_has_required_fields(self):
        resp = self.client.get("/manifest.json")
        data = json.loads(resp.data)
        for field in ("name", "short_name", "start_url", "display", "icons"):
            self.assertIn(field, data, f"manifest missing field: {field}")

    def test_manifest_display_is_standalone(self):
        resp = self.client.get("/manifest.json")
        data = json.loads(resp.data)
        self.assertEqual(data["display"], "standalone")

    def test_manifest_theme_color(self):
        resp = self.client.get("/manifest.json")
        data = json.loads(resp.data)
        self.assertEqual(data["theme_color"], "#D4892A")


if __name__ == "__main__":
    unittest.main()
