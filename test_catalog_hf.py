"""Tests for catalog reload, Hub/R2 store selection, and HF harvest wiring."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def _tiny_catalog(path: Path, paper_id: int = 1) -> None:
    """Write a minimal papers table SQLite file."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO papers (id, title) VALUES (?, ?)", (paper_id, f"paper-{paper_id}"))
    conn.commit()
    conn.close()


def _load_ci_module():
    """Import scripts/ci_daily_harvest.py as a module."""
    spec = importlib.util.spec_from_file_location("ci_daily_harvest", ROOT / "scripts" / "ci_daily_harvest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CatalogReloadTests(unittest.TestCase):
    """Live SQLite must swap atomically to a harvested copy."""

    def tearDown(self):
        os.environ.pop("CATALOG_RELOAD_TOKEN", None)
        os.environ.pop("DATABASE_PATH", None)

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


class CatalogStoreTests(unittest.TestCase):
    """Backend selection: R2 when fully configured, otherwise Hub dataset."""

    def tearDown(self):
        for key in (
            "CATALOG_STORE",
            "R2_BUCKET",
            "R2_ENDPOINT",
            "CATALOG_DATASET_ID",
            "HF_TOKEN",
        ):
            os.environ.pop(key, None)

    def test_store_backend_defaults_to_hf(self):
        import catalog_store

        os.environ.pop("R2_BUCKET", None)
        os.environ.pop("R2_ENDPOINT", None)
        os.environ.pop("CATALOG_STORE", None)
        self.assertEqual(catalog_store.store_backend({}), "hf")

    def test_store_backend_prefers_r2_when_configured(self):
        import catalog_store

        env = {"R2_BUCKET": "papers", "R2_ENDPOINT": "https://example.r2.cloudflarestorage.com"}
        self.assertEqual(catalog_store.store_backend(env), "r2")

    def test_upload_catalog_writes_hub_even_when_r2_is_configured(self):
        import catalog_store

        os.environ["R2_BUCKET"] = "papers"
        os.environ["R2_ENDPOINT"] = "https://example.r2.cloudflarestorage.com"
        os.environ["CATALOG_DATASET_ID"] = "mckenziansolutions/cannabis-papers-catalog"
        with patch.object(catalog_store, "_upload_r2") as r2_upload:
            with patch.object(catalog_store, "_upload_hf") as hf_upload:
                catalog_store.upload_catalog("/tmp/catalog.db")
        r2_upload.assert_called_once_with("/tmp/catalog.db")
        hf_upload.assert_called_once_with("/tmp/catalog.db")

    def test_inprocess_harvest_defaults_off(self):
        import hf_space_config

        self.assertFalse(hf_space_config.inprocess_daily_harvest_enabled({}))
        self.assertTrue(hf_space_config.inprocess_daily_harvest_enabled({"INPROCESS_DAILY_HARVEST": "1"}))
        self.assertTrue(hf_space_config.running_on_huggingface({"SPACE_ID": "mckenziansolutions/cannabis-paper-scraper"}))

    def test_ci_script_forces_sqlite(self):
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

    def test_deploy_workflow_exists(self):
        text = (ROOT / ".github" / "workflows" / "deploy-hf-space.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/deploy_hf_space.py", text)
        self.assertIn("HF_TOKEN", text)

    def test_readme_declares_docker_space(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("sdk: docker", text)
        self.assertIn("app_port: 7860", text)
        self.assertIn("mckenziansolutions/cannabis-paper-scraper", text)

    def test_entrypoint_forces_space_port(self):
        text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("SPACE_ID", text)
        self.assertIn("export PORT=7860", text)
        self.assertIn("/tmp/cannabis_papers.db", text)

    def test_bootstrap_normalizes_live_search_row(self):
        spec = importlib.util.spec_from_file_location(
            "bootstrap_catalog_from_live",
            ROOT / "scripts" / "bootstrap_catalog_from_live.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        row = module.normalize_live_paper(
            {
                "id": 99,
                "newly_harvested": True,
                "title": "Example THC paper",
                "thc_um": 1.5,
                "cbd_um": 0.2,
                "authors": ["A", "B"],
                "open_access": True,
            }
        )
        self.assertNotIn("id", row)
        self.assertNotIn("newly_harvested", row)
        self.assertEqual(row["thc_uM"], 1.5)
        self.assertEqual(row["cbd_uM"], 0.2)
        self.assertEqual(row["open_access"], 1)
        self.assertEqual(json.loads(row["authors"]), ["A", "B"])

    def test_deploy_script_detects_pro_gate(self):
        spec = importlib.util.spec_from_file_location(
            "deploy_hf_space",
            ROOT / "scripts" / "deploy_hf_space.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(
            module._pro_required(
                RuntimeError(
                    "Static Spaces are free for everyone, but hosting Gradio and Docker "
                    "Spaces on free cpu-basic requires a PRO subscription."
                )
            )
        )
        self.assertFalse(module._pro_required(RuntimeError("401 unauthorized")))

    def test_reload_pull_from_store(self):
        os.environ["CATALOG_RELOAD_TOKEN"] = "test-reload-token"
        os.environ.pop("DATABASE_URL", None)
        from app import app

        with tempfile.TemporaryDirectory() as tmp:
            live = Path(tmp) / "live.db"
            staging = Path(tmp) / "live.db.new"
            _tiny_catalog(live, paper_id=1)
            _tiny_catalog(staging, paper_id=4)
            os.environ["DATABASE_PATH"] = str(live)

            def fake_download(dest: str) -> str:
                Path(dest).write_bytes(staging.read_bytes())
                return dest

            with patch("catalog_store.download_catalog", side_effect=fake_download):
                client = app.test_client()
                ok = client.post(
                    "/api/catalog/reload",
                    json={"pull_from_store": True},
                    headers={"X-Catalog-Reload-Token": "test-reload-token"},
                )
            self.assertEqual(ok.status_code, 200, ok.data)
            conn = sqlite3.connect(str(live))
            row = conn.execute("SELECT id FROM papers").fetchone()
            conn.close()
            self.assertEqual(row[0], 4)


if __name__ == "__main__":
    unittest.main()
