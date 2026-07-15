# test_paper_text_cache_reuse.py
"""Regression: resolve_paper_text must prefer disk cache and skip short title_abstract."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paper_text_cache


class PaperTextCacheReuseTests(unittest.TestCase):
    """Cache-first resolution for golden/RL paths."""

    def test_disk_cache_beats_short_full_text_and_skips_network(self):
        """Short title+abstract must not block cached PDF text or trigger HTTP."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            paper_text_cache.write_cached_entry(
                99901,
                text="Methods section with enough characters to count as full PDF body. " * 100,
                source="pdf",
                full_text_link="https://example.com/paper.pdf",
                cache_dir=cache_dir,
            )
            with patch("calibration_pdf.resolve_classification_full_text") as mock_fetch:
                text, source = paper_text_cache.resolve_paper_text(
                    paper_id=99901,
                    full_text="Short Title\n\nShort abstract only.",
                    full_text_link="https://example.com/paper.pdf",
                    cache_dir=cache_dir,
                    allow_network_fetch=True,
                )
            mock_fetch.assert_not_called()
            self.assertEqual(source, "pdf")
            self.assertIn("Methods section", text or "")

    def test_skip_pdf_fetch_env_blocks_network(self):
        """SKIP_PDF_FETCH=1 returns abstract-only when cache misses."""
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            with patch.dict("os.environ", {"SKIP_PDF_FETCH": "1"}):
                with patch("calibration_pdf.resolve_classification_full_text") as mock_fetch:
                    text, source = paper_text_cache.resolve_paper_text(
                        paper_id=99902,
                        full_text_link="https://example.com/missing.pdf",
                        cache_dir=cache_dir,
                    )
            mock_fetch.assert_not_called()
            self.assertIsNone(text)
            self.assertEqual(source, "abstract")


if __name__ == "__main__":
    unittest.main()
