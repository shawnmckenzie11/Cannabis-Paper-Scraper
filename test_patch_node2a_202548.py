"""Tests for node2a patch from batch node2a_calibration_20260622_202548_118."""

import unittest

import extractor
import maude_classifier


class PatchNode2a202548Tests(unittest.TestCase):
    """Regression tests from offset-30 node2a RL holdout."""

    def test_study_type_override_keeps_clinical_exposure_unknown_on_cultivation(self):
        """Plant cultivation with clinical routing override still yields unknown exposure."""
        title = "Too Dense or Not Too Dense: Higher Planting Density"
        methods = (
            "Greenhouse cultivation at varying planting densities. "
            "Leaf extracts analyzed by HPLC. THCV standard from Cayman Chemical."
        )
        routing_type = ["Clinical (observational)"]
        with_override = extractor.extract_all_heuristics(
            title, methods, study_type_override=routing_type,
        )
        self.assertEqual(with_override["exposure_method"], ["unknown"])

    def test_plant_cultivation_routes_analytical_not_clinical(self):
        """Field/greenhouse cultivation without human subjects is not clinical observational."""
        title = "Planting density effects on cannabinoid yield in medical cannabis"
        methods = "Plants were grown in greenhouse pots at 1–6 plants per container."
        study_type = maude_classifier.resolve_study_type_for_routing(
            title,
            "Methods summary.",
            "original research",
            None,
            ["node2a_clinical", "node2c_in_vitro"],
            methods,
        )
        self.assertTrue(any(item.startswith("Cell Culture") for item in study_type))
        self.assertFalse(any(item.startswith("Clinical") for item in study_type))

    def test_medical_marijuana_patient_self_report_exposure(self):
        """Medical marijuana patients reporting smoked flower and edibles map to inhaled+oral."""
        title = "Splendor in the Grass? A Pilot Study Assessing Medical Marijuana"
        abstract = (
            "Methods: Participants reported smoking dried flower and consuming edibles "
            "during the 90-day assessment period."
        )
        routes = extractor.infer_exposure_method(
            title, abstract, ["Clinical (prospective)"],
        )
        self.assertIn("inhaled", routes)
        self.assertIn("oral", routes)

    def test_observational_polysubstance_stays_unknown_without_route_report(self):
        """General polysubstance cohorts without cannabis route reporting stay unknown."""
        title = "Polysubstance Use in Early Adulthood"
        abstract = (
            "Participants reported polysubstance use. Smoking tobacco frequently was more prevalent."
        )
        routes = extractor.infer_exposure_method(title, abstract, ["Clinical (observational)"])
        self.assertEqual(routes, ["unknown"])

    def test_sample_size_prefers_pilot_completed_n(self):
        """Pilot studies prefer completed-assessment n over screened/enrolled totals."""
        text = (
            "Thirty-two medical marijuana patients were screened; eleven completed "
            "the 90-day pilot assessment (n=11)."
        )
        self.assertEqual(extractor.extract_sample_size(text), 11)

    def test_sample_size_prefers_cohort_over_population_prevalence(self):
        """Survey respondent n beats catchment-area population totals."""
        text = (
            "Among 27169 residents in the catchment population, 1302 respondents "
            "completed the survey (n=1302)."
        )
        self.assertEqual(extractor.extract_sample_size(text), 1302)


if __name__ == "__main__":
    unittest.main()
