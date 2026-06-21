"""Tests for shared LLM/Maude classification schema normalization."""

import unittest

import classification_schema
import maude_classifier


class TestClassificationSchema(unittest.TestCase):
    """Unit tests for coarse publication_type and ingestion_status normalization."""

    def test_promotes_systematic_review_to_coarse_publication(self):
        """Granular systematic review labels move to study_type under review."""
        normalized = classification_schema.normalize_classification_record(
            {"publication_type": "systematic review", "study_type": []},
            "A systematic review of cannabis",
            "We searched PubMed.",
        )
        self.assertEqual(normalized["publication_type"], "review")
        self.assertIn("systematic review", normalized["study_type"])

    def test_llm_ingestion_status_inferred_when_missing(self):
        """Heuristic payloads receive ingestion_status when absent."""
        normalized = classification_schema.normalize_classification_record(
            {"publication_type": "original research", "study_type": ["Clinical (RCT)"]},
            "Cannabis reduces pain in RCT",
            "We randomized patients to receive THC or placebo.",
        )
        self.assertEqual(normalized["ingestion_status"], "relevant")

    def test_compare_includes_agreed_fields(self):
        """Classifier comparison returns agreed high-level fields."""
        maude = {
            "ingestion_status": "relevant",
            "publication_type": "original research",
            "study_type": ["Clinical (RCT)"],
        }
        llm = {
            "ingestion_status": "relevant",
            "publication_type": "original research",
            "study_type": ["Animal Models (Mouse)"],
        }
        result = classification_schema.compare_classifiers(maude, llm)
        self.assertIn("ingestion_status", result["agreed_fields"])
        self.assertIn("publication_type", result["agreed_fields"])
        self.assertIn("study_type", result["fields"])

    def test_maude_systematic_review_routes_coarse_publication(self):
        """Maude routes systematic reviews to review + systematic review study_type."""
        result = maude_classifier.classify_paper(
            "A systematic review of cannabis for chronic pain",
            "We searched PubMed and included 42 studies.",
        )
        self.assertEqual(result["publication_type"], "review")
        self.assertIn("systematic review", result["study_type"])
        self.assertIn("node3a", result["_maude_meta"]["nodes_visited"])

    def test_original_research_without_subtype_routes_node2d(self):
        """Unbranched original research maps to Node 2D rather than lingering at Node 1A."""
        subnode = classification_schema.infer_routing_subnode(
            "node1_routing",
            {"publication_type": "original research", "study_type": ["Other"]},
        )
        self.assertEqual(subnode, "node2d")

    def test_invivo_smoke_duration_routing(self):
        """In vivo smoke exposure keeps regimen fields and clears in vitro duration."""
        normalized = classification_schema.normalize_classification_record(
            {
                "publication_type": "original research",
                "study_type": ["Animal Models (Mouse)"],
                "exposure_method": ["whole body. smoke/vapor"],
                "duration_days": 21.0,
                "repeat_exposure_count": 1,
                "inhaled_exposure_duration": "30 minutes",
                "administration_frequency": "once daily",
                "treatment_duration": "24 hours",
            },
            "Mouse smoke exposure",
            "Mice received whole-body cannabis smoke for 30 minutes once daily for 3 weeks.",
        )
        self.assertEqual(normalized["exposure_regimen_bin"], "acute")
        self.assertIsNone(normalized["treatment_duration"])
        self.assertEqual(normalized["inhaled_exposure_duration"], "30 minutes")

    def test_invitro_direct_smoke_duration_routing(self):
        """Direct in vitro smoke exposure uses inhaled duration and repeat count, not treatment_duration."""
        normalized = classification_schema.normalize_classification_record(
            {
                "publication_type": "original research",
                "study_type": ["Cell Culture (Cell Lines)"],
                "exposure_method": ["exposure of cells to smoke/vapor"],
                "inhaled_exposure_duration": "15 minutes",
                "repeat_exposure_count": 3,
                "treatment_duration": "15 minutes",
            },
            "Cell smoke exposure",
            "Cells were exposed to cannabis smoke for 15 minutes on 3 separate occasions.",
        )
        self.assertEqual(normalized["inhaled_exposure_duration"], "15 minutes")
        self.assertEqual(normalized["repeat_exposure_count"], 3)
        self.assertIsNone(normalized["treatment_duration"])

    def test_invitro_dissolved_duration_routing(self):
        """Dissolved cannabinoid in vitro exposure keeps treatment_duration only."""
        normalized = classification_schema.normalize_classification_record(
            {
                "publication_type": "original research",
                "study_type": ["Cell Culture (Cell Lines)"],
                "exposure_method": ["cannabinoids dissolved in media"],
                "treatment_duration": "24 hours",
                "inhaled_exposure_duration": "10 minutes",
                "repeat_exposure_count": 2,
            },
            "THC in media",
            "Cells were treated with THC dissolved in media for 24 hours.",
        )
        self.assertEqual(normalized["treatment_duration"], "24 hours")
        self.assertIsNone(normalized["inhaled_exposure_duration"])
        self.assertIsNone(normalized["repeat_exposure_count"])


if __name__ == "__main__":
    unittest.main()
