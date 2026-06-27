"""Regression tests for node2b golden guard holdout papers."""

import unittest

import extractor as ex


class PatchNode2bGoldenGuardTests(unittest.TestCase):
    """Tests aligned to promoted golden_confirmed papers for node2b injection endpoints."""

    def test_fetal_cbd_sunflower_oil_oral_only(self):
        """Oil-vehicle oral gavage should not add injection or vapor routes."""
        title = (
            "Fetal cannabidiol (CBD) exposure alters thermal pain sensitivity, "
            "problem-solving, and prefrontal cortex excitability"
        )
        abstract = (
            "We administered 50 mg/kg CBD in sunflower oil via oral gavage daily "
            "from embryonic day 5 through birth to pregnant C57BL6J mice."
        )
        exposure = ex.infer_exposure_method(title, abstract, ["Animal Models (Mouse)"])
        self.assertEqual(exposure, ["oral administration"])

    def test_fetal_cbd_outcomes_cognition_not_addiction(self):
        """Problem-solving / PFC papers map to cognition and neuroprotection."""
        title = (
            "Fetal cannabidiol (CBD) exposure alters thermal pain sensitivity, "
            "problem-solving, and prefrontal cortex excitability"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Animal Models (Mouse)"])
        self.assertIn("pain", outcomes)
        self.assertIn("cognition", outcomes)
        self.assertIn("neuroprotection", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_pecs101_chemotherapy_injection_and_media(self):
        """CIPN papers with i.p. injection and cell culture include injection + dissolved in media."""
        title = "The Cannabidiol Analog PECS-101 Prevents Chemotherapy-Induced Neuropathic Pain via PPARγ Receptors"
        abstract = (
            "Mice received intraperitoneal injection of PECS-101 with paclitaxel chemotherapy. "
            "Primary cortical neurons were cultured in vitro with dissolved cannabinoid treatment."
        )
        types = ex.infer_study_type(title, abstract)
        self.assertIn("Animal Models (Mouse)", types)
        self.assertFalse(any(t.startswith("Clinical") for t in types))
        exposure = ex.infer_exposure_method(title, abstract, types)
        self.assertIn("injection cannabinoids", exposure)
        self.assertIn("cannabinoids dissolved in media", exposure)

    def test_antagonist_compound_names(self):
        """Named antagonists append CB receptor antagonist to cannabis_type."""
        title = "Peripheral CB1 antagonist JD5037 with AM630 in mice"
        abstract = "Mice received intraperitoneal AM630, a CB receptor antagonist, with cannabidiol treatment."
        types = ex.infer_cannabis_type(title, abstract, ["Animal Models (Mouse)"], ["injection cannabinoids"])
        self.assertIn("CB receptor antagonist", types)

    def test_zebrafish_gastrulation_oral_route(self):
        """Zebrafish waterborne THC/CBD maps to oral administration, not injection."""
        title = "Motor neuron development in zebrafish is altered by brief (5-hr) exposures to THC"
        abstract = (
            "Zebrafish embryos were exposed to THC added directly into the water in the well "
            "during gastrulation for 5 hours."
        )
        types = ex.infer_study_type(title, abstract)
        exposure = ex.infer_exposure_method(title, abstract, types)
        self.assertIn("oral administration", exposure)
        self.assertNotIn("injection cannabinoids", exposure)

    def test_vapor_inhalation_whole_body_not_injection(self):
        """Adolescent vapor inhalation primary protocol uses whole-body smoke/vapor."""
        title = "Adult consequences of repeated nicotine and THC vapor inhalation in adolescent rats"
        abstract = (
            "Rats were exposed to 30-min sessions of vapor inhalation, twice daily, "
            "from post-natal day (PND) 31 to PND 40 with THC vapor."
        )
        types = ["Animal Models (Rat)"]
        exposure = ex.infer_exposure_method(title, abstract, types, abstract)
        self.assertIn("whole body. smoke/vapor", exposure)
        self.assertNotIn("injection cannabinoids", exposure)

    def test_abn_cbd_oral_not_injection(self):
        """Abn-CBD oral administration should not retain spurious injection route."""
        title = "Antidiabetic actions of GPR55 agonist Abn-CBD in obese-diabetic mice"
        abstract = "Once daily oral administration of Abn-CBD (0.1µmol/kg bodyweight) for 21 days in mice."
        types = ["Animal Models (Mouse)"]
        exposure = ex.infer_exposure_method(title, abstract, types, abstract)
        self.assertEqual(exposure, ["oral administration"])

    def test_reverse_mg_kg_cbd_extraction(self):
        """50 mg/kg CBD before compound name is extracted."""
        text = "We administered 50 mg/kg CBD in sunflower oil via oral gavage daily."
        _, cbd, _ = ex.extract_thc_cbd_mg_kg(text)
        self.assertEqual(cbd, 50.0)


if __name__ == "__main__":
    unittest.main()
