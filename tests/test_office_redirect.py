import unittest

import app as app_module


class OfficeRedirectTests(unittest.TestCase):
    def test_office_redirects_to_admin_hub(self) -> None:
        client = app_module.app.test_client()

        response = client.get("/office", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")

    def test_office_trailing_slash_redirects_to_admin_hub(self) -> None:
        client = app_module.app.test_client()

        response = client.get("/office/", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")


if __name__ == "__main__":
    unittest.main()
