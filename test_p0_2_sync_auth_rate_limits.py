"""P0-2: sync-metadata requires admin auth; analyze is rate limited."""
import os
import unittest
from unittest.mock import patch


class TestSyncMetadataAuth(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_unauthenticated_sync_metadata_rejected(self):
        with patch("requests.get") as net:
            r = self.client.post("/api/papers/1/sync-metadata")
        self.assertEqual(r.status_code, 401)
        net.assert_not_called()

    def test_non_admin_sync_metadata_forbidden(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = "researcher@test.org"
        r = self.client.post("/api/papers/1/sync-metadata")
        self.assertEqual(r.status_code, 403)


class TestRateLimits(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
        app_module.limiter.reset()

    def tearDown(self):
        self.app_module.limiter.reset()

    def _hammer(self, path, env_var, n=3, body=None):
        codes = []
        with patch.dict(os.environ, {env_var: f"{n} per minute"}):
            for _ in range(n + 1):
                codes.append(self.client.post(path, json=body if body is not None else {}).status_code)
        return codes

    def test_analyze_returns_429_over_limit(self):
        # Invalid filters -> 400 before any work is queued; the limiter still counts it.
        codes = self._hammer("/api/analyze", "RATE_LIMIT_ANALYZE", body={"filters": []})
        self.assertTrue(all(c != 429 for c in codes[:-1]), codes)
        self.assertEqual(codes[-1], 429)

    def test_limits_are_env_configurable(self):
        with patch.dict(os.environ, {"RATE_LIMIT_ANALYZE": "7 per hour"}):
            self.assertEqual(self.app_module.ANALYZE_RATE_LIMIT(), "7 per hour")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RATE_LIMIT_ANALYZE", None)
            self.assertIn("per minute", self.app_module.ANALYZE_RATE_LIMIT())


if __name__ == "__main__":
    unittest.main()
