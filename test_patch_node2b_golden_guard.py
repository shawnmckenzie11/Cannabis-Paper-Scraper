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

    def test_shelter_dog_cbd_olive_oil_oral_not_injection(self):
        """Oil-dispensed CBD in shelter dogs maps to oral administration, not injection."""
        title = "Cannabis sativa L. may reduce aggressive behaviour towards humans in shelter dogs"
        abstract = (
            "Extra virgin olive oil, titrated to 5% in CBD was given to treated group; "
            "the placebo consisted of olive oil only, dispensed daily for 45 days."
        )
        full_text = (
            f"{title} {abstract} CBD based oil was administered every day before the usual meal. "
            "The oil was mixed with some meat. This effect was not observed with intraperitoneal "
            "administration19,20, but oral instead of intraperitoneal administration was used."
        )
        types = ["Animal Models (Other)"]
        exposure = ex.infer_exposure_method(title, abstract, types, full_text)
        self.assertEqual(exposure, ["oral administration"])
        cannabis_type = ex.infer_cannabis_type(title, abstract, types, exposure, full_text)
        self.assertIn("pure cannabinoid", cannabis_type)

    def test_thc_pharm_gmbh_pure_cannabinoid_and_cb_agonist(self):
        """THC Pharm sourcing and CB1 mechanism yield pure cannabinoid + CB receptor agonist."""
        title = "Striatopallidal cannabinoid type-1 receptors mediate amphetamine-induced sensitization"
        abstract = "CB1 receptors mediate amphetamine sensitization in mice."
        full_text = (
            "Methods details Drugs THC was obtained from THC Pharm GmbH (Frankfurt, Germany). "
            "Used at 10mg/kg, it was dissolved in saline. tetrad behavior induced by THC (10 mg/kg i.p.). "
            "CB1 receptor activation mediates behavioral sensitization."
        )
        types = ["Animal Models (Mouse)"]
        exposure = ["injection cannabinoids"]
        cannabis_type = ex.infer_cannabis_type(title, abstract, types, exposure, full_text)
        self.assertIn("pure cannabinoid", cannabis_type)
        self.assertIn("CB receptor agonist", cannabis_type)

    def test_dual_arm_injection_and_invitro_media(self):
        """Parallel in vivo injection and in vitro cultured cells include both exposure routes."""
        title = (
            "Cannabinoid 2-AG biosynthesis inhibition protects striatal neurons "
            "from malonate-induced death"
        )
        abstract = (
            "Rats received intraperitoneal JZL184 cannabinoid enzyme inhibitor with malonate lesion. "
            "Similar experiments were also conducted in vitro in cultured M-213 cells."
        )
        types = ["Animal Models (Rat)"]
        exposure = ex.infer_exposure_method(title, abstract, types, abstract)
        self.assertIn("injection cannabinoids", exposure)
        self.assertIn("cannabinoids dissolved in media", exposure)
        self.assertNotIn("unknown", exposure)

    def test_slice_bath_application_not_dissolved_in_media(self):
        """FAAH inhibitor bath application in slice electrophysiology is injection, not media."""
        title = "Cannabinoid CB1R upregulation in HIV-1 Tat transgenic mouse inhibitory control"
        abstract = (
            "PF3845, a fatty acid amide hydrolase (FAAH) cannabinoid enzyme inhibitor, was applied via "
            "bath application at 1 µM during acute brain slice electrophysiology recordings."
        )
        types = ["Animal Models (Mouse)"]
        exposure = ex.infer_exposure_method(title, abstract, types, abstract)
        self.assertIn("injection cannabinoids", exposure)
        self.assertNotIn("cannabinoids dissolved in media", exposure)

    def test_faah_inhibitor_cb_receptor_agonist_not_pure_cannabinoid(self):
        """FAAH inhibitor PF3845 maps to CB receptor agonist without pure cannabinoid."""
        title = "Cannabinoid CB1R deficits in HIV-1 Tat transgenic mouse model"
        abstract = (
            "PF3845, a fatty acid amide hydrolase (FAAH) inhibitor, was used in slice electrophysiology."
        )
        types = ["Animal Models (Mouse)"]
        exposure = ["injection cannabinoids"]
        cannabis_type = ex.infer_cannabis_type(title, abstract, types, exposure, abstract)
        self.assertIn("CB receptor agonist", cannabis_type)
        self.assertNotIn("pure cannabinoid", cannabis_type)

    def test_magl_inhibitor_dual_cannabis_type_with_2ag(self):
        """MAGL inhibitor papers with administered 2-AG may retain both pure cannabinoid and CB receptor agonist."""
        title = "Cannabinoid 2-AG biosynthesis inhibition protects striatal neurons"
        abstract = (
            "JZL184, a monoacylglycerol lipase (MAGL) inhibitor, was administered and "
            "cells were incubated with 2-AG in vitro."
        )
        types = ["Animal Models (Rat)"]
        exposure = ["injection cannabinoids", "cannabinoids dissolved in media"]
        cannabis_type = ex.infer_cannabis_type(title, abstract, types, exposure, abstract)
        self.assertIn("CB receptor agonist", cannabis_type)


if __name__ == "__main__":
    unittest.main()
