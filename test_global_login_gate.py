"""Global login gate: non-public routes require a signed-in session."""
import unittest


class TestGlobalLoginGate(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_api_route_returns_401_when_logged_out(self):
        r = self.client.get("/api/dashboard-config")
        self.assertEqual(r.status_code, 401)
        self.assertTrue(r.get_json()["login_required"])

    def test_page_route_redirects_to_login_when_logged_out(self):
        r = self.client.get("/settings")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_public_routes_not_gated(self):
        from app import _is_public_path
        for path in ("/login", "/signup", "/verify-email", "/resend-verification",
                     "/auth/google", "/auth/google/callback", "/logout",
                     "/health", "/healthz", "/static/app.js"):
            self.assertTrue(_is_public_path(path), path)
        for path in ("/", "/settings", "/api/search", "/calibration/dashboard"):
            self.assertFalse(_is_public_path(path), path)
        r = self.client.get("/login")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/verify-email")
        self.assertNotEqual(r.status_code, 401)
        self.assertNotIn("/login?next", r.headers.get("Location", ""))

    def test_logged_in_user_passes_gate(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
        r = self.client.get("/api/dashboard-config")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
