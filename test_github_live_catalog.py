"""Tests for GitHub Actions daily harvest, scheduler gate, and SQLite catalog reload."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
WORKFLOW = ROOT / ".github" / "workflows" / "daily-harvest.yml"
PULL = ROOT / "scripts" / "pull_catalog_from_r2.sh"
CI_SCRIPT = ROOT / "scripts" / "ci_daily_harvest.py"
APP_PY = ROOT / "app.py"
DOCS = ROOT / "docs" / "github-live-catalog.md"
MACOS_DOCS = ROOT / "docs" / "macos-public-site.md"


def _load_ci_module():
    """Import scripts/ci_daily_harvest.py as a module."""
    spec = importlib.util.spec_from_file_location("ci_daily_harvest", CI_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tiny_catalog(path: Path, paper_id: int = 1) -> None:
    """Write a minimal papers table SQLite file."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO papers (id, title) VALUES (?, ?)", (paper_id, f"paper-{paper_id}"))
    conn.commit()
    conn.close()


class DailyHarvestConfigTests(unittest.TestCase):
    """Incremental harvest dates and the in-process thread flag."""

    def test_mindate_uses_last_run(self):
        from daily_harvest_config import resolve_harvest_mindate

        self.assertEqual(resolve_harvest_mindate("2026-08-24"), "2026-08-24")
        self.assertEqual(
            resolve_harvest_mindate("Never", today=date(2026, 8, 26)),
            "2026-08-23",
        )

    def test_inprocess_flag_defaults_off(self):
        from daily_harvest_config import inprocess_daily_harvest_enabled

        self.assertFalse(inprocess_daily_harvest_enabled({}))
        self.assertTrue(inprocess_daily_harvest_enabled({"INPROCESS_DAILY_HARVEST": "1"}))
        self.assertFalse(inprocess_daily_harvest_enabled({"INPROCESS_DAILY_HARVEST": "0"}))


class WorkflowAndScriptContractTests(unittest.TestCase):
    """CI must own harvest on a schedule and must not use Fly Postgres."""

    def test_workflow_schedule_and_sqlite(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("cron: \"0 14 * * *\"", text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("unset DATABASE_URL", text)
        self.assertIn("scripts/ci_daily_harvest.py", text)
        self.assertIn("python-version: \"3.12\"", text)
        self.assertIn("NCBI_API_KEY", text)
        self.assertIn("/api/catalog/reload", text)
        self.assertNotIn("flyctl", text)
        self.assertNotIn("DATABASE_URL:", text)

    def test_ci_script_forces_sqlite(self):
        text = CI_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("force_sqlite_env", text)
        self.assertIn("pop(\"DATABASE_URL\"", text)
        self.assertIn("run_harvest_pipeline", text)
        self.assertIn("sync_tab_flags_for_paper", text)

    def test_force_sqlite_env_pops_postgres_url(self):
        ci = _load_ci_module()
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = "postgres://example"
        try:
            ci.force_sqlite_env("/tmp/catalog.db")
            self.assertNotIn("DATABASE_URL", os.environ)
            self.assertEqual(os.environ["DATABASE_PATH"], "/tmp/catalog.db")
        finally:
            os.environ.pop("DATABASE_PATH", None)
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous

    def test_pull_script_uses_r2_and_reload(self):
        text = PULL.read_text(encoding="utf-8")
        self.assertIn("aws s3 cp", text)
        self.assertIn("R2_ENDPOINT", text)
        self.assertIn("/api/catalog/reload", text)
        self.assertIn("X-Catalog-Reload-Token", text)

    def test_app_gates_inprocess_scheduler(self):
        text = APP_PY.read_text(encoding="utf-8")
        self.assertIn("inprocess_daily_harvest_enabled()", text)
        self.assertIn("/api/catalog/reload", text)
        self.assertIn("X-Catalog-Reload-Token", text)

    def test_docs_cover_domain_and_abandon_mac(self):
        live = DOCS.read_text(encoding="utf-8")
        abandoned = MACOS_DOCS.read_text(encoding="utf-8")
        self.assertIn("Render", live)
        self.assertIn("ci_daily_harvest.py", live)
        self.assertNotIn("Fly.io is used", live)
        self.assertIn("abandoned", abandoned.lower())
        self.assertIn("github-live-catalog.md", abandoned)

    def test_render_and_start_script_avoid_fly(self):
        """Public host boots SQLite via start_web.sh; Fly Postgres is never required."""
        start = (ROOT / "scripts" / "start_web.sh").read_text(encoding="utf-8")
        render = (ROOT / "render.yaml").read_text(encoding="utf-8")
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("unset DATABASE_URL", start)
        self.assertIn("ensure_local_catalog", start)
        self.assertIn("gunicorn", start)
        self.assertIn("plan: free", render)
        self.assertIn("scripts/start_web.sh", render)
        self.assertIn("start_web.sh", docker)
        self.assertNotIn("ENTRYPOINT", docker)


class CatalogReloadTests(unittest.TestCase):
    """Live SQLite must swap atomically to a harvested copy."""

    def test_replace_sqlite_catalog(self):
        import catalog_reload

        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.db"
            staging = Path(tmp) / "staging.db"
            _tiny_catalog(live, paper_id=1)
            _tiny_catalog(staging, paper_id=2)
            Path(str(live) + "-wal").write_text("stale", encoding="utf-8")
            catalog_reload.replace_sqlite_catalog(live, staging)
            conn = sqlite3.connect(str(live))
            row = conn.execute("SELECT id FROM papers").fetchone()
            conn.close()
            self.assertEqual(row[0], 2)
            self.assertFalse(Path(str(live) + "-wal").exists())
            self.assertFalse(staging.exists())

    def test_catalog_needs_seed_detects_missing_and_tiny_files(self):
        import catalog_reload

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no.db"
            self.assertTrue(catalog_reload.catalog_needs_seed(missing, min_bytes=10))
            tiny = Path(tmp) / "tiny.db"
            tiny.write_bytes(b"x")
            self.assertTrue(catalog_reload.catalog_needs_seed(tiny, min_bytes=10))
            ok = Path(tmp) / "ok.db"
            _tiny_catalog(ok)
            self.assertFalse(catalog_reload.catalog_needs_seed(ok, min_bytes=1))

    def test_ensure_local_catalog_downloads_when_missing(self):
        import catalog_reload
        import shutil

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "live.db"
            remote = Path(tmp) / "remote.db"
            _tiny_catalog(remote, paper_id=7)

            def fake_download(dest_path):
                shutil.copy2(remote, dest_path)

            previous_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with patch.dict(os.environ, {"MIN_CATALOG_BYTES": "1"}, clear=False):
                    with patch("catalog_reload.download_from_r2_env", side_effect=fake_download):
                        path = catalog_reload.ensure_local_catalog(dest)
            finally:
                os.chdir(previous_cwd)
            conn = sqlite3.connect(path)
            row = conn.execute("SELECT id FROM papers").fetchone()
            conn.close()
            self.assertEqual(row[0], 7)
            self.assertFalse(Path(str(dest) + ".download").exists())

    def test_reload_endpoint_requires_token_and_swaps(self):
        os.environ["CATALOG_RELOAD_TOKEN"] = "test-reload-token"
        os.environ.pop("DATABASE_URL", None)
        from app import app

        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.db"
            staging = Path(tmp) / "staging.db"
            _tiny_catalog(live, paper_id=1)
            _tiny_catalog(staging, paper_id=9)
            os.environ["DATABASE_PATH"] = str(live)
            client = app.test_client()
            denied = client.post("/api/catalog/reload", json={"staging_path": str(staging)})
            self.assertEqual(denied.status_code, 401)
            ok = client.post(
                "/api/catalog/reload",
                json={"staging_path": str(staging)},
                headers={"X-Catalog-Reload-Token": "test-reload-token"},
            )
            self.assertEqual(ok.status_code, 200, ok.data)
            payload = json.loads(ok.data)
            self.assertTrue(payload["ok"])
            conn = sqlite3.connect(str(live))
            row = conn.execute("SELECT id FROM papers").fetchone()
            conn.close()
            self.assertEqual(row[0], 9)

    def test_run_daily_harvest_records_metadata(self):
        ci = _load_ci_module()
        from db_manager import DatabaseManager

        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = str(Path(tmp) / "catalog.db")
            os.environ.pop("DATABASE_URL", None)
            os.environ["DATABASE_PATH"] = sqlite_path
            DatabaseManager._initialized = False
            db = DatabaseManager(db_path=sqlite_path)
            db.init_db()
            db.set_metadata("last_daily_harvest_date", "2026-08-20")

            with patch("harvest.run_harvest_pipeline", return_value=(3, 1, 0, [11, 12])):
                with patch.object(DatabaseManager, "sync_tab_flags_for_paper") as sync_flags:
                    with patch.object(DatabaseManager, "sync_orphan_tab_flags_since", return_value=2):
                        result = ci.run_daily_harvest(sqlite_path, run_purge=False)
            self.assertEqual(result["success_count"], 3)
            self.assertEqual(result["mindate"], "2026-08-20")
            self.assertEqual(sync_flags.call_count, 2)
            db2 = DatabaseManager(db_path=sqlite_path)
            self.assertEqual(db2.get_metadata("last_daily_harvest_date"), date.today().isoformat())
            self.assertIn("Ingested 3 papers", db2.get_metadata("last_daily_harvest_status"))


if __name__ == "__main__":
    unittest.main()
