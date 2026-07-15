"""Tests for harvest-time PDF upgrade helpers."""

import unittest
from unittest import mock

import harvest


class HarvestPdfUpgradeTests(unittest.TestCase):
    """Open-access / direct-PDF sync decisions at harvest time."""

    def test_open_access_attempts_sync_pdf(self):
        """Open-access papers should sync PDF/full-text at ingest."""
        paper = {"open_access": 1, "full_text_link": "https://pubmed.ncbi.nlm.nih.gov/1/"}
        self.assertTrue(harvest.harvest_should_attempt_sync_pdf(paper))

    def test_direct_pdf_link_attempts_sync_pdf(self):
        """Direct PDF URLs should sync even when open_access is unset."""
        paper = {
            "open_access": 0,
            "full_text_link": "https://example.org/paper.pdf",
        }
        self.assertTrue(harvest.harvest_should_attempt_sync_pdf(paper))

    def test_pubmed_landing_skips_sync_pdf(self):
        """Paywalled PubMed landing pages stay abstract-only at ingest."""
        paper = {
            "open_access": 0,
            "full_text_link": "https://pubmed.ncbi.nlm.nih.gov/12345/",
        }
        self.assertFalse(harvest.harvest_should_attempt_sync_pdf(paper))

    @mock.patch.object(harvest, "HARVEST_SYNC_PDF", False)
    def test_env_can_disable_sync_pdf(self):
        """HARVEST_SYNC_PDF=0 disables synchronous PDF attempts."""
        paper = {"open_access": 1, "full_text_link": "https://example.org/paper.pdf"}
        self.assertFalse(harvest.harvest_should_attempt_sync_pdf(paper))


if __name__ == "__main__":
    unittest.main()
