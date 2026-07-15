"""Tests for manual expert-edit pre-harvest cycle."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import manual_edit_cycle
import rules_version


class TestRulesVersion(unittest.TestCase):
    """Semver bump helpers."""

    def test_bump_patch_version(self):
        """Patch segment increments correctly."""
        self.assertEqual(rules_version.bump_patch_version("2.6.0"), "2.6.1")
        self.assertEqual(rules_version.bump_patch_version("2.6.9"), "2.6.10")

    def test_compare_semver(self):
        """Semver ordering compares major, minor, then patch."""
        self.assertEqual(rules_version.compare_semver("2.7.0", "2.6.1"), 1)
        self.assertEqual(rules_version.compare_semver("2.6.1", "2.7.0"), -1)
        self.assertEqual(rules_version.compare_semver("2.7.0", "2.7.0"), 0)


class TestManualEditHelpers(unittest.TestCase):
    """Unit tests for manual edit batch construction helpers."""

    def test_dedupe_expert_edits_keeps_latest(self):
        """Latest timestamp wins per paper/field pair."""
        rows = [
            {"id": 1, "paper_id": 1172, "field_name": "thc_pct", "timestamp": "2026-06-28T10:00:00"},
            {"id": 2, "paper_id": 1172, "field_name": "thc_pct", "timestamp": "2026-06-28T11:00:00"},
        ]
        deduped = manual_edit_cycle.dedupe_expert_edits(rows)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["id"], 2)

    def test_build_miss_reason_llm_source(self):
        """Miss reason names LLM when classifier_version is llm-*."""
        reason = manual_edit_cycle.build_miss_reason(
            "study_type",
            '["Clinical Observational"]',
            '["Animal Models (Mouse)"]',
            "llm-pdf-reclassify-2.6.0",
        )
        self.assertIn("LLM", reason)
        self.assertIn("study_type", reason)

    def test_build_miss_reason_maude_source(self):
        """Miss reason names Maude when classifier_version is maude-*."""
        reason = manual_edit_cycle.build_miss_reason(
            "thc_pct",
            "25",
            None,
            "maude-2.6.0",
        )
        self.assertIn("Maude", reason)
        self.assertIn("thc_pct", reason)

    def test_reconstruct_pre_edit_paper(self):
        """Pre-edit reconstruction applies audit old_values."""
        paper = {"id": 1172, "study_type": '["Animal Models (Mouse)"]', "thc_pct": None}
        edits = [
            {"field_name": "study_type", "old_value": '["Clinical Observational"]'},
            {"field_name": "thc_pct", "old_value": "25"},
        ]
        restored = manual_edit_cycle.reconstruct_pre_edit_paper(paper, edits)
        self.assertEqual(restored["study_type"], ["Clinical Observational"])
        self.assertEqual(restored["thc_pct"], 25)


class TestPaper1172Scenario(unittest.TestCase):
    """Integration-style test mimicking paper 1172 expert corrections."""

    def setUp(self):
        """Creates an in-memory DB with paper 1172 and expert audit rows."""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE system_metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE papers (
                id INTEGER PRIMARY KEY,
                title TEXT,
                abstract TEXT,
                pmid TEXT,
                doi TEXT,
                full_text_link TEXT,
                study_type TEXT,
                exposure_method TEXT,
                cannabis_type TEXT,
                outcome_domain TEXT,
                publication_type TEXT,
                thc_pct REAL,
                cbd_pct REAL,
                classifier_version TEXT,
                classification_confidence REAL,
                expert_locked_fields TEXT DEFAULT '[]',
                tab_clinical INTEGER DEFAULT 0,
                tab_preclinical INTEGER DEFAULT 0
            );
            CREATE TABLE feedback_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER,
                field_name TEXT,
                old_value TEXT,
                new_value TEXT,
                title TEXT,
                abstract TEXT,
                timestamp TEXT,
                confidence_before_review REAL,
                classifier_version TEXT
            );
            """
        )
        self.conn.execute(
            """
            INSERT INTO papers (
                id, title, abstract, pmid, study_type, exposure_method, publication_type,
                thc_pct, classifier_version, tab_clinical, tab_preclinical
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1172,
                "Nose-to-brain administration of cannabidiol-loaded polymeric micelles improves the core behavioral symptoms of autism spectrum disorder",
                "Mouse model intranasal CBD micelles for autism behavioral symptoms.",
                "42004618",
                json.dumps(["Animal Models (Mouse)"]),
                json.dumps(["intranasal"]),
                "original research",
                None,
                "llm-pdf-reclassify-2.6.0",
                0,
                1,
            ),
        )
        now = datetime.now().isoformat()
        title = "Nose-to-brain administration of cannabidiol-loaded polymeric micelles improves the core behavioral symptoms of autism spectrum disorder"
        abstract = "Mouse model intranasal CBD micelles for autism behavioral symptoms."
        audits = [
            (1172, "study_type", json.dumps(["Clinical Observational"]), json.dumps(["Animal Models (Mouse)"]), now, "llm-pdf-reclassify-2.6.0"),
            (1172, "thc_pct", "25", None, now, "maude-2.6.0"),
        ]
        for idx, row in enumerate(audits, start=1):
            self.conn.execute(
                """
                INSERT INTO feedback_audit (
                    id, paper_id, field_name, old_value, new_value, title, abstract,
                    timestamp, classifier_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (idx, row[0], row[1], row[2], row[3], title, abstract, row[4], row[5]),
            )
        self.conn.commit()
        self.conn.close()

    def tearDown(self):
        """Clean up temp directory."""
        self.tmp.cleanup()

    @mock.patch("manual_edit_cycle.run_maude_compare_block")
    def test_build_manual_edit_batch_paper_1172(self, mock_maude):
        """Batch includes expert ground truth and Maude disagreement for paper 1172."""
        mock_maude.return_value = {
            "study_type": ["Clinical Observational"],
            "thc_pct": 25.0,
            "classifier_version": "maude-2.6.0",
        }
        db = mock.Mock()
        db.get_paper.return_value = {
            "id": 1172,
            "title": "Nose-to-brain administration of cannabidiol-loaded polymeric micelles improves the core behavioral symptoms of autism spectrum disorder",
            "abstract": "Mouse model intranasal CBD micelles for autism behavioral symptoms.",
            "pmid": "42004618",
            "study_type": json.dumps(["Animal Models (Mouse)"]),
            "exposure_method": json.dumps(["intranasal"]),
            "publication_type": "original research",
            "thc_pct": None,
            "classifier_version": "llm-pdf-reclassify-2.6.0",
            "outcome_domain": json.dumps(["other"]),
            "cannabis_type": json.dumps(["pure cannabinoid"]),
        }
        edits = manual_edit_cycle.dedupe_expert_edits([
            {"id": 1, "paper_id": 1172, "field_name": "study_type", "old_value": json.dumps(["Clinical Observational"]), "new_value": json.dumps(["Animal Models (Mouse)"]), "classifier_version": "llm-pdf-reclassify-2.6.0", "timestamp": "2026-06-28T12:00:00"},
            {"id": 2, "paper_id": 1172, "field_name": "thc_pct", "old_value": "25", "new_value": None, "classifier_version": "maude-2.6.0", "timestamp": "2026-06-28T12:01:00"},
        ])
        groups = {1172: edits}
        batch, miss_reasons, batch_id = manual_edit_cycle.build_manual_edit_batch(groups, db)

        self.assertTrue(batch_id.startswith("manual_edit_"))
        self.assertEqual(batch["mode"], "manual_edit")
        self.assertEqual(len(batch["results"]), 1)
        result = batch["results"][0]
        self.assertEqual(result["paper_id"], 1172)
        self.assertEqual(result["before_classifier_version"], "maude-2.6.0")
        self.assertIn("study_type", result["disagreement"]["fields"])
        self.assertGreaterEqual(len(miss_reasons), 2)
        self.assertTrue(any("LLM" in line or "Maude" in line for line in miss_reasons))

    def test_fetch_expert_edits_excludes_maude_prefix_rows(self):
        """Auto-calibration maude: rows are excluded; drawer rows are kept."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO feedback_audit (
                paper_id, field_name, old_value, new_value, title, abstract,
                timestamp, classifier_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1172, "maude:study_type", "a", "b", "", "", "2026-06-29T00:00:00", "maude-feedback-2.6.0"),
        )
        conn.commit()
        conn.close()

        db = mock.Mock()
        db.fetch_feedback_audit_since.return_value = [
            {"id": 1, "paper_id": 1172, "field_name": "study_type", "timestamp": "2026-06-28T12:00:00"},
        ]
        edits = manual_edit_cycle.fetch_expert_edits_since(db, "2026-06-28T00:00:00")
        db.fetch_feedback_audit_since.assert_called_once()
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["field_name"], "study_type")


if __name__ == "__main__":
    unittest.main()
