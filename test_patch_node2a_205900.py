"""Tests for node2a cycle 3/4 patch — outcome_domain, strain_reported, cannabis_type."""

import unittest

import extractor


class PatchNode2a205900Tests(unittest.TestCase):
    """Regression tests from offset-40/50 node2a RL holdout."""

    def test_ulcerative_colitis_title_maps_inflammation(self):
        """IBD titles should tag inflammation outcome domain."""
        title = "Endocannabinoid Levels in Ulcerative Colitis Patients Correlate With Clinical Activity"
        abstract = "Background: Patients with active disease were recruited."
        outcomes = extractor.extract_outcomes(title, abstract)
        self.assertIn("inflammation", outcomes)

    def test_drug_testing_title_maps_addiction(self):
        """Forensic drug-testing papers should tag addiction outcome."""
        title = "Diverse psychotropic substances detected in drug and drug administration samples"
        abstract = "Methods: Toxicology screening identified designer cannabinoids."
        outcomes = extractor.extract_outcomes(title, abstract)
        self.assertIn("addiction", outcomes)

    def test_delta8_review_title_maps_pain_anxiety_cognition(self):
        """Delta-8 review titles should surface multi-domain outcomes."""
        title = "Delta-8-THC: Delta-9-THC's nicer younger sibling?"
        abstract = "This review summarizes pain, anxiety, and cognitive effects."
        outcomes = extractor.extract_outcomes(title, abstract)
        self.assertIn("pain", outcomes)
        self.assertIn("anxiety", outcomes)
        self.assertIn("cognition", outcomes)

    def test_csf_biomarker_study_suppresses_strain_and_cannabis_type(self):
        """CSF endocannabinoid observational studies should not tag THC strain or pure cannabinoid."""
        title = "Cerebrospinal fluid anandamide levels, cannabis use and psychotic-like experiences"
        abstract = (
            "Methods: CSF was collected from participants. Anandamide and 2-AG levels were measured. "
            "Cannabis use was assessed by interview."
        )
        study_type = ["Clinical (observational)"]
        self.assertTrue(extractor._is_endocannabinoid_biomarker_study(abstract, study_type))
        strain, _ = extractor.extract_strain_info(abstract, cannabis_type=["unknown"], study_type=study_type)
        self.assertIsNone(strain)
        cannabis_type = extractor.infer_cannabis_type(
            title, abstract, study_type, ["unknown"],
        )
        self.assertEqual(cannabis_type, ["dried flower"])

    def test_toxicology_detects_amb_fubinaca_over_cultivar(self):
        """Drug-testing papers should report detected synthetic cannabinoid, not unrelated cultivars."""
        text = (
            "Substances detected in forensic toxicology included AMB-FUBINACA and other designer drugs. "
            "Treasure Island (12% THC) was used as a reference cultivar in a separate validation panel."
        )
        strain, _ = extractor.extract_strain_info(text, study_type=["Clinical (observational)"])
        self.assertEqual(strain, "AMB-FUBINACA")

    def test_win55212_normalizes_without_bare_thc(self):
        """Synthetic WIN55,212-2 papers should normalize compound ID without bare THC tag-along."""
        text = "Mice received WIN 55,212-2 (1 mg/kg) and THC levels were measured as a biomarker."
        strain = extractor._extract_compound_provenance_strain(text)
        self.assertEqual(strain, "WIN 55,212-2")

    def test_detected_synthetic_maps_cb_agonist(self):
        """Forensic synthetic cannabinoid detection should map CB receptor agonist."""
        text = "Forensic toxicology identified AMB-FUBINACA in patient samples."
        cannabis_type = extractor.infer_cannabis_type(
            "Substances detected", text, ["Clinical (observational)"], ["unknown"],
        )
        self.assertEqual(cannabis_type, ["CB receptor agonist"])

    def test_tourette_biomarker_prefers_other_over_anxiety(self):
        """Tourette CSF biomarker papers should not inherit anxiety from generic ECS mentions."""
        title = "Cerebrospinal fluid endocannabinoid levels in Gilles de la Tourette syndrome"
        abstract = (
            "Background: The endocannabinoid system modulates anxiety and cognition. "
            "Methods: CSF anandamide was quantified in Tourette patients."
        )
        outcomes = extractor.extract_outcomes(title, abstract, study_type=["Clinical (prospective)"])
        self.assertNotIn("anxiety", outcomes)


if __name__ == "__main__":
    unittest.main()
