"""Tests for node2c targeted handoff on treatment_duration + strain_reported."""

import unittest

import extractor


class Node2cTargetedHandoffTests(unittest.TestCase):
    """Holdout-derived cases for node2c diverging characteristic fields."""

    def test_treatment_duration_incubated_24h_beats_prep(self):
        """24 h incubation beats shorter centrifugation or decarboxylation steps."""
        text = (
            "Flowers were maintained at 120 °C for 1 h. "
            "Cells were incubated for 24 h with either DMSO or Cannabis extract."
        )
        self.assertEqual(extractor.extract_treatment_duration(text), "24 hours")

    def test_treatment_duration_md_simulated_during(self):
        """MD runtimes capture 'simulated during N ns' phrasing."""
        text = "The best docked ligands was simulated during 100 ns, under an aqueous environment, using GROMACS."
        self.assertEqual(extractor.extract_treatment_duration(text), "100 ns")

    def test_treatment_duration_range_hours_to_days(self):
        """Hour-to-day ranges are preserved as a single label."""
        text = "Samples were stabilized for 24 hours to 6 days in duplicate cultures."
        self.assertEqual(extractor.extract_treatment_duration(text), "24 hours to 6 days")

    def test_strain_suppresses_ethics_wistar_in_invitro(self):
        """Animal strains mentioned only in ethics sections are omitted for in-vitro papers."""
        text = (
            "Ethics: Wistar rats were approved under IRB REC.1400.1122. "
            "Cells were incubated in vitro for 24 h with THC dissolved in media."
        )
        study_type = ["Cell Culture (Other In Vitro)"]
        reported, _ = extractor.extract_strain_info(text, study_type=study_type)
        self.assertIsNone(reported)

    def test_strain_cultivar_code_panel(self):
        """Extended cultivar codes and cannabinoid abbreviations form a panel."""
        text = "Synthetic CBD, CBDV and 331-18A were purchased from Cannasoul Analytics."
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Other In Vitro)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("331-18A", reported)
        self.assertIn("CBDV", reported)

    def test_strain_cherry_wine_cv(self):
        """Quoted cv. names are captured instead of cultivar-noise fragments."""
        text = "A CBD-rich strain, Cannabis sativa cv. 'Cherry Wine' (CW) seeds were purchased."
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Other In Vitro)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("Cherry Wine", reported)

    def test_strain_chemotype_profiles(self):
        """Multi-chemotype percentage profiles are returned as one label."""
        text = (
            "Chemotype I (CI) – a THC rich strain (~ 12% THC), "
            "(ii) Chemotype II (CII) – a THC/CBD leveled strain (~ 5% THC and ~ 7% CBD) and "
            "(iii) Chemotype III (CIII) – a CBD rich strain (~ 11% CBD)."
        )
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Other In Vitro)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("Chemotype I", reported)
        self.assertIn("Chemotype III", reported)

    def test_strain_botanical_source(self):
        """Non-cannabis botanical sources are captured when named as cannabinoid sources."""
        text = "These methods underscore the potential of Trema micranthum as an alternative source for cannabinoids."
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Other In Vitro)"],
        )
        self.assertEqual(reported, "Trema micranthum")

    def test_strain_compound_panel(self):
        """Synthetic test-article panels normalize compound IDs."""
        text = "Cells were treated with CP-55,940, WIN 55,212-2, AEA, and SR141716."
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Other In Vitro)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("CP-55,940", reported)
        self.assertIn("WIN 55,212-2", reported)


if __name__ == "__main__":
    unittest.main()
