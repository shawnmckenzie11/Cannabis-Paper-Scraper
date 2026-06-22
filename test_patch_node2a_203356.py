"""Regression tests for node2a holdout node2a_calibration_20260622_203356_002."""

import unittest

import extractor as ex


class PatchNode2a203356Tests(unittest.TestCase):
    """Tests for node2a clinical/biomarker/review extraction fixes."""

    def test_biomarker_study_suppresses_endocannabinoid_strain(self):
        """CSF endocannabinoid biomarker papers do not populate AEA/THC strain labels."""
        text = (
            "Cerebrospinal fluid endocannabinoid levels in Gilles de la Tourette syndrome. "
            "We measured anandamide and 2-AG in CSF samples from patients."
        )
        strain, _ = ex.extract_strain_info(text, study_type=["Clinical (prospective)"])
        self.assertIsNone(strain)

    def test_delta8_product_survey_cannabis_type(self):
        """Delta-8 product availability surveys infer multi-label product types."""
        title = "Delta-8-THC: Delta-9-THC's nicer younger sibling?"
        abstract = (
            "Products containing delta-8-THC became widely available following the 2018 Farm Bill "
            "and hemp processing companies sold edibles and vape pens."
        )
        types = ex.infer_cannabis_type(title, abstract, ["Clinical (observational)"], ["unknown"])
        self.assertIn("pure cannabinoid", types)
        self.assertTrue(set(types) & {"edibles", "vape pen", "concentrates"})

    def test_delta8_product_survey_exposure_unknown(self):
        """Product survey papers do not infer participant exposure routes."""
        title = "Delta-8-THC: Delta-9-THC's nicer younger sibling?"
        abstract = "Products containing delta-8-THC became widely available following the 2018 Farm Bill."
        methods = ex.infer_exposure_method(title, abstract, ["Clinical (observational)"])
        self.assertEqual(methods, ["unknown"])

    def test_cultivar_erez_avidekel_extraction(self):
        """Named medical cannabis cultivars are captured from var. Indica labels."""
        text = (
            "Cannabis sativa var. Indica 'Erez' and Cannabis sativa var. Indica 'Avidekel' "
            "were administered to ulcerative colitis patients."
        )
        strain = ex._extract_priority_cultivar_strain(text)
        self.assertIsNotNone(strain)
        self.assertIn("Erez", strain)
        self.assertIn("Avidekel", strain)

    def test_biomarker_with_cannabis_use_infers_dried_flower(self):
        """Observational CSF studies with cannabis-use cohorts map to dried flower."""
        title = "Cerebrospinal fluid anandamide levels, cannabis use and psychosis"
        abstract = "We assessed cannabis use among participants and measured CSF anandamide."
        types = ex.infer_cannabis_type(title, abstract, ["Clinical (observational)"], [])
        self.assertEqual(types, ["dried flower"])

    def test_delta8_survey_suppresses_strain(self):
        """Delta-8 product surveys do not populate strain_reported catalog noise."""
        title = "Delta-8-THC: Delta-9-THC's nicer younger sibling?"
        abstract = (
            "Products containing delta-8-THC became widely available following the 2018 Farm Bill."
        )
        strain, _ = ex.extract_strain_info(f"{title} {abstract}", study_type=["Clinical (observational)"])
        self.assertIsNone(strain)

    def test_cnr1_association_pure_cannabinoid(self):
        """CNR1 methylation association papers infer pure cannabinoid without strain bleed."""
        title = "Cannabinoid receptor CNR1 expression and DNA methylation in adolescents"
        abstract = (
            "Cannabis use has been identified as an environmental risk factor. "
            "We examined CNR1 DNA methylation in adolescents with cannabis use."
        )
        study = ["Clinical (observational)"]
        strain, _ = ex.extract_strain_info(f"{title} {abstract}", study_type=study)
        self.assertIsNone(strain)
        types = ex.infer_cannabis_type(title, abstract, study, ["unknown"])
        self.assertIn("pure cannabinoid", types)

    def test_ecb_clinical_treatment_suppresses_aea_thc_strain(self):
        """IBD cannabis-treatment trials suppress endocannabinoid biomarker strain stacking."""
        text = (
            "Endocannabinoid levels in ulcerative colitis patients. "
            "Patients were treated by either cannabis or placebo for 8 weeks."
        )
        strain, _ = ex.extract_strain_info(text, study_type=["Clinical (RCT)"])
        self.assertIsNone(strain)

    def test_forensic_toxicology_exposure_unknown(self):
        """Drug-checking toxicology papers map exposure to unknown."""
        title = "Diverse psychotropic substances detected in drug checking samples"
        abstract = "Toxicology screening identified designer cannabinoids including AMB-FUBINACA."
        exposure = ex.infer_exposure_method(title, abstract, ["Clinical (observational)"])
        self.assertEqual(exposure, ["unknown"])

    def test_csf_abstract_only_cannabis_type(self):
        """CSF biomarker papers infer dried flower from cannabis-use cohorts without PDF."""
        from maude_classifier import should_extract_downstream_fields
        title = "Cerebrospinal fluid anandamide levels, cannabis use and psychosis"
        abstract = "CSF anandamide was measured in 33 volunteers including cannabis users."
        self.assertTrue(should_extract_downstream_fields(None, None, title, abstract))

    def test_win55212_strain_normalized(self):
        """WIN55212 variant spellings normalize to WIN 55,212-2."""
        self.assertEqual(ex._normalize_compound_strain_label("WIN55,212-2"), "WIN 55,212-2")
        self.assertEqual(ex._normalize_compound_strain_label("WIN55; THC"), "WIN 55,212-2")

    def test_cnr1_association_exposure_unknown(self):
        """CNR1 methylation association papers do not infer participant exposure routes."""
        title = "Cannabinoid receptor CNR1 expression and DNA methylation in adolescents"
        abstract = (
            "Cannabis use has been identified as an environmental risk factor. "
            "We examined CNR1 DNA methylation in adolescents with cannabis use."
        )
        exposure = ex.infer_exposure_method(title, abstract, ["Clinical (observational)"])
        self.assertEqual(exposure, ["unknown"])

    def test_drug_checking_six_month_duration(self):
        """Drug-checking monitoring windows map six months to 182 days."""
        text = (
            "representing the first six months of DCS implementation. "
            "Analyses were conducted across the monitoring period."
        )
        self.assertEqual(ex.extract_duration_days(text), 182.0)


if __name__ == "__main__":
    unittest.main()
