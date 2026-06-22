"""Tests for Maude alignment-based confidence scoring."""

import unittest

import maude_confidence


class MaudeConfidenceTests(unittest.TestCase):
    """Node alignment maps to classification_confidence."""

    def test_clinical_paper_uses_node2a_alignment(self):
        """Clinical routing sub-node inherits node2a post-patch alignment."""
        extracted = {
            "publication_type": "original research",
            "study_type": ["Clinical (RCT)"],
            "ingestion_status": "relevant",
        }
        confidence = maude_confidence.confidence_for_classification(extracted)
        alignments = maude_confidence.cached_alignment_pcts()
        expected = round(alignments["node2a"] / 100.0, 3)
        self.assertEqual(confidence, expected)

    def test_invivo_paper_uses_node2b_alignment(self):
        """Animal in-vivo routing inherits node2b alignment."""
        extracted = {
            "publication_type": "original research",
            "study_type": ["Animal Models (Mouse)"],
            "ingestion_status": "relevant",
        }
        confidence = maude_confidence.confidence_for_classification(extracted)
        alignments = maude_confidence.cached_alignment_pcts()
        expected = round(alignments["node2b"] / 100.0, 3)
        self.assertEqual(confidence, expected)


if __name__ == "__main__":
    unittest.main()
