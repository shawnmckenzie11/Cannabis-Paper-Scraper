"""Tests for two-pass Maude re-ingestion helpers."""

import unittest

import reingest_heuristic_papers as rip


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


if __name__ == "__main__":
    unittest.main()
