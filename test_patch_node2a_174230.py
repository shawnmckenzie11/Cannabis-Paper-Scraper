"""Tests for node2a patch from batch node2a_calibration_20260622_174230_109."""

import unittest

import extractor
import maude_classifier


class PatchNode2a174230Tests(unittest.TestCase):
    """Regression tests from the 10-paper node2a RL feedback handoff."""

    def test_clinical_oral_exposure_not_stripped(self):
        """Clinical oral routes survive exposure_method normalization."""
        title = "Oral cannabinoids in palliative care"
        abstract = (
            "METHODS: A randomized trial. Participants received oral THC capsules "
            "once daily for 28 days."
        )
        study_type = ["Clinical (RCT)"]
        methods = extractor.infer_exposure_method(title, abstract, study_type)
        self.assertIn("oral", methods)

    def test_sample_size_rejects_url_reference_n(self):
        """Reference URL query params do not populate sample_size."""
        text = (
            "Participants report usage patterns. "
            "See https://example.org/indicadors/?id=aec&n=15800&lang=es for census data."
        )
        self.assertIsNone(extractor.extract_sample_size(text))

    def test_sample_size_prefers_participant_cohort(self):
        """Cohort n= near participants beats stray population counts."""
        text = "A total of 249 participants completed the survey (n=249). Population N=3816."
        self.assertEqual(extractor.extract_sample_size(text), 249)

    def test_administration_frequency_null_for_observational_use(self):
        """Observational cannabis-use frequency does not map to administration_frequency."""
        text = "Daily cannabis users were more likely to report withdrawal symptoms."
        study_type = ["Clinical (observational)"]
        self.assertIsNone(
            extractor.extract_administration_frequency(text, study_type=study_type)
        )

    def test_clinical_routing_overrides_review_title(self):
        """Primary-data survey PDF routes to original research despite review-like title."""
        title = "The role of stigma in cannabis use disclosure: an exploratory study"
        abstract = "Background on stigma."
        full_text = (
            "Methods: Participants were recruited between July and December 2022. "
            "A cross-sectional survey was administered. n=249 participants completed the questionnaire."
        )
        pub, _, nodes, _ = maude_classifier.route_publication_type(
            title, f"{title} {full_text}"
        )
        self.assertEqual(pub, "original research")
        self.assertIn("node1a_original", nodes)

    def test_human_subjects_drop_animal_study_type(self):
        """Human participant studies drop incidental animal-model labels from PDF methods."""
        title = "Social exclusion and endocannabinoids"
        abstract = "We examined human participants during social exclusion."
        methods = "Rat primary neurons were cited in background only. Participants (n=50) completed tasks."
        study_type = maude_classifier.resolve_study_type_for_routing(
            title,
            abstract,
            "original research",
            None,
            ["node2a_clinical", "node2b_in_vivo"],
            methods,
        )
        self.assertTrue(any(item.startswith("Clinical") for item in study_type))
        self.assertFalse(any(item.startswith("Animal Models") for item in study_type))


if __name__ == "__main__":
    unittest.main()
