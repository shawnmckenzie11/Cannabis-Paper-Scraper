"""Server errors on /api/search, /api/tab-counts and PDF upload must not echo exception text."""
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECRET = "SECRET-DETAIL /tmp/fake_db_path password=fake-not-real"


class TestErrorMessageLeaks(unittest.TestCase):
    def setUp(self):
        import app as m
        self.m = m
        m.app.config["TESTING"] = True
        m.limiter.reset()
        self.client = m.app.test_client()
        with self.client.session_transaction() as s:
            s["logged_in"] = True

    def tearDown(self):
        self.m.limiter.reset()

    def _assert_generic(self, r, status=500):
        self.assertEqual(r.status_code, status)
        body = r.get_data(as_text=True)
        self.assertNotIn("SECRET-DETAIL", body)
        self.assertNotIn("password", body)
        self.assertEqual(r.get_json()["error"], self.m.GENERIC_SERVER_ERROR)

    def test_search_hides_exception_text(self):
        db = MagicMock()
        db.search_papers.side_effect = RuntimeError(SECRET)
        with patch.object(self.m, "DatabaseManager", return_value=db), \
                self.assertLogs(self.m.app.logger, level="ERROR") as logs:
            r = self.client.get("/api/search")
        self._assert_generic(r)
        self.assertTrue(any("SECRET-DETAIL" in line for line in logs.output))

    def test_tab_counts_hides_exception_text(self):
        db = MagicMock()
        db.get_tab_counts.side_effect = RuntimeError(SECRET)
        with patch.object(self.m, "DatabaseManager", return_value=db), \
                self.assertLogs(self.m.app.logger, level="ERROR") as logs:
            r = self.client.get("/api/tab-counts")
        self._assert_generic(r)
        self.assertTrue(any("SECRET-DETAIL" in line for line in logs.output))

    def test_section_stats_hides_exception_text(self):
        db = MagicMock()
        db.search_papers_minimal_for_section_stats.side_effect = RuntimeError(SECRET)
        with patch.object(self.m, "DatabaseManager", return_value=db), \
                self.assertLogs(self.m.app.logger, level="ERROR") as logs:
            r = self.client.get("/api/search/section-stats")
        self._assert_generic(r)
        self.assertTrue(any("SECRET-DETAIL" in line for line in logs.output))

    def _citation_graph_raising(self, method):
        cg = MagicMock()
        getattr(cg, method).side_effect = RuntimeError(SECRET)
        return patch.object(self.m, "CitationGraph", return_value=cg)

    def test_references_hides_exception_text(self):
        with patch.object(self.m, "DatabaseManager", return_value=MagicMock()), \
                self._citation_graph_raising("get_references"), \
                self.assertLogs(self.m.app.logger, level="ERROR") as logs:
            r = self.client.get("/api/papers/90001/references")
        self._assert_generic(r)
        self.assertTrue(any("SECRET-DETAIL" in line for line in logs.output))

    def test_cited_by_hides_exception_text(self):
        with patch.object(self.m, "DatabaseManager", return_value=MagicMock()), \
                self._citation_graph_raising("get_cited_by"), \
                self.assertLogs(self.m.app.logger, level="ERROR") as logs:
            r = self.client.get("/api/papers/90001/cited-by")
        self._assert_generic(r)
        self.assertTrue(any("SECRET-DETAIL" in line for line in logs.output))

    def _login_admin(self):
        from auth_test_helpers import login_session
        login_session(self.client, next(iter(self.m.ADMIN_EMAILS)))

    def test_upload_enqueue_failure_hides_exception_text(self):
        self._login_admin()
        with patch.object(self.m.background_jobs, "create_task", side_effect=RuntimeError(SECRET)), \
                self.assertLogs(self.m.app.logger, level="ERROR"):
            r = self.client.post(
                "/api/papers/upload-pdf",
                data={"pdf": (BytesIO(b"%PDF-1.4 fake"), "paper.pdf")},
                content_type="multipart/form-data",
            )
        self._assert_generic(r)

    def test_async_upload_task_stores_generic_error(self):
        token = self.m._store_pending_pdf_upload(b"%PDF-1.4 fake", "paper.pdf")
        with patch.object(self.m.harvest, "ingest_uploaded_pdf", side_effect=RuntimeError(SECRET)), \
                patch.object(self.m.background_jobs, "update_task"), \
                patch.object(self.m.background_jobs, "mark_task_failed") as failed, \
                self.assertLogs(self.m.app.logger, level="ERROR"):
            self.m.async_pdf_upload_task("fake-task-0001", token)
        self.assertEqual(failed.call_args.kwargs["user_message"], self.m.GENERIC_SERVER_ERROR)
        self.assertNotIn("SECRET-DETAIL", failed.call_args.kwargs["user_message"])


if __name__ == "__main__":
    unittest.main()
