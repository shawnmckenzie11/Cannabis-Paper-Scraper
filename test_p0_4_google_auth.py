"""P0-4: /auth/google/callback must verify ID tokens, require state, and gate fake login."""
import base64
import json
import os
import unittest
from unittest import mock

import app as app_module
from app import app

CLIENT_ID = "test-client.apps.googleusercontent.com"
GOOD_PAYLOAD = {
    "aud": CLIENT_ID,
    "iss": "https://accounts.google.com",
    "email": "admin@example.com",
    "email_verified": True,
    "sub": "google-sub-123",
    "name": "Admin",
}


def _forged_jwt(payload):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc(payload)}.sig"


class FakeDB:
    def __init__(self):
        self.users = {1: {"id": 1, "username": "admin", "email": "admin@example.com", "google_id": None}}
        self.linked = []

    def get_user_by_google_id(self, gid):
        return next((u for u in self.users.values() if u.get("google_id") == gid), None)

    def get_user_by_username_or_email(self, v):
        return next((u for u in self.users.values() if v in (u["email"], u["username"])), None)

    def get_user_by_email(self, v):
        return next((u for u in self.users.values() if (u["email"] or "").lower() == (v or "").lower()), None)

    def get_connection(self):
        db = self

        class Conn:
            def execute(self, sql, params):
                gid, uid = params
                db.users[uid]["google_id"] = gid
                db.linked.append((uid, gid))

            def commit(self):
                pass

            def close(self):
                pass

        return Conn()

    def create_user(self, username, email, google_id=None, is_verified=0, **kw):
        uid = max(self.users) + 1
        self.users[uid] = {"id": uid, "username": username, "email": email, "google_id": google_id}
        self.linked.append((uid, google_id))
        return True

    def record_user_login(self, uid):
        pass


class GoogleCallbackTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        self.db = FakeDB()
        p = mock.patch.object(app_module, "DatabaseManager", lambda *a, **k: self.db)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"GOOGLE_CLIENT_ID": CLIENT_ID, "GOOGLE_CLIENT_SECRET": "s"})
        env.start()
        self.addCleanup(env.stop)
        for k in ("GOOGLE_FAKE_LOGIN", "APP_ENV", "FLASK_ENV"):
            os.environ.pop(k, None)

    def _logged_in(self):
        with self.client.session_transaction() as s:
            return bool(s.get("logged_in"))

    # (a) One Tap credential
    def test_forged_unsigned_token_rejected(self):
        r = self.client.post("/auth/google/callback", data={"credential": _forged_jwt(GOOD_PAYLOAD)})
        self.assertEqual(r.status_code, 401)
        self.assertFalse(self._logged_in())
        self.assertEqual(self.db.linked, [])

    def _post_with_verify(self, payload=None, side_effect=None):
        with mock.patch("google.oauth2.id_token.verify_oauth2_token", return_value=payload, side_effect=side_effect) as v:
            r = self.client.post("/auth/google/callback", data={"credential": "tok"})
        return r, v

    def test_valid_token_passes(self):
        r, v = self._post_with_verify(dict(GOOD_PAYLOAD))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self._logged_in())
        self.assertEqual(v.call_args.args[0], "tok")
        self.assertEqual(v.call_args.args[2], CLIENT_ID)
        self.assertEqual(self.db.linked, [(1, "google-sub-123")])

    def test_bad_signature_rejected(self):
        r, _ = self._post_with_verify(side_effect=ValueError("bad sig"))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.db.linked, [])

    def test_wrong_aud_iss_or_unverified_email_rejected(self):
        for bad in ({"aud": "other"}, {"iss": "evil.com"}, {"email_verified": False}):
            r, _ = self._post_with_verify({**GOOD_PAYLOAD, **bad})
            self.assertEqual(r.status_code, 401, bad)
            self.assertFalse(self._logged_in())
        self.assertEqual(self.db.linked, [])

    def test_bare_issuer_accepted(self):
        r, _ = self._post_with_verify({**GOOD_PAYLOAD, "iss": "accounts.google.com"})
        self.assertEqual(r.status_code, 302)

    # (b) fake ?email= login
    def test_email_param_rejected_without_flag(self):
        r = self.client.get("/auth/google/callback?email=admin@example.com")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(self._logged_in())

    def test_email_param_rejected_when_secrets_missing(self):
        os.environ.pop("GOOGLE_CLIENT_ID")
        os.environ.pop("GOOGLE_CLIENT_SECRET")
        r = self.client.get("/auth/google/callback?email=admin@example.com")
        self.assertEqual(r.status_code, 401)
        self.assertFalse(self._logged_in())
        self.assertEqual(self.db.linked, [])

    def test_flag_without_dev_env_rejected(self):
        for env in ({}, {"APP_ENV": "production"}, {"FLASK_ENV": "prod"}):
            with mock.patch.dict(os.environ, {"GOOGLE_FAKE_LOGIN": "1", **env}):
                r = self.client.get("/auth/google/callback?email=admin@example.com")
            self.assertEqual(r.status_code, 401, env)
            self.assertFalse(self._logged_in())

    def test_dev_env_without_flag_rejected(self):
        with mock.patch.dict(os.environ, {"APP_ENV": "dev"}):
            r = self.client.get("/auth/google/callback?email=admin@example.com")
        self.assertEqual(r.status_code, 401)

    def test_flag_works_in_dev(self):
        with mock.patch.dict(os.environ, {"GOOGLE_FAKE_LOGIN": "1", "APP_ENV": "dev"}):
            r = self.client.get("/auth/google/callback?email=dev@example.com")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self._logged_in())

    # (c) state
    def test_auth_google_state_is_random_urlsafe_string(self):
        import urllib.parse
        r = self.client.get("/auth/google")
        self.assertEqual(r.status_code, 302)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["Location"]).query)
        with self.client.session_transaction() as s:
            stored = s["google_oauth_state"]
        self.assertIsInstance(stored, str)
        self.assertGreaterEqual(len(stored), 32)
        self.assertEqual(q["state"], [stored])
        self.assertNotEqual(self.client.get("/auth/google").headers["Location"], r.headers["Location"])

    def test_missing_state_rejected(self):
        with mock.patch.object(app_module.requests, "post") as post:
            r = self.client.get("/auth/google/callback?code=abc&state=123456")
        self.assertEqual(r.status_code, 401)
        post.assert_not_called()
        self.assertFalse(self._logged_in())

    def test_mismatched_state_rejected(self):
        with self.client.session_transaction() as s:
            s["google_oauth_state"] = "111111"
        with mock.patch.object(app_module.requests, "post") as post:
            r = self.client.get("/auth/google/callback?code=abc&state=222222")
        self.assertEqual(r.status_code, 401)
        post.assert_not_called()

    def test_matching_state_proceeds_to_token_exchange(self):
        with self.client.session_transaction() as s:
            s["google_oauth_state"] = "111111"
        tok = mock.Mock(**{"json.return_value": {"access_token": "at"}})
        info = mock.Mock(**{"json.return_value": {"email": "admin@example.com", "email_verified": True, "sub": "g1", "name": "A"}})
        with mock.patch.object(app_module.requests, "post", return_value=tok), \
             mock.patch.object(app_module.requests, "get", return_value=info):
            r = self.client.get("/auth/google/callback?code=abc&state=111111")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self._logged_in())

    # (d) redirect flow: email_verified required
    def _redirect_flow(self, token_json, userinfo_json=None, verify=None, verify_err=None):
        with self.client.session_transaction() as s:
            s["google_oauth_state"] = "111111"
        tok = mock.Mock(**{"json.return_value": token_json})
        info = mock.Mock(**{"json.return_value": userinfo_json or {}})
        with mock.patch.object(app_module.requests, "post", return_value=tok), \
             mock.patch.object(app_module.requests, "get", return_value=info) as get, \
             mock.patch("google.oauth2.id_token.verify_oauth2_token", return_value=verify, side_effect=verify_err):
            r = self.client.get("/auth/google/callback?code=abc&state=111111")
        return r, get

    def test_redirect_userinfo_unverified_email_rejected(self):
        for ev in (False, None, "true"):
            info = {"email": "admin@example.com", "sub": "attacker", "name": "X"}
            if ev is not None:
                info["email_verified"] = ev
            r, _ = self._redirect_flow({"access_token": "at"}, info)
            self.assertEqual(r.status_code, 401, ev)
            self.assertFalse(self._logged_in())
        self.assertEqual(self.db.linked, [])
        self.assertIsNone(self.db.users[1]["google_id"])

    def test_redirect_id_token_unverified_email_rejected(self):
        r, get = self._redirect_flow({"access_token": "at", "id_token": "idt"},
                                     {"email": "admin@example.com", "email_verified": True, "sub": "x"},
                                     verify={**GOOD_PAYLOAD, "email_verified": False})
        self.assertEqual(r.status_code, 401)
        self.assertFalse(self._logged_in())
        self.assertEqual(self.db.linked, [])
        get.assert_not_called()

    def test_redirect_id_token_bad_signature_rejected(self):
        r, _ = self._redirect_flow({"access_token": "at", "id_token": "idt"}, verify_err=ValueError("bad"))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.db.linked, [])

    def test_redirect_verified_id_token_works(self):
        r, get = self._redirect_flow({"access_token": "at", "id_token": "idt"},
                                     {"email": "other@example.com", "email_verified": True, "sub": "other"},
                                     verify=dict(GOOD_PAYLOAD))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self._logged_in())
        self.assertEqual(self.db.linked, [(1, "google-sub-123")])
        get.assert_not_called()

    def test_redirect_verified_userinfo_works(self):
        r, _ = self._redirect_flow({"access_token": "at"},
                                   {"email": "admin@example.com", "email_verified": True, "sub": "g9", "name": "A"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self._logged_in())
        self.assertEqual(self.db.linked, [(1, "g9")])

    # (e) link by email only
    def test_google_email_matching_username_does_not_link(self):
        self.db.users[2] = {"id": 2, "username": "victim@example.com", "email": "real@example.com", "google_id": None}
        r, _ = self._post_with_verify({**GOOD_PAYLOAD, "email": "victim@example.com", "sub": "atk"})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(self.db.users[2]["google_id"])
        self.assertNotIn(2, [uid for uid, _ in self.db.linked])


class SafeNextUrlTests(unittest.TestCase):
    def test_rejects_open_redirects(self):
        for bad in ("/%09/evil.com", "/\\evil.com", "//evil.com", "https://evil.com"):
            self.assertEqual(app_module._safe_next_url(bad), "/", bad)

    def test_keeps_local_path(self):
        self.assertEqual(app_module._safe_next_url("/settings?x=1"), "/settings?x=1")


if __name__ == "__main__":
    unittest.main()
