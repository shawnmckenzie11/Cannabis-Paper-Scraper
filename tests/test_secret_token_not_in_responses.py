"""Handlers that raise with a secret in the message never return it to the client."""
import os
import sys
import unittest
from io import BytesIO
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SECRET = "SECRET-TOKEN-xyz"


class TestSecretTokenNotInResponses(unittest.TestCase):
    def setUp(self):
        import app as m
        self.m = m
        m.app.config["TESTING"] = True
        m.limiter.reset()
        self.client = m.app.test_client()
        from auth_test_helpers import login_session
        login_session(self.client, next(iter(m.ADMIN_EMAILS)))

    def tearDown(self):
        self.m.limiter.reset()

    def _assert_no_secret(self, r):
        self.assertNotIn(SECRET, r.get_data(as_text=True))

    def test_upload_enqueue_failure(self):
        with patch.object(self.m.background_jobs, "create_task", side_effect=RuntimeError(SECRET)), \
                self.assertLogs(self.m.app.logger, level="ERROR") as logs:
            r = self.client.post("/api/papers/upload-pdf",
                                 data={"pdf": (BytesIO(b"%PDF-1.4 fake"), "fake.pdf")},
                                 content_type="multipart/form-data")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["error"], self.m.GENERIC_SERVER_ERROR)
        self._assert_no_secret(r)
        self.assertTrue(any(SECRET in line for line in logs.output))

    def test_upload_match_review_rejected(self):
        token = self.m._store_pending_pdf_upload(b"%PDF-1.4 fake", "fake.pdf", proposed_paper={"title": "Fake"})
        with patch.object(self.m.harvest, "build_pdf_upload_review", side_effect=ValueError(SECRET)), \
                self.assertLogs(self.m.app.logger, level="ERROR"):
            r = self.client.post("/api/papers/upload-pdf", data={"merge_token": token, "match_choice": "new"})
        self.assertEqual(r.status_code, 400)
        self._assert_no_secret(r)

    def test_upload_merge_save_failure(self):
        token = self.m._store_pending_pdf_upload(b"%PDF-1.4 fake", "fake.pdf", proposed_paper={"title": "Fake"})
        with patch.object(self.m.harvest, "ingest_uploaded_pdf", side_effect=RuntimeError(SECRET)), \
                self.assertLogs(self.m.app.logger, level="ERROR"):
            r = self.client.post("/api/papers/upload-pdf", data={"merge_token": token})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.get_json()["error"], self.m.GENERIC_SERVER_ERROR)
        self._assert_no_secret(r)

    def test_async_upload_failure_not_in_task_status(self):
        rows = {}

        def fake_update(task_id, **kw):
            rows.setdefault(task_id, {"task_id": task_id}).update(kw)

        token = self.m._store_pending_pdf_upload(b"%PDF-1.4 fake", "fake.pdf")
        with patch.object(self.m.harvest, "ingest_uploaded_pdf", side_effect=RuntimeError(SECRET)), \
                patch.object(self.m.background_jobs, "update_task", side_effect=fake_update), \
                patch.object(self.m.background_jobs, "get_task", side_effect=lambda t: rows.get(t)), \
                self.assertLogs(level="ERROR"):
            self.m.async_pdf_upload_task("fake-task-0042", token)
            r = self.client.get("/api/tasks/fake-task-0042")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["error_message"], self.m.GENERIC_SERVER_ERROR)
        self._assert_no_secret(r)


if __name__ == "__main__":
    unittest.main()
