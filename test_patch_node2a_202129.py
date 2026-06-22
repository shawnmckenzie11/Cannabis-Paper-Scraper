"""Tests for node2a patch from batch node2a_calibration_20260622_202129_219."""

import unittest

import extractor
import maude_classifier


class PatchNode2a202129Tests(unittest.TestCase):
    """Regression tests from the offset-20 node2a RL holdout."""

    def test_analytical_title_does_not_override_human_participants(self):
        """Human-subject observational work keeps clinical study_type despite quantification title."""
        title = (
            "Quantification of the content of cannabinol in commercially available e-liquids "
            "and studies on their effects"
        )
        abstract = "Background on e-liquid cannabinoid content."
        methods = (
            "Methods: Thirteen healthy volunteers participated in an observational survey. "
            "Participants completed questionnaires about e-liquid use."
        )
        study_type = maude_classifier.resolve_study_type_for_routing(
            title,
            abstract,
            "original research",
            None,
            ["node2a_clinical", "node2c_in_vitro"],
            methods,
        )
        self.assertTrue(any(item.startswith("Clinical") for item in study_type))
        self.assertFalse(any(item.startswith("Cell Culture") for item in study_type))

    def test_sample_size_rejects_e_liquid_product_counts(self):
        """Commercial e-liquid product counts do not beat participant cohort N."""
        text = (
            "One hundred eighty commercially available e-liquid products were analyzed. "
            "Thirteen participants completed the survey (n=13)."
        )
        self.assertEqual(extractor.extract_sample_size(text), 13)

    def test_sample_size_prefers_included_analysis_cohort(self):
        """Included-for-analysis participant totals beat screening/enrollment counts."""
        text = (
            "One hundred twenty-five individuals were screened and 47 were enrolled. "
            "Thirty-three participants were included in the final analysis (n=33)."
        )
        self.assertEqual(extractor.extract_sample_size(text), 33)


if __name__ == "__main__":
    unittest.main()
