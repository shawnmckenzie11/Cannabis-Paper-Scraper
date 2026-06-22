"""Tests for node2b RL handoff extraction improvements (shared across node2a/2b/2c)."""

import unittest

import extractor
import maude_classifier


class TestHandoffExtractor(unittest.TestCase):
    """Regression tests from node2b staged handoff acceptance cases."""

    def test_vaporized_plant_matter_not_vape_pen(self):
        """Vapor inhalation with plant matter → dried flower, not vape pen."""
        title = "Vapor exposure in rodents"
        abstract = (
            "METHODS: Mice received vaporized cannabis plant matter in whole-body chambers. "
            "CP-55940 was also tested as a CB receptor agonist."
        )
        study = ["Animal Models (Mouse)"]
        exposure = extractor.infer_exposure_method(title, abstract, study)
        types = extractor.infer_cannabis_type(title, abstract, study, exposure)
        self.assertIn("dried flower", types)
        self.assertNotIn("vape pen", types)
        self.assertIn("CB receptor agonist", types)

    def test_pure_cannabinoid_mg_kg_and_schedule(self):
        """Isolated cannabinoids with mg/kg dosing and daily schedule."""
        abstract = (
            "METHODS: Mice received THC (0.205 mg/kg) and CBD (0.273 mg/kg) from Sigma-Aldrich "
            "daily for 28 days."
        )
        thc, cbd, multi = extractor.extract_thc_cbd_mg_kg(abstract)
        self.assertAlmostEqual(thc, 0.205)
        self.assertAlmostEqual(cbd, 0.273)
        self.assertEqual(extractor.extract_administration_frequency(abstract), "daily")
        self.assertEqual(extractor.extract_duration_days(abstract), 28.0)

    def test_gestational_day_duration(self):
        """Gestational day range computes duration_days as Y minus X."""
        text = "Treatment daily from gestational days 6-20."
        self.assertEqual(extractor.extract_duration_days(text), 14.0)
        self.assertEqual(extractor.extract_administration_frequency(text), "daily")

    def test_coded_cultivars(self):
        """Coded cultivar labels CN2/CN4/CN6 are captured."""
        text = "Cannabinoid content was measured in cultivars CN2, CN4, and CN6."
        reported, _ = extractor.extract_strain_info(text)
        self.assertIsNotNone(reported)
        self.assertIn("CN2", reported)
        self.assertIn("CN4", reported)
        self.assertIn("CN6", reported)

    def test_edible_overrides_injection_context(self):
        """Edible cue near cannabinoid → oral administration, not injection."""
        title = "THC edible in rats"
        abstract = (
            "METHODS: Rats received a THC edible mixed in food daily. "
            "Intraperitoneal saline was used as vehicle control."
        )
        study = ["Animal Models (Rat)"]
        methods = extractor.infer_exposure_method(title, abstract, study)
        self.assertIn("oral administration", methods)

    def test_maude_forwards_mg_kg_fields(self):
        """Maude classify_paper includes mg/kg and schedule fields from heuristics."""
        full_text = (
            "METHODS\n"
            "Mice received THC (1 mg/kg) daily for 10 days via oral gavage."
        )
        result = maude_classifier.classify_paper(
            "Cannabinoid dosing study",
            "METHODS: See full text.",
            full_text=full_text,
        )
        self.assertEqual(result.get("thc_mg_kg"), 1.0)
        self.assertEqual(result.get("duration_days"), 10.0)
        self.assertEqual(result.get("administration_frequency"), "daily")


if __name__ == "__main__":
    unittest.main()
