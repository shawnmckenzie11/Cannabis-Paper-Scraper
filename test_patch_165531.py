"""Tests for staged patch node2c_20260622_165531 implementation."""

import unittest

import extractor
import maude_classifier


class Patch165531Tests(unittest.TestCase):
    """Regression tests from the first node2c RL feedback handoff."""

    def test_treatment_duration_incubation_hours(self):
        """In-vitro incubation durations are captured."""
        text = "Cells were incubated for 24 h with THC."
        self.assertEqual(extractor.extract_treatment_duration(text), "24 hours")

    def test_treatment_duration_md_simulation(self):
        """Molecular dynamics simulation runtimes map to treatment_duration."""
        text = "A 100 ns molecular dynamics simulation was performed."
        self.assertEqual(extractor.extract_treatment_duration(text), "100 ns")

    def test_treatment_duration_prefers_incubation_window(self):
        """When multiple durations appear, scored incubation windows beat prep noise."""
        text = "Cells maintained for 5 days then incubated for 6 h before assay."
        self.assertEqual(extractor.extract_treatment_duration(text), "6 hours")
        text2 = "Centrifuged for 3 min. Cells were incubated for 24 h with THC."
        self.assertEqual(extractor.extract_treatment_duration(text2), "24 hours")

    def test_strain_vendor_blocked(self):
        """Vendor catalog strings are not returned as strain_reported."""
        text = "THC (Sigma-Aldrich, catalog no. T4764) was applied to cells."
        reported, _ = extractor.extract_strain_info(text)
        if reported:
            self.assertNotIn("Sigma", reported)

    def test_strain_cultivar_priority(self):
        """Cultivar abbreviations beat vendor strings."""
        text = "Cannabis sativa cv. Skunk #1 obtained from Sigma-Aldrich."
        reported, _ = extractor.extract_strain_info(text)
        self.assertIsNotNone(reported)
        self.assertIn("Skunk", reported)
        self.assertNotIn("Sigma", reported)

    def test_thc_pct_acid_context_rejected(self):
        """Acid-fraction ratios are not treated as plant THC percent."""
        text = "THCA/total ratio was 68.3% in the sample."
        self.assertIsNone(extractor.extract_thc_pct(text))

    def test_thc_pct_valid_plant_content(self):
        """Plant-level THC percentages within range are accepted."""
        text = "THC content was 18.5% w/w in the dried flower."
        self.assertAlmostEqual(extractor.extract_thc_pct(text), 18.5)

    def test_outcome_domain_glioblastoma(self):
        """Oncology cell-line terms map to oncology outcome domain."""
        title = "Cannabinoid cytotoxicity"
        abstract = "We studied a glioblastoma cell line for apoptosis."
        outcomes = extractor.extract_outcomes(title, abstract)
        self.assertIn("oncology", outcomes)

    def test_outcome_domain_girk(self):
        """GIRK channel studies map to pain outcome domain."""
        title = "Cannabinoid receptor study"
        abstract = "GIRK channel activation was measured in neurons."
        outcomes = extractor.extract_outcomes(title, abstract)
        self.assertIn("pain", outcomes)

    def test_cannabis_type_pure_isolate(self):
        """Isolated cannabinoid dissolved in solvent maps to pure cannabinoid."""
        title = "CBD in culture"
        abstract = "METHODS: CBD was dissolved in DMSO and applied to cells."
        study = ["Cell Culture (Other In Vitro)"]
        exposure = extractor.infer_exposure_method(title, abstract, study)
        types = extractor.infer_cannabis_type(title, abstract, study, exposure)
        self.assertIn("pure cannabinoid", types)

    def test_study_type_hplc_paper(self):
        """Analytical chemistry papers route to in-vitro study type, not clinical."""
        title = "UHPLC quantification of cannabinoids"
        abstract = "METHODS: UHPLC-MS was used to quantify THC and CBD in extracts."
        study_type = extractor.infer_study_type(title, abstract)
        self.assertIn("Cell Culture (Other In Vitro)", study_type)
        self.assertNotIn("Clinical (observational)", study_type)

    def test_study_type_molecular_docking(self):
        """Computational docking papers stay in vitro."""
        abstract = "AutoDock Vina docking scores were calculated for THC analogs."
        study_type = extractor.infer_study_type("Docking study", abstract)
        self.assertIn("Cell Culture (Other In Vitro)", study_type)


if __name__ == "__main__":
    unittest.main()
