"""Open-redirect hardening for next= and /health(z) routes."""
import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSafeNextUrl(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_rejected(self):
        from app import _safe_next_url
        for bad in ("/%09/evil.com", "/\t/evil.com", "/\\evil.com", "//evil.com",
                    "https://evil.com", "/%0d%0a//evil.com", "/\r\n//evil.com", ""):
            self.assertEqual(_safe_next_url(bad), "/", repr(bad))

    def test_allowed(self):
        from app import _safe_next_url
        self.assertEqual(_safe_next_url("/settings?x=1"), "/settings?x=1")

    def test_login_redirect_does_not_escape(self):
        for q in ("/%09/evil.com", "/%5Cevil.com", "//evil.com",
                  "https://evil.com", "/%0d%0a//evil.com"):
            with self.client.session_transaction() as s:
                s["logged_in"] = True
            r = self.client.get("/login?next=" + q)
            self.assertEqual(r.status_code, 302)
            loc = r.headers["Location"]
            self.assertNotIn("evil.com", loc, q)

    def test_health_public(self):
        with self.client.session_transaction() as s:
            s.clear()
        for path in ("/health", "/healthz"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.get_json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
