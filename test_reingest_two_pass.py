"""Tests for two-pass Maude re-ingestion helpers."""

import unittest
from unittest.mock import patch

import reingest_heuristic_papers as rip
from db_manager import DatabaseManager
from paper_tab_flags import compute_tab_flags


class ReingestTwoPassTests(unittest.TestCase):
    """Two-pass where-clause and paper eligibility helpers."""

    def test_slow_pass_extra_clause_mentions_pmid_and_sparse(self):
        """Slow pass SQL includes PMC ids and sparse field checks."""
        clause = rip._slow_pass_extra_clause()
        self.assertIn("pmid", clause)
        self.assertIn("study_type", clause)

    def test_paper_needs_slow_pass_with_pmid(self):
        """Papers with PMID qualify for slow pass."""
        paper = {"pmid": "12345", "doi": None, "full_text_link": None, "study_type": '["Clinical Trial"]'}
        self.assertTrue(rip.paper_needs_slow_pass(paper))

    def test_paper_needs_slow_pass_sparse_fields(self):
        """Papers with empty classification fields qualify for slow pass."""
        paper = {"pmid": None, "doi": None, "full_text_link": None, "study_type": None}
        self.assertTrue(rip.paper_needs_slow_pass(paper))

    def test_paper_needs_slow_pass_dense_abstract_only(self):
        """Fully classified abstract-only papers skip slow pass unless text source exists."""
        paper = {
            "pmid": None,
            "doi": None,
            "full_text_link": "https://pubmed.ncbi.nlm.nih.gov/999/",
            "study_type": '["Clinical Trial"]',
            "exposure_method": '["oral"]',
            "cannabis_type": '["THC"]',
            "outcome_domain": '["pain"]',
        }
        self.assertFalse(rip.paper_needs_slow_pass(paper))

    def test_already_at_pass_tier_fast(self):
        """Fast pass skips papers already on current maude abstract tier."""
        rules = "2.6.0"
        paper = {"classifier_version": "maude-2.6.0"}
        self.assertTrue(rip._already_at_pass_tier(paper, "fast", rules))

    def test_already_at_pass_tier_slow_needs_upgrade(self):
        """Slow pass still runs on abstract-only current-version papers."""
        rules = "2.6.0"
        paper = {"classifier_version": "maude-2.6.0"}
        self.assertFalse(rip._already_at_pass_tier(paper, "slow", rules))

    def test_paper_update_is_noop_when_unchanged(self):
        """No-op detection skips writes when classification and tab flags match."""
        paper = {
            "id": 1,
            "study_type": '["Clinical Trial"]',
            "exposure_method": '["oral"]',
            "cannabis_type": '["THC"]',
            "outcome_domain": '["pain"]',
            "publication_type": "original research",
            "ingestion_status": None,
            "classifier_version": "maude-2.6.0",
            "classification_timestamp": "2026-01-01T00:00:00",
            "tab_preclinical": 0,
            "tab_clinical": 1,
            "tab_unclassified_preclinical": 0,
            "tab_tangential": 0,
            "tab_review": 0,
        }
        extracted = {
            "study_type": ["Clinical Trial"],
            "exposure_method": ["oral"],
            "cannabis_type": ["THC"],
            "outcome_domain": ["pain"],
            "publication_type": "original research",
            "ingestion_status": None,
            "classifier_version": "maude-2.6.0",
            "classification_timestamp": "2026-06-24T12:00:00",
        }
        for col in rip.UPDATE_COLUMNS:
            if col not in extracted and col not in rip.NOOP_SKIP_COLUMNS:
                extracted[col] = paper.get(col)
        self.assertTrue(rip.paper_update_is_noop(paper, extracted))

    def test_paper_update_is_not_noop_when_study_type_changes(self):
        """No-op detection returns False when a tracked field changes."""
        paper = {
            "id": 2,
            "study_type": '["Clinical Trial"]',
            "publication_type": "original research",
            "classifier_version": "maude-2.0.0",
        }
        extracted = {
            "study_type": ["RCT"],
            "publication_type": "original research",
            "classifier_version": "maude-2.6.0",
        }
        self.assertFalse(rip.paper_update_is_noop(paper, extracted))

    def test_build_merged_update_includes_tab_columns(self):
        """Merged UPDATE includes tab flag columns alongside classification fields."""
        paper = {"id": 3, "expert_locked_fields": "[]"}
        extracted = {
            "study_type": ["animal study"],
            "exposure_method": ["inhaled"],
            "cannabis_type": ["THC"],
            "outcome_domain": ["behavior"],
            "publication_type": "original research",
            "ingestion_status": None,
            "classifier_version": "maude-2.6.0",
            "classification_timestamp": "2026-06-24T12:00:00",
            "classification_confidence": 0.8,
        }
        for col in rip.UPDATE_COLUMNS:
            extracted.setdefault(col, None)

        set_parts, params = rip.build_merged_update(paper, extracted)
        joined = " ".join(set_parts)
        self.assertIn("tab_preclinical", joined)
        self.assertIn("tab_clinical", joined)
        flags = compute_tab_flags(
            publication_type=extracted["publication_type"],
            study_type=extracted["study_type"],
            ingestion_status=extracted["ingestion_status"],
        )
        for column in flags:
            self.assertIn(f"{column} = ?", set_parts)

    def test_skip_current_version_clause_fast(self):
        """Fast-pass SQL skip clause excludes all current maude tier labels."""
        clause = rip._skip_current_version_clause("fast", "2.6.0")
        self.assertIn("maude-2.6.0", clause)
        self.assertIn("maude-pdf-2.6.0", clause)
        self.assertIn("maude-ft-2.6.0", clause)
        self.assertIn("NOT IN", clause)

    def test_already_at_pass_tier_slow_pdf(self):
        """Slow pass skips papers already stamped at the pdf tier."""
        paper = {"classifier_version": "maude-pdf-2.6.0"}
        self.assertTrue(rip._already_at_pass_tier(paper, "slow", "2.6.0"))

    def test_already_at_pass_tier_slow_fulltext(self):
        """Slow pass skips papers stamped at current or legacy full-text tiers."""
        self.assertTrue(
            rip._already_at_pass_tier({"classifier_version": "maude-ft-2.6.0"}, "slow", "2.6.0")
        )
        self.assertTrue(
            rip._already_at_pass_tier(
                {"classifier_version": "maude-fulltext-2.6.0"}, "slow", "2.6.0"
            )
        )

    def test_source_bucket_labels(self):
        """Source bucket mapping recognizes pdf, ft, and legacy fulltext labels."""
        self.assertEqual(rip._source_bucket("maude-pdf-2.6.0"), "pdf")
        self.assertEqual(rip._source_bucket("maude-ft-2.6.0"), "fulltext")
        self.assertEqual(rip._source_bucket("maude-fulltext-2.6.0"), "fulltext")
        self.assertEqual(rip._source_bucket("maude-2.6.0"), "abstract")

    @patch.object(DatabaseManager, "sync_tab_flags_for_paper")
    def test_apply_paper_update_uses_single_update(self, mock_sync):
        """Merged write performs one UPDATE and does not call sync_tab_flags_for_paper."""
        db = DatabaseManager()
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO papers (
                title, abstract, study_type, publication_type, classifier_version, date_harvested
            )
            VALUES (
                'Test', 'Abstract', '[]', 'original research', 'heuristic-1.0.0', '2026-01-01'
            )
            """
        )
        conn.commit()
        paper_id = cur.lastrowid
        paper = {
            "id": paper_id,
            "expert_locked_fields": "[]",
            "study_type": "[]",
            "publication_type": "original research",
        }
        extracted = {
            "study_type": ["Clinical Trial"],
            "exposure_method": ["oral"],
            "cannabis_type": ["THC"],
            "outcome_domain": ["pain"],
            "publication_type": "original research",
            "ingestion_status": None,
            "species": None,
            "summary": "summary",
            "duration_days": None,
            "inhaled_exposure_duration": None,
            "administration_frequency": None,
            "treatment_duration": None,
            "sample_size": None,
            "thc_pct": None,
            "cbd_pct": None,
            "dose_mg": None,
            "strain_reported": None,
            "strain_normalized": None,
            "classification_confidence": 0.5,
            "classification_timestamp": "2026-06-24T12:00:00",
            "classifier_version": "maude-2.6.0",
        }
        stats = rip.ReingestStats()
        wrote = rip._apply_paper_update(db, conn, cur, paper, extracted, stats=stats)
        conn.commit()
        self.assertTrue(wrote)
        mock_sync.assert_not_called()
        cur.execute(
            "SELECT tab_clinical, classifier_version FROM papers WHERE id = ?",
            (paper_id,),
        )
        row = cur.fetchone()
        self.assertEqual(row["classifier_version"], "maude-2.6.0")
        self.assertEqual(int(row["tab_clinical"]), 1)
        conn.close()


if __name__ == "__main__":
    unittest.main()
