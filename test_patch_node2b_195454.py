"""Tests for node2b patch from batch node2b_calibration_20260622_195454_576."""

import unittest

import extractor


class PatchNode2b195454Tests(unittest.TestCase):
    """Regression tests from the offset-10 node2b RL holdout."""

    def test_frequency_once_a_day_maps_to_daily(self):
        """Once-a-day phrasing normalizes to daily for A/B alignment."""
        text = "Mice received THC once a day for 22 days."
        self.assertEqual(extractor.extract_administration_frequency(text), "daily")

    def test_frequency_twice_a_day_maps_to_twice_daily(self):
        """Twice-a-day phrasing normalizes to twice daily."""
        text = "Treatments were administered twice a day for 4 weeks."
        self.assertEqual(extractor.extract_administration_frequency(text), "twice daily")

    def test_invivo_prefers_animal_strain_over_cannabidiol(self):
        """Rodent model strain beats bare cannabinoid name on in-vivo dosing papers."""
        text = (
            "Male Sprague-Dawley rats received cannabidiol (CBD, 30 mg/kg) "
            "via oral gavage as a single dose."
        )
        study_type = ["Animal Models (Rat)"]
        reported, _ = extractor.extract_strain_info(text, study_type=study_type)
        self.assertEqual(reported, "Sprague-Dawley")

    def test_invivo_synthetic_still_reports_cp55940(self):
        """Synthetic agonist test articles still win over vendor animal strains."""
        text = "C57BL/6 mice from Harlan received CP-55,940 (3 mg/kg, i.p.) daily for 4 days."
        study_type = ["Animal Models (Mouse)"]
        reported, _ = extractor.extract_strain_info(text, study_type=study_type)
        self.assertIn("CP-55,940", reported or "")

    def test_mixed_invivo_primary_not_dissolved_in_media(self):
        """In-vivo dosing on mixed cell-culture papers does not route to dissolved-in-media."""
        title = "Microglial CB1 and adolescent THC in mice"
        abstract = (
            "METHODS: Primary microglia were incubated with THC. "
            "Adolescent C57BL/6 mice received THC (8 mg/kg, s.c.) once daily for 22 days."
        )
        study_type = ["Animal Models (Mouse)", "Cell Culture (Primary Cells)"]
        routes = extractor.infer_exposure_method(title, abstract, study_type)
        self.assertNotIn("cannabinoids dissolved in media", routes)
        self.assertIn("injection cannabinoids", routes)

    def test_duration_from_received_for_weeks(self):
        """Animal dosing windows like 'received ... for 4 weeks' populate duration_days."""
        text = "Mice received CBD (10 mg/kg, i.p.) for 4 weeks before behavioral testing."
        self.assertEqual(extractor.extract_duration_days(text), 28.0)

    def test_mixed_invivo_primary_extracts_duration(self):
        """Mixed papers with in-vivo dosing still populate duration_days."""
        title = "THC in mice and microglia"
        abstract = (
            "Cells were incubated 24 h. Mice received THC daily for 22 days (8 mg/kg, s.c.)."
        )
        study_type = ["Animal Models (Mouse)", "Cell Culture (Primary Cells)"]
        result = extractor.extract_all_heuristics(title, abstract)
        self.assertEqual(result.get("duration_days"), 22.0)

    def test_pure_cannabinoid_from_mg_kg_dosing(self):
        """Isolated cannabinoid mg/kg dosing infers pure cannabinoid product type."""
        title = "CBD effects in rats"
        abstract = "Rats received cannabidiol (30 mg/kg, i.p.) daily for 28 days."
        study_type = ["Animal Models (Rat)"]
        routes = extractor.infer_exposure_method(title, abstract, study_type)
        types = extractor.infer_cannabis_type(title, abstract, study_type, routes)
        self.assertIn("pure cannabinoid", types)


if __name__ == "__main__":
    unittest.main()
