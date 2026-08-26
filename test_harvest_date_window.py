"""Tests for PubMed Entrez-date harvest window helpers."""

import unittest

import harvest


class HarvestDateWindowTests(unittest.TestCase):
    """Daily harvest must date-window PubMed instead of capping at 200."""

    def test_edat_filter_uses_term_syntax(self):
        """NCBI ignores mindate kwargs; the query must include [edat]."""
        term = harvest.apply_pubmed_edat_filter(
            "cannabis OR cannabinoid OR marijuana",
            mindate="2026-07-17",
            maxdate="2026-08-24",
        )
        self.assertIn("[edat]", term)
        self.assertIn("2026/07/17:2026/08/24", term)

    def test_zero_max_results_fetches_full_count(self):
        """max_results=0 must not fetch zero papers."""
        self.assertEqual(harvest.pubmed_fetch_limit(0, 482), 482)
        self.assertEqual(harvest.pubmed_fetch_limit(None, 482), 482)
        self.assertEqual(harvest.pubmed_fetch_limit(200, 482), 200)


if __name__ == "__main__":
    unittest.main()
