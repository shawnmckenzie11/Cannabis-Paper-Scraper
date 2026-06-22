"""Tests for node2a patch from batch node2a_calibration_20260622_193649_079."""

import unittest

import extractor
import maude_classifier


class PatchNode2a193649Tests(unittest.TestCase):
    """Regression tests from the second 10-paper node2a RL holdout."""

    def test_sample_size_prefers_completed_survey_n(self):
        """Completed participant counts beat population-level totals."""
        text = (
            "A total of 249 participants completed the survey (n=249). "
            "Population N=3816 residents were eligible."
        )
        self.assertEqual(extractor.extract_sample_size(text), 249)

    def test_sample_size_rejects_large_population_without_cohort(self):
        """Cross-sectional population totals without cohort context are skipped."""
        text = "Prevalence among 7044 residents in the catchment area (population N=7044)."
        self.assertIsNone(extractor.extract_sample_size(text))

    def test_duration_skips_lifecourse_followup(self):
        """Birth-cohort / adolescence-to-adulthood spans are not treatment duration."""
        text = (
            "Cannabis use from early adolescence to the mid-twenties was tracked "
            "over 11 years in a birth cohort."
        )
        self.assertIsNone(extractor.extract_duration_days(text))

    def test_frequency_null_for_app_assessment_visits(self):
        """Weekly mobile-app assessments are not administration_frequency."""
        text = "Participants used the mobile phone app weekly to report cannabis use."
        study_type = ["Clinical (observational)"]
        self.assertIsNone(
            extractor.extract_administration_frequency(text, study_type=study_type)
        )

    def test_outcome_includes_anxiety_for_youth_anxiety_app(self):
        """Youth anxiety assessment apps map to anxiety outcome domain."""
        title = "A mobile phone application for the assessment and management of youth anxiety"
        abstract = "We developed an app to support youth with cannabis and anxiety concerns."
        outcomes = extractor.extract_outcomes(title, abstract)
        self.assertIn("anxiety", outcomes)

    def test_human_biomarker_study_not_dissolved_in_media(self):
        """Human endocannabinoid biomarker studies do not route to dissolved-in-media exposure."""
        title = "Endocannabinoids linked to social exclusion"
        abstract = "Participants completed a social exclusion task."
        methods = "Plasma anandamide and 2-AG were quantified by LC-MS."
        study_type = ["Clinical (observational)"]
        routes = extractor.infer_exposure_method(title, abstract, study_type)
        self.assertNotIn("cannabinoids dissolved in media", routes)

    def test_empty_infer_study_type_falls_back_to_clinical(self):
        """Human-subjects PDFs with empty infer_study_type still route to clinical observational."""
        title = "Endocannabinoids and social exclusion in individuals"
        abstract = "Human participants completed exclusion tasks."
        methods = "Participants (n=50) completed the protocol."
        study_type = maude_classifier.resolve_study_type_for_routing(
            title,
            abstract,
            "original research",
            None,
            ["node2a_clinical"],
            methods,
        )
        self.assertTrue(any(item.startswith("Clinical") for item in study_type))


if __name__ == "__main__":
    unittest.main()
