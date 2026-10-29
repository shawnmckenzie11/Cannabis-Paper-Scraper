"""P0-1: fail closed on missing FLASK_SECRET_KEY; admin is checked server-side."""
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))


def _boot(env_overrides):
    env = {k: v for k, v in os.environ.items() if k not in ("FLASK_SECRET_KEY", "APP_ENV", "FLASK_ENV")}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-c", "import app; print('BOOTED')"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )


class TestSecretKeyFailClosed(unittest.TestCase):
    def test_boot_fails_without_key_in_prod(self):
        r = _boot({})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FLASK_SECRET_KEY is not set", r.stderr)

    def test_boot_fails_with_empty_key(self):
        r = _boot({"FLASK_SECRET_KEY": "   "})
        self.assertNotEqual(r.returncode, 0)

    def test_boot_ok_with_key(self):
        r = _boot({"FLASK_SECRET_KEY": "x" * 40})
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("BOOTED", r.stdout)

    def test_dev_mode_generates_key(self):
        r = _boot({"APP_ENV": "development"})
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def test_old_hardcoded_key_gone(self):
        with open(os.path.join(REPO, "app.py"), encoding="utf-8") as f:
            self.assertNotIn("mckenzian-secret-key-12345", f.read())


class TestAdminServerSide(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_forged_admin_email_without_user_id_refused(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = "shawnmckenzie11.sm@gmail.com"
        r = self.client.post("/api/papers/delete", json={})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("shawnmckenzie11", r.get_data(as_text=True))

    def test_non_admin_user_with_forged_admin_email_refused(self):
        from auth_test_helpers import login_session
        user = login_session(self.client, "p0-researcher@test.org")
        with self.client.session_transaction() as sess:
            sess["email"] = "shawnmckenzie11.sm@gmail.com"  # forged; DB row says otherwise
        self.assertNotEqual(user["email"], "shawnmckenzie11.sm@gmail.com")
        r = self.client.post("/api/papers/delete", json={})
        self.assertEqual(r.status_code, 403)

    def test_unverified_admin_email_account_refused(self):
        from auth_test_helpers import login_session
        login_session(self.client, "p0-unverified-admin@test.org", verified=0)
        import app as app_module
        app_module.ADMIN_EMAILS.add("p0-unverified-admin@test.org")
        try:
            r = self.client.post("/api/papers/delete", json={})
            self.assertEqual(r.status_code, 403)
        finally:
            app_module.ADMIN_EMAILS.discard("p0-unverified-admin@test.org")

    def test_real_admin_passes(self):
        from auth_test_helpers import login_session
        login_session(self.client, "shawnmckenzie11.sm@gmail.com")
        r = self.client.post("/api/papers/delete", json={})
        self.assertEqual(r.status_code, 400)

    def test_auth_status_not_admin_for_forged_email(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = "shawnmckenzie11.sm@gmail.com"
        r = self.client.get("/api/calibration/auth-status")
        self.assertFalse(r.get_json()["is_admin"])


if __name__ == "__main__":
    unittest.main()
