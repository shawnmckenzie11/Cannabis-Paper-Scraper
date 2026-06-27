"""Regression tests for node2a golden guard holdout papers."""

import unittest

import extractor as ex
from maude_classifier import classify_paper


class PatchNode2aGoldenGuardTests(unittest.TestCase):
    """Tests aligned to promoted golden_confirmed papers for node2a.clinical_observational.inhaled."""

    def test_spine_cbd_survey_exposure_oral_sublingual(self):
        """Anonymous spine CBD prevalence surveys infer oral/sublingual routes only."""
        title = (
            "Prevalence of Cannabidiol Use in Patients With Spine Complaints: "
            "Results of an Anonymous Survey"
        )
        exposure = ex.infer_exposure_method(title, "", ["Clinical (observational)"])
        self.assertEqual(exposure, ["oral", "sublingual"])

    def test_spine_cbd_survey_outcomes(self):
        """Spine complaint surveys include pain, sleep, and anxiety outcome domains."""
        title = (
            "Prevalence of Cannabidiol Use in Patients With Spine Complaints: "
            "Results of an Anonymous Survey"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (observational)"])
        self.assertIn("pain", outcomes)
        self.assertIn("sleep", outcomes)
        self.assertIn("anxiety", outcomes)

    def test_longitudinal_medical_cannabis_cognition_study_type(self):
        """Longitudinal observational medical cannabis cognition studies are prospective."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        types = ex.infer_study_type(title, "")
        self.assertEqual(types, ["Clinical (prospective)"])

    def test_longitudinal_medical_cannabis_duration_from_title(self):
        """12-month treatment windows in titles map to ~360 days."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        self.assertEqual(ex.extract_duration_days(title), 360.0)

    def test_longitudinal_medical_cannabis_outcomes(self):
        """Medical cannabis cognition longitudinal studies include anxiety and sleep."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (prospective)"])
        self.assertIn("cognition", outcomes)
        self.assertIn("anxiety", outcomes)
        self.assertIn("sleep", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_itc_smoking_survey_exposure_unknown(self):
        """Population smoking/vaping surveys without administration map exposure to unknown."""
        title = (
            "Associations of Cannabis Use, High-Risk Alcohol Use, and Depressive Symptomology "
            "with Motivation and Attempts to Quit Cigarette Smoking Among Adults: "
            "Findings from the 2020 ITC Four Country Smoking and Vaping Survey"
        )
        exposure = ex.infer_exposure_method(title, "", ["Clinical (observational)"])
        self.assertEqual(exposure, ["unknown"])

    def test_amotivational_hypothesis_outcomes(self):
        """Amotivational hypothesis papers map to addiction and cognition outcomes."""
        title = (
            "Acute and chronic effects of cannabinoids on effort-related decision-making "
            "and reward learning: an evaluation of the cannabis 'amotivational' hypotheses"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (RCT)"])
        self.assertIn("addiction", outcomes)
        self.assertIn("cognition", outcomes)
        self.assertNotIn("other", outcomes)


if __name__ == "__main__":
    unittest.main()
