"""Tests for fill-aware Maude confidence scoring."""

import unittest

import maude_confidence


class MaudeConfidenceFillTests(unittest.TestCase):
    """Confidence reflects alignment plus real-fill / cue coverage."""

    def test_clinical_paper_uses_node2a_alignment_when_filled(self):
        """Filled clinical papers stay near node2a alignment."""
        extracted = {
            "publication_type": "original research",
            "study_type": ["Clinical (RCT)"],
            "ingestion_status": "relevant",
            "exposure_method": ["oral"],
            "cannabis_type": ["pure cannabinoid"],
            "outcome_domain": ["pain"],
            "classifier_version": "maude-pdf-2.7.0",
            "_maude_meta": {"methods_used": True, "cue_score": 0.7},
        }
        confidence = maude_confidence.confidence_for_classification(extracted)
        alignments = maude_confidence.cached_alignment_pcts()
        base = alignments["node2a"] / 100.0
        self.assertGreater(confidence, base)
        self.assertLessEqual(confidence, 0.95)

    def test_unknown_fields_reduce_confidence(self):
        """Unknown exposure/cannabis/outcome lower confidence below base alignment."""
        sparse = {
            "publication_type": "original research",
            "study_type": ["Clinical (observational)"],
            "ingestion_status": "relevant",
            "exposure_method": ["unknown"],
            "cannabis_type": ["unknown"],
            "outcome_domain": [],
            "classifier_version": "maude-2.7.0",
        }
        filled = {
            **sparse,
            "exposure_method": ["inhaled"],
            "cannabis_type": ["dried flower"],
            "outcome_domain": ["addiction"],
        }
        sparse_conf = maude_confidence.confidence_for_classification(sparse)
        filled_conf = maude_confidence.confidence_for_classification(filled)
        self.assertLess(sparse_conf, filled_conf)

    def test_real_fill_rate_ignores_unknown(self):
        """real_fill_rate treats unknown as unfilled."""
        rate = maude_confidence.real_fill_rate(
            {
                "exposure_method": ["unknown"],
                "cannabis_type": ["dried flower"],
                "outcome_domain": [],
            }
        )
        self.assertAlmostEqual(rate, 1.0 / 3.0)

    def test_invivo_paper_uses_node2b_alignment(self):
        """Animal in-vivo routing inherits node2b alignment as the base."""
        extracted = {
            "publication_type": "original research",
            "study_type": ["Animal Models (Mouse)"],
            "ingestion_status": "relevant",
            "exposure_method": ["injection cannabinoids"],
            "cannabis_type": ["pure cannabinoid"],
            "outcome_domain": ["neuroprotection"],
        }
        confidence = maude_confidence.confidence_for_classification(extracted)
        alignments = maude_confidence.cached_alignment_pcts()
        self.assertGreaterEqual(confidence, alignments["node2b"] / 100.0 - 0.01)


if __name__ == "__main__":
    unittest.main()
