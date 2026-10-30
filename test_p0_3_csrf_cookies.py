"""P0-3: CSRF protection on mutating requests and hardened session cookie flags."""
import os
import re
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
# Fake values that satisfy #89's prod boot checks (boot_safety.validate_startup_env).
PROD_FAKE = {"FLASK_SECRET_KEY": "fake-" + "k" * 40, "GOOGLE_CLIENT_ID": "fake-client-id",
             "GOOGLE_CLIENT_SECRET": "fake-client-secret", "RATE_LIMIT_SINGLE_INSTANCE": "1"}


class TestCsrf(unittest.TestCase):
    def setUp(self):
        from app import app
        self.app = app
        app.config["TESTING"] = True
        self._prev = app.config.get("WTF_CSRF_ENABLED")
        app.config["WTF_CSRF_ENABLED"] = True
        self.client = app.test_client()

    def tearDown(self):
        self.app.config["WTF_CSRF_ENABLED"] = self._prev

    def _token(self):
        return self.client.get("/api/csrf-token").get_json()["csrf_token"]

    def test_json_post_without_token_rejected(self):
        r = self.client.post("/api/analyze", json={})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.get_json()["csrf_error"])

    def test_admin_post_without_token_rejected(self):
        r = self.client.post("/api/papers/delete", json={"ids": [1]})
        self.assertEqual(r.status_code, 400)

    def test_form_login_without_token_rejected(self):
        r = self.client.post("/login", data={"username_or_email": "x", "password": "y"})
        self.assertEqual(r.status_code, 400)

    def test_post_with_header_token_passes_csrf(self):
        r = self.client.post("/api/papers/delete", json={"ids": [1]}, headers={"X-CSRFToken": self._token()})
        self.assertEqual(r.status_code, 401)  # reached the auth decorator, so CSRF passed

    def test_form_with_token_passes_csrf(self):
        r = self.client.post("/login", data={"username_or_email": "nobody@x", "password": "y",
                                             "csrf_token": self._token()})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Invalid username/email or password", r.get_data(as_text=True))

    def test_wrong_token_rejected(self):
        self._token()
        r = self.client.post("/api/papers/delete", json={}, headers={"X-CSRFToken": "bogus"})
        self.assertEqual(r.status_code, 400)

    def test_google_callback_post_needs_g_csrf_double_submit(self):
        r = self.client.post("/auth/google/callback", data={"credential": "a.b.c"})
        self.assertEqual(r.status_code, 401)  # route is @csrf.exempt; Google's own double-submit rejects it
        self.assertIn("CSRF check failed", r.get_data(as_text=True))

    def test_pages_embed_token(self):
        html = self.client.get("/login").get_data(as_text=True)
        self.assertIn('name="csrf_token"', html)
        with open(os.path.join(REPO, "templates", "index.html"), encoding="utf-8") as f:
            index = f.read()
        self.assertIn('meta name="csrf-token"', index)
        self.assertIn("X-CSRFToken", index)

    def test_calibration_dashboard_js_sends_token(self):
        with open(os.path.join(REPO, "calibration_metrics.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r"/api/csrf-token")
        self.assertIn("'X-CSRFToken': csrfToken", src)

    def test_prod_cannot_disable_csrf_via_env(self):
        import subprocess, sys
        env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "FLASK_ENV", "RATELIMIT_STORAGE_URI")}
        env.update({"WTF_CSRF_ENABLED": "0", **PROD_FAKE})
        r = subprocess.run([sys.executable, "-c", "import app; print(app.app.config['WTF_CSRF_ENABLED'])"],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "True", r.stderr[-1500:])

    def test_app_env_production_overrides_flask_env_test(self):
        import subprocess, sys
        env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "FLASK_ENV", "RATELIMIT_STORAGE_URI")}
        env.update({"APP_ENV": "production", "FLASK_ENV": "test", "WTF_CSRF_ENABLED": "0", **PROD_FAKE})
        r = subprocess.run([sys.executable, "-c", "import app; print(app.app.config['WTF_CSRF_ENABLED'])"],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(r.stdout.strip().splitlines()[-1], "True", r.stderr[-1500:])


class TestCookieFlags(unittest.TestCase):
    def test_config_flags(self):
        import subprocess, sys
        env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "FLASK_ENV", "SESSION_COOKIE_SECURE", "RATELIMIT_STORAGE_URI")}
        env.update(PROD_FAKE)
        code = ("import app; c=app.app.config; "
                "print(c['SESSION_COOKIE_SECURE'], c['SESSION_COOKIE_HTTPONLY'], c['SESSION_COOKIE_SAMESITE'])")
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "True True Lax", r.stderr[-1500:])
        env["SESSION_COOKIE_SECURE"] = "0"
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "False True Lax")

    def test_set_cookie_header_has_flags(self):
        from app import app
        prev = app.config["SESSION_COOKIE_SECURE"]
        app.config["SESSION_COOKIE_SECURE"] = True
        try:
            client = app.test_client()
            r = client.get("/api/csrf-token", base_url="https://localhost")
            cookie = next(h for h in r.headers.getlist("Set-Cookie") if h.startswith(app.config.get("SESSION_COOKIE_NAME", "session") + "="))
            self.assertRegex(cookie, r"(?i);\s*Secure")
            self.assertRegex(cookie, r"(?i);\s*HttpOnly")
            self.assertRegex(cookie, r"(?i);\s*SameSite=Lax")
        finally:
            app.config["SESSION_COOKIE_SECURE"] = prev


if __name__ == "__main__":
    unittest.main()
