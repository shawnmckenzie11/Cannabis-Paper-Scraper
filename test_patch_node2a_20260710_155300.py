"""Regression tests for node2a holdout node2a_calibration_20260710_195101_194 patch."""

import unittest

import extractor as ex


class PatchNode2a20260710Tests(unittest.TestCase):
    """Holdout-derived guards for demographics, product type, outcomes, and exposure."""

    def test_population_age_no_silent_adult_default(self):
        """Clinical text without an explicit age band leaves population_age unset."""
        text = "Patients with chronic pain were studied in this observational cohort."
        self.assertIsNone(ex.extract_population_age(text))

    def test_population_sex_no_silent_both_default(self):
        """Clinical text without gendered cohort language leaves population_sex unset."""
        text = "Patients with chronic pain were studied in this observational cohort."
        self.assertIsNone(ex.extract_population_sex(text))

    def test_digital_mental_health_app_exposure_unknown(self):
        """Mobile mental-health apps that only monitor cannabis use stay exposure unknown."""
        title = "A mobile phone application for the assessment and management of youth mental health"
        abstract = (
            "Patients aged 14 to 24 years used a mobiletype program monitoring mood, "
            "alcohol and cannabis use. Cigarette smoking was recorded on follow-up surveys."
        )
        exposure = ex.infer_exposure_method(title, abstract, ["Clinical (RCT)"])
        self.assertEqual(exposure, ["unknown"])
        types = ex.infer_cannabis_type(title, abstract, ["Clinical (RCT)"], ["unknown"])
        self.assertEqual(types, ["unknown"])

    def test_digital_mental_health_outcome_anxiety_not_addiction(self):
        """App mental-health trials map to anxiety without substance-use addiction bleed."""
        title = "A mobile phone application for the assessment and management of youth mental health"
        abstract = "Substance use questionnaires and cannabis use modules were completed daily."
        outcomes = ex.extract_outcomes(title, abstract, study_type=["Clinical (RCT)"])
        self.assertIn("anxiety", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_vaporized_flower_cbd_thc_not_vape_pen(self):
        """Volcano vaporised cannabis with CBD:THC ratios is dried flower + pure cannabinoid."""
        title = "Does cannabidiol make cannabis safer?"
        abstract = (
            "Participants inhaled vaporised cannabis containing 10 mg THC and CBD. "
            "Bedrocan (22.6% THC, 0.1% CBD) and Bedrolite (7.5% CBD, 0.3% THC) were used. "
            "Cannabis was administered using a Volcano Medic Vaporiser."
        )
        types = ex.infer_cannabis_type(
            title, abstract, ["Clinical (RCT)"], ["inhaled"], full_text=abstract,
        )
        self.assertIn("dried flower", types)
        self.assertIn("pure cannabinoid", types)
        self.assertNotIn("vape pen", types)

    def test_iv_cannula_not_injection_cannabinoids(self):
        """Blood-draw IV cannula before vaporised cannabis is not parenteral exposure."""
        title = "Does cannabidiol make cannabis safer?"
        abstract = (
            "An intravenous cannula was inserted before participants were administered "
            "vaporised cannabis containing 10 mg THC. Prior studies reported intravenous THC."
        )
        exposure = ex.infer_exposure_method(
            title, abstract, ["Clinical (RCT)"], full_text=abstract,
        )
        self.assertIn("inhaled", exposure)
        self.assertNotIn("injection cannabinoids", exposure)

    def test_cbd_pct_prefers_bedrolite_over_thc_label(self):
        """CBD% prefers Bedrolite 7.5% CBD and ignores 0.3% THC / prevalence percents."""
        text = (
            "Bedrocan (22.6% THC, 0.1% CBD), Bedrolite (7.5% CBD, 0.3% THC) were used. "
            "Some participants consumed concentrates (23%; hash, wax)."
        )
        self.assertEqual(ex.extract_cbd_pct(text), 7.5)

    def test_social_exclusion_biomarker_outcomes(self):
        """Plasma endocannabinoid social-exclusion papers map addiction + anxiety."""
        title = "Endocannabinoids and related lipids linked to social exclusion in individuals with opioid use"
        abstract = (
            "We investigated basal plasma levels of the endocannabinoids anandamide (AEA) "
            "and 2-AG linked to social exclusion. URB597 is discussed in prior work."
        )
        study = ["Clinical (observational)"]
        outcomes = ex.extract_outcomes(title, abstract, study_type=study)
        self.assertIn("addiction", outcomes)
        self.assertIn("anxiety", outcomes)
        types = ex.infer_cannabis_type(title, abstract, study, ["unknown"], full_text=abstract)
        self.assertEqual(types, ["unknown"])

    def test_cb1_pet_obesity_unknown_cannabis_type(self):
        """CB1R PET obesity-risk imaging without product dosing stays cannabis_type unknown."""
        title = "Obesity risk is associated with altered cerebral glucose metabolism"
        abstract = (
            "CB1 receptor availability was measured with [18F]FMPEP-d2 PET imaging. "
            "Endocannabinoids influence feeding through CB1 receptors."
        )
        types = ex.infer_cannabis_type(
            title, abstract, ["Clinical (observational)"], ["unknown"], full_text=abstract,
        )
        self.assertEqual(types, ["unknown"])

    def test_cnr1_outcomes_addiction_cognition(self):
        """CNR1 methylation association papers prefer addiction + cognition over neuroprotection."""
        title = "Cannabinoid receptor CNR1 expression and DNA methylation in human prefrontal cortex"
        abstract = "Cannabis use has been identified as an environmental risk factor for psychosis."
        outcomes = ex.extract_outcomes(
            title, abstract, study_type=["Clinical (observational)"],
        )
        self.assertIn("addiction", outcomes)
        self.assertIn("cognition", outcomes)
        self.assertNotIn("neuroprotection", outcomes)

    def test_review_synthesis_exposure_injection_and_media(self):
        """Two-poles ECS review with animal + cell-culture arms maps injection + dissolved media."""
        title = "Schizophrenia and depression, two poles of endocannabinoid system deregulation"
        abstract = (
            "Using postmortem prefrontal cortex samples from subjects with schizophrenia. "
            "Primary cortical cell culture neuron-enriched mouse cultures were prepared. "
            "Animals were sacrificed 3 h after the last injection."
        )
        study = [
            "Clinical (observational)",
            "Animal Models (Mouse)",
            "Cell Culture (Primary Cells)",
        ]
        exposure = ex.infer_exposure_method(title, abstract, study, full_text=abstract)
        self.assertIn("injection cannabinoids", exposure)
        self.assertIn("cannabinoids dissolved in media", exposure)
        outcomes = ex.extract_outcomes(title, abstract, study_type=study)
        self.assertIn("neuroprotection", outcomes)
        self.assertIn("other", outcomes)
        self.assertNotIn("cognition", outcomes)

    def test_exclusion_none_string_becomes_null(self):
        """Bare 'no exclusion criteria' maps to None rather than the string 'None'."""
        text = "No exclusion criteria were applied in this survey."
        self.assertIsNone(ex.extract_exclusion_criteria(text))

    def test_inclusion_requires_strong_cue(self):
        """Loose 'participants in' prose no longer yields inclusion_criteria free text."""
        text = "Participants in the high-risk group were male sex, age of 20-35 years."
        self.assertIsNone(ex.extract_inclusion_criteria(text))


if __name__ == "__main__":
    unittest.main()
