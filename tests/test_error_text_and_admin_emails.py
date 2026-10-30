"""No route returns raw exception text, and index() no longer ships the admin email list (MCK fix PR)."""
import ast
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SECRET = "SECRET-DETAIL /tmp/fake_db_path password=fake-not-real"
FAKE_ADMIN = "fake-admin@example.invalid"


def _exception_text_uses(tree):
    """Yield line numbers of str(<exception var>) / f-string uses of an except-bound name."""
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        for node in ast.walk(handler):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "str" and node.args
                    and isinstance(node.args[0], ast.Name) and node.args[0].id == handler.name):
                yield node.lineno
            if isinstance(node, ast.FormattedValue) and isinstance(node.value, ast.Name) \
                    and node.value.id == handler.name:
                # f-strings are allowed only inside logger calls; flag the rest below
                yield ("fstr", node.lineno)


class TestNoExceptionTextInResponses(unittest.TestCase):
    def test_app_never_stringifies_caught_exceptions(self):
        src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        lines = src.splitlines()
        bad = []
        for hit in _exception_text_uses(tree):
            if isinstance(hit, tuple):
                line = lines[hit[1] - 1]
                if "logger." in line or line.strip().startswith("print("):
                    continue
                bad.append(hit[1])
            else:
                bad.append(hit)
        self.assertEqual(bad, [], f"raw exception text used at app.py lines {bad}")

    def test_background_task_failure_stores_generic_message(self):
        import app as m
        with patch.object(m.background_jobs, "update_task") as upd:
            with self.assertLogs(m.background_jobs.logger, level="ERROR") as logs:
                m.background_jobs.mark_task_failed("fake-task-1", RuntimeError(SECRET))
        self.assertEqual(upd.call_args.kwargs["error_message"], m.GENERIC_SERVER_ERROR)
        self.assertNotIn("SECRET-DETAIL", repr(upd.call_args))
        self.assertTrue(any("SECRET-DETAIL" in line for line in logs.output))

    def test_background_task_failure_stores_given_user_message(self):
        import app as m
        with patch.object(m.background_jobs, "update_task") as upd, \
                self.assertLogs(m.background_jobs.logger, level="ERROR"):
            m.background_jobs.mark_task_failed("fake-task-2", RuntimeError(SECRET), user_message="Safe text.")
        self.assertEqual(upd.call_args.kwargs["error_message"], "Safe text.")


class TestRoutes(unittest.TestCase):
    def setUp(self):
        import app as m
        self.m = m
        m.app.config["TESTING"] = True
        m.limiter.reset()
        self.client = m.app.test_client()

    def tearDown(self):
        self.m.limiter.reset()

    def test_search_simple_hides_exception_text(self):
        with self.client.session_transaction() as s:
            s["logged_in"] = True
        db = MagicMock()
        db.get_connection.return_value.execute.side_effect = RuntimeError(SECRET)
        with patch.object(self.m, "DatabaseManager", return_value=db), \
                self.assertLogs(self.m.app.logger, level="ERROR") as logs:
            r = self.client.get("/api/papers/search-simple?q=fake")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("SECRET-DETAIL", r.get_data(as_text=True))
        self.assertEqual(r.get_json()["error"], self.m.GENERIC_SERVER_ERROR)
        self.assertTrue(any("SECRET-DETAIL" in line for line in logs.output))

    def test_index_does_not_send_admin_emails(self):
        with patch.object(self.m, "ADMIN_EMAILS", {FAKE_ADMIN}), \
                patch.object(self.m, "render_template", return_value="ok") as rt:
            with self.client.session_transaction() as s:
                s["logged_in"] = True
                s["email"] = "fake-user@example.invalid"
            self.client.get("/")
        rt.assert_called_once()
        kwargs = rt.call_args.kwargs
        self.assertNotIn("admin_emails_list", kwargs)
        self.assertFalse(kwargs["is_admin"])
        self.assertNotIn(FAKE_ADMIN, repr(kwargs))

    def test_index_template_has_no_admin_list(self):
        html = open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()
        self.assertNotIn("admin_emails_list", html)


if __name__ == "__main__":
    unittest.main()
