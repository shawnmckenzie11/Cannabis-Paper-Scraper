"""Tests for blast-radius reporting and similarity cohort validation."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import golden_dataset_paths
import patch_blast_radius
import similarity_cohort_validation
from local_sync import ensure_sync_schema, save_baseline_rows


class PatchBlastRadiusTests(unittest.TestCase):
    """Blast-radius payload normalization and report writing."""

    def test_normalize_blast_radius_payload(self) -> None:
        """Reingest and push summaries map to normalized schema."""
        ctx = patch_blast_radius.PatchFinishContext(
            loop_type="golden_b",
            patch_id="test_patch_001",
            scope_subnode="node2c",
            endpoint_id="node2c.cell_culture_other_in_vitro.cannabinoids_dissolved_in_media",
            reingest_summary={
                "papers_processed": 3645,
                "papers_changed": 890,
                "papers_written": 890,
                "field_change_counts": {"exposure_method": 815, "study_type": 130},
                "scope_subnode": "node2c",
                "full_subnode": True,
            },
            push_summary={"delta_count": 486, "stdout_tail": "486 deltas applied (Fly SSH)"},
        )
        payload = patch_blast_radius.normalize_blast_radius_payload(ctx)
        self.assertEqual(payload["papers_scanned"], 3645)
        self.assertEqual(payload["papers_changed"], 890)
        self.assertEqual(payload["papers_pushed"], 486)
        self.assertEqual(payload["field_change_counts"]["exposure_method"], 815)

    def test_write_blast_radius_reports(self) -> None:
        """Report files are written under scratch/patch_reports."""
        with tempfile.TemporaryDirectory() as tmp:
            original_root = patch_blast_radius.REPORT_ROOT
            patch_blast_radius.REPORT_ROOT = Path(tmp) / "patch_reports"
            try:
                ctx = patch_blast_radius.PatchFinishContext(
                    loop_type="golden_b",
                    patch_id="test_write",
                    scope_subnode="node2c",
                    reingest_summary={
                        "papers_processed": 10,
                        "papers_changed": 4,
                        "papers_written": 4,
                        "field_change_counts": {"study_type": 2},
                    },
                )
                paths = patch_blast_radius.write_blast_radius_reports(ctx)
                self.assertTrue(Path(paths["json"]).is_file())
                self.assertTrue(Path(paths["html"]).is_file())
                data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
                self.assertEqual(data["papers_scanned"], 10)
            finally:
                patch_blast_radius.REPORT_ROOT = original_root

    def test_top_changed_papers_with_links(self) -> None:
        """Top changed papers include before/after diffs and L/P links."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE papers (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    pmid TEXT,
                    doi TEXT,
                    full_text_link TEXT,
                    study_type TEXT,
                    exposure_method TEXT,
                    expert_locked_fields TEXT
                );
                """
            )
            ensure_sync_schema(conn)
            conn.execute(
                "INSERT INTO papers (id, title, pmid, doi, full_text_link, study_type, exposure_method) "
                "VALUES (1, 'Test paper', '12345', '10.1000/test', 'https://example.com/paper.pdf', "
                "'[\"Cell Culture (Other In Vitro)\"]', '[\"cannabinoids dissolved in media\"]')"
            )
            conn.commit()
            row = dict(conn.execute("SELECT * FROM papers WHERE id = 1").fetchone())
            baseline = dict(row)
            baseline["exposure_method"] = '["injected"]'
            save_baseline_rows(conn, [baseline])
            conn.close()

            top, top_prior, details, _sources = patch_blast_radius.analyze_reingest_changes(
                str(db_path),
                [1],
                ["study_type", "exposure_method"],
                papers_scanned=1,
            )
            self.assertEqual(len(top), 1)
            self.assertEqual(len(top_prior), 0)
            self.assertTrue(top[0]["had_prior_classification"])
            self.assertEqual(top[0]["fields_changed"], 1)
            self.assertEqual(top[0]["landing_url"], "https://doi.org/10.1000/test")
            self.assertEqual(top[0]["pdf_url"], "https://example.com/paper.pdf")
            changed = [d for d in top[0]["field_diffs"] if d["changed"]]
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]["field"], "exposure_method")
            self.assertEqual(details[0]["papers_changed"], 1)

    def test_local_report_file_url(self) -> None:
        """Repo-relative blast paths resolve to file:// URLs."""
        uri = patch_blast_radius.local_report_file_url(
            "scratch/patch_reports/golden_b/test/blast_radius.html"
        )
        self.assertTrue(uri.startswith("file://"))
        self.assertIn("blast_radius.html", uri)

    def test_resolve_before_state_prefers_sync_baseline(self) -> None:
        """Before-state resolution prefers sync baseline over snapshots."""
        baseline = {"study_type": '["Cell Culture (Other In Vitro)"]'}
        snapshot = {"study_type": '["Clinical (RCT)"]'}
        before, source = patch_blast_radius.resolve_before_state(
            stored_baseline=baseline,
            pre_reingest_snapshot=snapshot,
            postgres_snapshot=None,
            track_fields=["study_type"],
        )
        self.assertEqual(source, "sync_baseline")
        self.assertEqual(before, baseline)

    def test_prior_classification_excludes_first_time_papers(self) -> None:
        """Pre-existing top 10 excludes papers without a classified baseline."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE papers (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    study_type TEXT,
                    exposure_method TEXT,
                    cannabis_type TEXT,
                    outcome_domain TEXT,
                    thc_pct REAL,
                    expert_locked_fields TEXT
                );
                """
            )
            ensure_sync_schema(conn)
            conn.execute(
                "INSERT INTO papers (id, title, study_type, exposure_method, cannabis_type, "
                "outcome_domain, thc_pct) VALUES (1, 'Prior paper', "
                "'[\"Cell Culture (Other In Vitro)\"]', '[\"cannabinoids dissolved in media\"]', "
                "'[\"pure cannabinoid\"]', '[\"other\"]', 12.0)"
            )
            conn.execute(
                "INSERT INTO papers (id, title, study_type, exposure_method, cannabis_type, "
                "outcome_domain, thc_pct) VALUES (2, 'First-time paper', "
                "'[\"Cell Culture (Other In Vitro)\"]', "
                "'[\"cannabinoids dissolved in media\"]', '[\"pure cannabinoid\"]', '[\"other\"]', 5.0)"
            )
            conn.commit()
            prior_row = dict(conn.execute("SELECT * FROM papers WHERE id = 1").fetchone())
            baseline = dict(prior_row)
            baseline["exposure_method"] = '["injected"]'
            baseline["thc_pct"] = 8.0
            save_baseline_rows(conn, [baseline])
            conn.close()

            top, top_prior, _details, _sources = patch_blast_radius.analyze_reingest_changes(
                str(db_path),
                [1, 2],
                ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "thc_pct"],
                papers_scanned=2,
            )
            self.assertEqual(top[0]["paper_id"], 2)
            self.assertFalse(top[0]["had_prior_classification"])
            self.assertEqual(len(top_prior), 1)
            self.assertEqual(top_prior[0]["paper_id"], 1)
            self.assertTrue(top_prior[0]["had_prior_classification"])
            self.assertEqual(top_prior[0]["fields_changed"], 1)
            self.assertTrue(top_prior[0].get("updates_only"))
            changed = top_prior[0]["field_diffs"]
            self.assertEqual(len(changed), 1)
            self.assertEqual(changed[0]["field"], "thc_pct")
            self.assertTrue(changed[0]["property_updated"])

    def test_prior_top_excludes_null_to_measured_fills(self) -> None:
        """Pre-existing top 10 ignores null/unmeasured → first measurement fills."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE papers (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    study_type TEXT,
                    exposure_method TEXT,
                    cannabis_type TEXT,
                    outcome_domain TEXT,
                    thc_pct REAL,
                    dose_mg REAL,
                    expert_locked_fields TEXT
                );
                """
            )
            ensure_sync_schema(conn)
            conn.execute(
                "INSERT INTO papers (id, title, study_type, exposure_method, cannabis_type, "
                "outcome_domain, thc_pct, dose_mg) VALUES "
                "(20, 'New measurements', '[\"Animal Models (Mouse)\"]', '[\"injected\"]', "
                "'[\"pure cannabinoid\"]', '[\"pain\"]', 18.0, 300.0)"
            )
            conn.commit()
            row = dict(conn.execute("SELECT * FROM papers WHERE id = 20").fetchone())
            baseline = dict(row)
            baseline["study_type"] = '["Animal Models (Mouse)"]'
            baseline["thc_pct"] = None
            baseline["dose_mg"] = None
            save_baseline_rows(conn, [baseline])
            conn.close()

            _top, top_prior, _details, _sources = patch_blast_radius.analyze_reingest_changes(
                str(db_path),
                [20],
                ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "thc_pct", "dose_mg"],
                papers_scanned=1,
            )
            self.assertEqual(len(top_prior), 0)

    def test_prior_top_ranks_extractable_properties_only(self) -> None:
        """Pre-existing top 10 ranks by extractable dose/sample property changes only."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE papers (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    study_type TEXT,
                    exposure_method TEXT,
                    cannabis_type TEXT,
                    outcome_domain TEXT,
                    thc_pct REAL,
                    dose_mg REAL,
                    expert_locked_fields TEXT
                );
                """
            )
            ensure_sync_schema(conn)
            conn.execute(
                "INSERT INTO papers (id, title, study_type, exposure_method, cannabis_type, "
                "outcome_domain, thc_pct, dose_mg) VALUES "
                "(10, 'Many routing changes', '[\"Animal Models (Mouse)\"]', '[\"injected\"]', "
                "'[\"pure cannabinoid\"]', '[\"pain\"]', 5.0, 100.0)"
            )
            conn.execute(
                "INSERT INTO papers (id, title, study_type, exposure_method, cannabis_type, "
                "outcome_domain, thc_pct, dose_mg) VALUES "
                "(11, 'Dose change', '[\"Cell Culture (Other In Vitro)\"]', "
                "'[\"cannabinoids dissolved in media\"]', '[\"pure cannabinoid\"]', '[\"other\"]', "
                "20.0, 50.0)"
            )
            conn.commit()
            row10 = dict(conn.execute("SELECT * FROM papers WHERE id = 10").fetchone())
            baseline10 = dict(row10)
            baseline10["study_type"] = '["Clinical (RCT)"]'
            baseline10["exposure_method"] = '["unknown"]'
            baseline10["cannabis_type"] = '[]'
            baseline10["outcome_domain"] = '[]'
            save_baseline_rows(conn, [baseline10])
            row11 = dict(conn.execute("SELECT * FROM papers WHERE id = 11").fetchone())
            baseline11 = dict(row11)
            baseline11["study_type"] = '["Cell Culture (Other In Vitro)"]'
            baseline11["thc_pct"] = 1.0
            baseline11["dose_mg"] = 1.0
            save_baseline_rows(conn, [baseline11])
            conn.close()

            top, top_prior, _details, _sources = patch_blast_radius.analyze_reingest_changes(
                str(db_path),
                [10, 11],
                ["study_type", "exposure_method", "cannabis_type", "outcome_domain", "thc_pct", "dose_mg"],
                papers_scanned=2,
            )
            self.assertEqual(top[0]["paper_id"], 10)
            self.assertEqual(len(top_prior), 1)
            self.assertEqual(top_prior[0]["paper_id"], 11)
            self.assertEqual(top_prior[0]["fields_changed"], 2)
            self.assertTrue(all(d["property_updated"] for d in top_prior[0]["field_diffs"]))


class SimilarityCohortTests(unittest.TestCase):
    """Cohort validation before/after baseline diffs."""

    def _seed_paper(
        self,
        conn: sqlite3.Connection,
        *,
        paper_id: int,
        study_type: list,
        exposure_method: list,
        baseline_study: list | None = None,
        baseline_exposure: list | None = None,
    ) -> None:
        """Insert a paper row and optional baseline snapshot."""
        conn.execute(
            """
            INSERT INTO papers (
                id, title, abstract, classifier_version, study_type, exposure_method,
                cannabis_type, outcome_domain, publication_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id,
                f"Paper {paper_id}",
                "cannabinoid cell culture treatment",
                "maude-2.6.0",
                json.dumps(study_type),
                json.dumps(exposure_method),
                json.dumps(["pure cannabinoid"]),
                json.dumps(["other"]),
                "original research",
            ),
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone())
        keys = row.keys()
        row_dict = {k: row[k] for k in keys}
        if baseline_study is not None or baseline_exposure is not None:
            baseline_row = dict(row_dict)
            if baseline_study is not None:
                baseline_row["study_type"] = json.dumps(baseline_study)
            if baseline_exposure is not None:
                baseline_row["exposure_method"] = json.dumps(baseline_exposure)
            save_baseline_rows(conn, [baseline_row])

    def test_paper_1172_routing_change_detected(self) -> None:
        """Clinical misroute cleared to animal model counts as cohort routing change."""
        endpoint_id = "node2b.animal_models_mouse.injection_cannabinoids"
        endpoint = golden_dataset_paths.endpoint_by_id(endpoint_id)
        self.assertIsNotNone(endpoint)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE papers (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    abstract TEXT,
                    classifier_version TEXT,
                    study_type TEXT,
                    exposure_method TEXT,
                    cannabis_type TEXT,
                    outcome_domain TEXT,
                    publication_type TEXT,
                    expert_locked_fields TEXT,
                    full_text_link TEXT,
                    pmid TEXT,
                    doi TEXT,
                    duration_days REAL,
                    classification_confidence REAL,
                    classification_timestamp TEXT,
                    ingestion_status TEXT,
                    species TEXT,
                    summary TEXT,
                    tab_preclinical INTEGER DEFAULT 0,
                    tab_clinical INTEGER DEFAULT 0,
                    tab_unclassified_preclinical INTEGER DEFAULT 0,
                    tab_tangential INTEGER DEFAULT 0,
                    tab_review INTEGER DEFAULT 0
                );
                """
            )
            ensure_sync_schema(conn)
            # Before: wrongly tagged clinical + injected; after: mouse + injection cannabinoids
            self._seed_paper(
                conn,
                paper_id=1172,
                study_type=["Animal Models (Mouse)", "Animal Models (Rat)"],
                exposure_method=["injection cannabinoids", "intranasal"],
                baseline_study=["Clinical (RCT)", "Animal Models (Mouse)", "Animal Models (Rat)"],
                baseline_exposure=["injected", "injection cannabinoids", "intranasal"],
            )
            conn.close()

            payload = similarity_cohort_validation.validate_similarity_cohort(
                endpoint_id,
                sqlite_path=str(db_path),
                scope_subnode="node2b",
            )
            self.assertGreaterEqual(payload["subnode_papers_routing_changed"], 1)
            self.assertIn(1172, payload.get("sample_changed_paper_ids") or [])

    def test_cohort_routing_delta_improves(self) -> None:
        """Routing match after reingest can exceed baseline match."""
        endpoint_id = "node2c.cell_culture_other_in_vitro.cannabinoids_dissolved_in_media"
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE papers (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    abstract TEXT,
                    classifier_version TEXT,
                    study_type TEXT,
                    exposure_method TEXT,
                    cannabis_type TEXT,
                    outcome_domain TEXT,
                    publication_type TEXT,
                    expert_locked_fields TEXT,
                    full_text_link TEXT,
                    pmid TEXT,
                    doi TEXT,
                    duration_days REAL,
                    classification_confidence REAL,
                    classification_timestamp TEXT,
                    ingestion_status TEXT,
                    species TEXT,
                    summary TEXT,
                    tab_preclinical INTEGER DEFAULT 0,
                    tab_clinical INTEGER DEFAULT 0,
                    tab_unclassified_preclinical INTEGER DEFAULT 0,
                    tab_tangential INTEGER DEFAULT 0,
                    tab_review INTEGER DEFAULT 0
                );
                """
            )
            ensure_sync_schema(conn)
            self._seed_paper(
                conn,
                paper_id=7134,
                study_type=["Cell Culture (Other In Vitro)"],
                exposure_method=["cannabinoids dissolved in media"],
                baseline_study=["Cell Culture (Other In Vitro)"],
                baseline_exposure=["injected"],
            )
            conn.close()

            payload = similarity_cohort_validation.validate_similarity_cohort(
                endpoint_id,
                sqlite_path=str(db_path),
                scope_subnode="node2c",
            )
            self.assertGreaterEqual(payload["cohort_routing_match_after"], payload["cohort_routing_match_before"])


if __name__ == "__main__":
    unittest.main()
