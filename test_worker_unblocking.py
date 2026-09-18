"""Slice A: heavy harvest / PDF / analyze work must leave the request thread."""

import time
import unittest
from io import BytesIO
from unittest.mock import patch

from app import ADMIN_EMAILS, app, harvest_lock, harvest_state
from db_manager import DatabaseManager
import background_jobs


def _admin_email():
    """Return a configured admin email for Flask test sessions."""
    return next(iter(ADMIN_EMAILS))


def ensure_background_tasks_table():
    """Create the Alembic ``background_tasks`` table in the test SQLite if missing."""
    db = DatabaseManager()
    conn = db.get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS background_tasks (
                task_id TEXT PRIMARY KEY,
                sa_task_type TEXT,
                status TEXT,
                total_papers INTEGER,
                processed_papers INTEGER,
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _wait_until(predicate, timeout_s=4.0, interval=0.05):
    """Poll ``predicate`` until it is true or ``timeout_s`` elapses."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition not met before timeout; last={last!r}")


class TestHarvestDoesNotBlockOnNcbi(unittest.TestCase):
    """POST /api/harvest must return before PubMed count finishes."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        with harvest_lock:
            harvest_state.update({
                "status": "idle",
                "progress": "",
                "error": None,
                "start_time": None,
                "total_count": None,
                "query": None,
            })

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = _admin_email()

    def test_harvest_returns_202_before_pubmed_count(self):
        """NCBI esearch used to run on the request; it must not delay 202."""
        started = time.time()
        count_entered = {"at": None}

        def slow_count(_query):
            count_entered["at"] = time.time()
            time.sleep(0.35)
            return 42

        self._login_admin()
        with patch("harvest.get_pubmed_count", side_effect=slow_count) as mock_count:
            with patch("harvest.run_harvest_pipeline", return_value=(0, 0, 0, [])) as mock_run:
                response = self.client.post(
                    "/api/harvest",
                    json={"query": "cannabis[ti]"},
                )
                elapsed = time.time() - started
                self.assertEqual(response.status_code, 202, response.data)
                body = response.get_json()
                self.assertEqual(body["status"], "accepted")
                self.assertLess(elapsed, 0.25, f"request blocked for {elapsed:.3f}s")
                _wait_until(lambda: mock_count.called)
                _wait_until(lambda: mock_run.called)
                self.assertGreaterEqual(count_entered["at"] - started, 0.0)

    def test_harvest_prompt_arrives_via_status_not_request(self):
        """Counts over 500 pause the worker at status=prompt without ingesting."""
        self._login_admin()
        with patch("harvest.get_pubmed_count", return_value=750) as mock_count:
            with patch("harvest.run_harvest_pipeline") as mock_run:
                response = self.client.post(
                    "/api/harvest",
                    json={"query": "cannabis OR marijuana"},
                )
                self.assertEqual(response.status_code, 202)
                self.assertNotEqual(response.get_json().get("status"), "prompt")
                _wait_until(lambda: harvest_state["status"] == "prompt")
                self.assertEqual(harvest_state["total_count"], 750)
                mock_count.assert_called()
                mock_run.assert_not_called()

        with patch("harvest.get_pubmed_count") as mock_count:
            with patch("harvest.run_harvest_pipeline", return_value=(1, 0, 0, [])) as mock_run:
                forced = self.client.post(
                    "/api/harvest",
                    json={"query": "cannabis OR marijuana", "force": True, "max_results": 25},
                )
                self.assertEqual(forced.status_code, 202)
                _wait_until(lambda: mock_run.called)
                mock_count.assert_not_called()
                args, kwargs = mock_run.call_args
                self.assertEqual(kwargs.get("max_results") or args[1], 25)


class TestPdfUploadQueuesParse(unittest.TestCase):
    """Initial PDF upload is a 202 job; match/merge stay on the request."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        ensure_background_tasks_table()

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = _admin_email()

    def test_initial_pdf_upload_returns_202_and_polls_result(self):
        """Parse/classify is enqueued; GET /api/tasks returns the match payload."""
        self._login_admin()
        fake_result = {
            "status": "match_selection_required",
            "title": "Queued PDF Paper",
            "candidates": [{"id": 9, "title": "Queued PDF Paper", "similarity": 0.91}],
            "proposed_paper": {"title": "Queued PDF Paper"},
            "existing_row": {},
            "is_new_paper": True,
            "paper_id": None,
        }

        with patch("harvest.ingest_uploaded_pdf", return_value=fake_result) as mock_ingest:
            response = self.client.post(
                "/api/papers/upload-pdf",
                data={"pdf": (BytesIO(b"%PDF-1.4 fake"), "paper.pdf")},
                content_type="multipart/form-data",
            )
            self.assertEqual(response.status_code, 202, response.data)
            body = response.get_json()
            task_id = body["task_id"]
            self.assertTrue(task_id)

            def _completed():
                poll = self.client.get(f"/api/tasks/{task_id}")
                if poll.status_code != 200:
                    return None
                payload = poll.get_json()
                return payload if payload.get("status") == "completed" else None

            payload = _wait_until(_completed)
            mock_ingest.assert_called_once()
            result = payload["result"]
            self.assertEqual(result["status"], "match_selection_required")
            self.assertTrue(result.get("merge_token"))
            self.assertNotIn("proposed_paper", result)

    def test_empty_pdf_rejected_on_request(self):
        """Cheap validation still happens before the worker is involved."""
        self._login_admin()
        response = self.client.post(
            "/api/papers/upload-pdf",
            data={"pdf": (BytesIO(b""), "empty.pdf")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)


class TestAnalyzeIsQueuedAndCapped(unittest.TestCase):
    """Analyze must not load 100k rows on the request or return the full list."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        ensure_background_tasks_table()

    def test_analyze_returns_202_then_capped_result(self):
        """Worker loads at most ANALYZE_PAPER_CAP+1 rows and omits a huge papers dump."""
        over_cap = background_jobs.ANALYZE_PAPER_CAP + 25
        papers = [
            {
                "id": i,
                "title": f"Paper {i}",
                "year": 2020,
                "study_type": ["Clinical (RCT)"],
                "exposure_method": ["inhaled"],
                "cannabis_type": ["flower"],
                "outcome_domain": ["pain"],
                "thc_pct": 10,
                "cbd_pct": 1,
                "sample_size": 20,
            }
            for i in range(over_cap)
        ]

        with patch("db_manager.DatabaseManager.search_papers_for_analysis", return_value=papers):
            with patch("db_manager.DatabaseManager.init_analyses_table"):
                response = self.client.post("/api/analyze", json={"filters": {"tab": "clinical"}})
                self.assertEqual(response.status_code, 202, response.data)
                task_id = response.get_json()["task_id"]

                def _done():
                    poll = self.client.get(f"/api/analyze/status/{task_id}")
                    payload = poll.get_json()
                    return payload if payload and payload.get("status") == "completed" else None

                payload = _wait_until(_done, timeout_s=8)
        result = payload["result"]
        self.assertTrue(result["truncated"])
        self.assertEqual(result["analyzed_count"], background_jobs.ANALYZE_PAPER_CAP)
        self.assertEqual(result["paper_count"], background_jobs.ANALYZE_PAPER_CAP)
        self.assertLessEqual(len(result.get("papers") or []), background_jobs.ANALYZE_DRILLDOWN_CAP)
        self.assertIn("chart_data", result)
        self.assertEqual(len(result["chart_data"]["paper_ids"]), background_jobs.ANALYZE_PAPER_CAP)


class TestSectionStatsIsSampled(unittest.TestCase):
    """Section-stats must not load every matching abstract."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_section_stats_passes_sample_limit(self):
        """The route asks the DB for sample_limit+1 rows so it can set sampled."""
        captured = {}

        def fake_search(self, filters, limit=None):
            captured["limit"] = limit
            return [
                {"id": 1, "title": "A", "abstract": "Methods: x.\nResults: y."},
            ]

        with patch(
            "db_manager.DatabaseManager.search_papers_minimal_for_section_stats",
            fake_search,
        ):
            response = self.client.get("/api/search/section-stats")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["limit"], background_jobs.SECTION_STATS_SAMPLE_LIMIT + 1)
        body = response.get_json()
        self.assertIn("sampled", body)
        self.assertEqual(body["sample_limit"], background_jobs.SECTION_STATS_SAMPLE_LIMIT)
        self.assertFalse(body["sampled"])


if __name__ == "__main__":
    unittest.main()
