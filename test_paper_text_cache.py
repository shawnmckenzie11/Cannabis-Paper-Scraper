# test_paper_text_cache.py
"""Tests for local paper PDF/full-text cache."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paper_text_cache


class PaperTextCacheTests(unittest.TestCase):
    """Validates cache read/write and batch paper iteration."""

    def test_write_and_read_cached_entry(self):
        """Cached text and metadata round-trip on disk."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            paper_text_cache.write_cached_entry(
                42,
                text="Methods: rats received THC.",
                source="pdf",
                full_text_link="https://example.com/paper.pdf",
                pdf_bytes=b"%PDF-1.4 fake",
                cache_dir=cache_dir,
            )
            entry = paper_text_cache.read_cached_entry(42, cache_dir)
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertIn("THC", entry["text"])
            self.assertTrue(entry["has_pdf"])
            self.assertTrue((cache_dir / "pdfs" / "42.pdf").exists())

    def test_fetch_and_cache_skips_existing(self):
        """Second fetch returns skipped when cache entry exists."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            paper_text_cache.write_cached_entry(
                7,
                text="Cached already",
                source="fulltext",
                cache_dir=cache_dir,
            )
            outcome = paper_text_cache.fetch_and_cache_paper(7, cache_dir=cache_dir)
            self.assertEqual(outcome["status"], "skipped")

    @patch("paper_text_cache.store_paper_text_if_missing")
    @patch("calibration_pdf.resolve_classification_full_text", return_value=("PMC body", "fulltext"))
    def test_resolve_paper_text_caches_on_miss(self, _resolve, mock_store):
        """Disk cache is populated after a successful fetch."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            text, source = paper_text_cache.resolve_paper_text(
                paper_id=55,
                pmid="999",
                use_disk_cache=True,
                cache_dir=cache_dir,
            )
            self.assertEqual(text, "PMC body")
            self.assertEqual(source, "fulltext")
            mock_store.assert_called_once()

    @patch("paper_text_cache.download_pdf_bytes", return_value=None)
    @patch("calibration_pdf.resolve_classification_full_text", return_value=("PMC body", "fulltext"))
    def test_fetch_and_cache_resolves_text(self, _resolve, _download):
        """Fetch stores resolved full text when download succeeds."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            outcome = paper_text_cache.fetch_and_cache_paper(
                99,
                pmid="12345",
                cache_dir=cache_dir,
            )
            self.assertEqual(outcome["status"], "cached")
            entry = paper_text_cache.read_cached_entry(99, cache_dir)
            self.assertEqual(entry.get("text"), "PMC body")


if __name__ == "__main__":
    unittest.main()
