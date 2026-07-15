"""Regression tests for node2a golden guard holdout papers."""

import unittest

import extractor as ex
from maude_classifier import classify_paper


class PatchNode2aGoldenGuardTests(unittest.TestCase):
    """Tests aligned to promoted golden_confirmed papers for node2a.clinical_observational.inhaled."""

    def test_spine_cbd_survey_exposure_oral_sublingual(self):
        """Anonymous spine CBD prevalence surveys infer oral/sublingual routes only."""
        title = (
            "Prevalence of Cannabidiol Use in Patients With Spine Complaints: "
            "Results of an Anonymous Survey"
        )
        exposure = ex.infer_exposure_method(title, "", ["Clinical (observational)"])
        self.assertEqual(exposure, ["oral", "sublingual"])

    def test_spine_cbd_survey_outcomes(self):
        """Spine complaint surveys include pain, sleep, and anxiety outcome domains."""
        title = (
            "Prevalence of Cannabidiol Use in Patients With Spine Complaints: "
            "Results of an Anonymous Survey"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (observational)"])
        self.assertIn("pain", outcomes)
        self.assertIn("sleep", outcomes)
        self.assertIn("anxiety", outcomes)

    def test_longitudinal_medical_cannabis_cognition_study_type(self):
        """Longitudinal observational medical cannabis cognition studies are prospective."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        types = ex.infer_study_type(title, "")
        self.assertEqual(types, ["Clinical (prospective)"])

    def test_longitudinal_medical_cannabis_duration_from_title(self):
        """12-month treatment windows in titles map to ~360 days."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        self.assertEqual(ex.extract_duration_days(title), 360.0)

    def test_longitudinal_medical_cannabis_outcomes(self):
        """Medical cannabis cognition longitudinal studies include anxiety and sleep."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (prospective)"])
        self.assertIn("cognition", outcomes)
        self.assertIn("anxiety", outcomes)
        self.assertIn("sleep", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_itc_smoking_survey_exposure_unknown(self):
        """Population smoking/vaping surveys without administration map exposure to unknown."""
        title = (
            "Associations of Cannabis Use, High-Risk Alcohol Use, and Depressive Symptomology "
            "with Motivation and Attempts to Quit Cigarette Smoking Among Adults: "
            "Findings from the 2020 ITC Four Country Smoking and Vaping Survey"
        )
        exposure = ex.infer_exposure_method(title, "", ["Clinical (observational)"])
        self.assertEqual(exposure, ["unknown"])

"""Regression tests for node2a golden guard holdout papers."""

import unittest

import extractor as ex
from maude_classifier import classify_paper


class PatchNode2aGoldenGuardTests(unittest.TestCase):
    """Tests aligned to promoted golden_confirmed papers for node2a.clinical_observational.inhaled."""

    def test_spine_cbd_survey_exposure_oral_sublingual(self):
        """Anonymous spine CBD prevalence surveys infer oral/sublingual routes only."""
        title = (
            "Prevalence of Cannabidiol Use in Patients With Spine Complaints: "
            "Results of an Anonymous Survey"
        )
        exposure = ex.infer_exposure_method(title, "", ["Clinical (observational)"])
        self.assertEqual(exposure, ["oral", "sublingual"])

    def test_spine_cbd_survey_outcomes(self):
        """Spine complaint surveys include pain, sleep, and anxiety outcome domains."""
        title = (
            "Prevalence of Cannabidiol Use in Patients With Spine Complaints: "
            "Results of an Anonymous Survey"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (observational)"])
        self.assertIn("pain", outcomes)
        self.assertIn("sleep", outcomes)
        self.assertIn("anxiety", outcomes)

    def test_longitudinal_medical_cannabis_cognition_study_type(self):
        """Longitudinal observational medical cannabis cognition studies are prospective."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        types = ex.infer_study_type(title, "")
        self.assertEqual(types, ["Clinical (prospective)"])

    def test_longitudinal_medical_cannabis_duration_from_title(self):
        """12-month treatment windows in titles map to ~360 days."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        self.assertEqual(ex.extract_duration_days(title), 360.0)

    def test_longitudinal_medical_cannabis_outcomes(self):
        """Medical cannabis cognition longitudinal studies include anxiety and sleep."""
        title = (
            "An Observational, Longitudinal Study of Cognition in Medical Cannabis Patients "
            "over the Course of 12 Months of Treatment: Preliminary Results"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (prospective)"])
        self.assertIn("cognition", outcomes)
        self.assertIn("anxiety", outcomes)
        self.assertIn("sleep", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_itc_smoking_survey_exposure_unknown(self):
        """Population smoking/vaping surveys without administration map exposure to unknown."""
        title = (
            "Associations of Cannabis Use, High-Risk Alcohol Use, and Depressive Symptomology "
            "with Motivation and Attempts to Quit Cigarette Smoking Among Adults: "
            "Findings from the 2020 ITC Four Country Smoking and Vaping Survey"
        )
        exposure = ex.infer_exposure_method(title, "", ["Clinical (observational)"])
        self.assertEqual(exposure, ["unknown"])

    def test_amotivational_hypothesis_outcomes(self):
        """Amotivational hypothesis papers map to addiction and cognition outcomes."""
        title = (
            "Acute and chronic effects of cannabinoids on effort-related decision-making "
            "and reward learning: an evaluation of the cannabis 'amotivational' hypotheses"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (RCT)"])
        self.assertIn("addiction", outcomes)
        self.assertIn("cognition", outcomes)
        self.assertNotIn("other", outcomes)

    def test_fibromyalgia_oil_sublingual_exposure(self):
        """THC-rich cannabis oil drop trials with sublingual dosing map to sublingual route."""
        abstract = (
            "The initial dose was one drop of THC-rich cannabis oil a day sublingually. "
            "17 women with fibromyalgia received cannabis oil 24.44 mg/ml of THC and "
            "0.51 mg/ml of cannabidiol for eight weeks."
        )
        title = (
            "Ingestion of a THC-Rich Cannabis Oil in People with Fibromyalgia: "
            "A Randomized, Double-Blind, Placebo-Controlled Clinical Trial"
        )
        exposure = ex.infer_exposure_method(title, abstract, ["Clinical (RCT)"], full_text=abstract)
        self.assertEqual(exposure, ["sublingual"])
        types = ex.infer_cannabis_type(title, abstract, ["Clinical (RCT)"], exposure, full_text=abstract)
        self.assertIn("concentrates", types)
        self.assertNotIn("CB receptor agonist", types)
        self.assertEqual(ex.extract_population_sex(abstract), "female")

    def test_vaporized_cannabis_dried_flower_vape_pen(self):
        """Medical vaporizer flower maps to dried flower (not consumer vape pen)."""
        title = (
            "Cannabidiol (CBD) content in vaporized cannabis does not prevent "
            "tetrahydrocannabinol (THC)-induced impairment of driving and cognition"
        )
        abstract = (
            "Participants inhaled vaporized cannabis using the Mighty Medic vaporizer. "
            "Conditions included THC/CBD equivalent (11% THC, 11% CBD) cannabis."
        )
        types = ex.infer_cannabis_type(
            title, abstract, ["Clinical (RCT)"], ["inhaled"], full_text=abstract,
        )
        self.assertIn("dried flower", types)
        self.assertNotIn("vape pen", types)
        self.assertNotIn("pure cannabinoid", types)

    def test_psychotomimetic_outcomes_anxiety_not_addiction(self):
        """Psychotomimetic symptom studies map to anxiety rather than addiction."""
        title = (
            "Individual and combined effects of acute delta-9-tetrahydrocannabinol and "
            "cannabidiol on psychotomimetic symptoms and memory function"
        )
        outcomes = ex.extract_outcomes(title, "", study_type=["Clinical (RCT)"])
        self.assertIn("cognition", outcomes)
        self.assertIn("anxiety", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_salience_psychosis_outcomes_anxiety_not_neuroprotection(self):
        """Motivational salience psychosis-risk fMRI maps to anxiety not neuroprotection."""
        title = (
            "Cannabidiol attenuates insular dysfunction during motivational salience "
            "processing in subjects at clinical high risk for psychosis"
        )
        abstract = (
            "Double-blind placebo-controlled parallel-arm study of 600 mg oral cannabidiol "
            "in antipsychotic-naive subjects at clinical high risk for psychosis."
        )
        outcomes = ex.extract_outcomes(title, abstract, study_type=["Clinical (RCT)"])
        self.assertIn("cognition", outcomes)
        self.assertIn("anxiety", outcomes)
        self.assertNotIn("neuroprotection", outcomes)

    def test_citation_intravenous_thc_not_injection_route(self):
        """Citation-only intravenous THC mentions must not add injection exposure."""
        abstract = (
            "A randomised crossover design compared inhaled THC and CBD through a vaporiser. "
            "Prior work replicated oral CBD and intravenous THC effects on psychosis."
        )
        title = (
            "Individual and combined effects of acute delta-9-tetrahydrocannabinol and "
            "cannabidiol on psychotomimetic symptoms and memory function"
        )
        exposure = ex.infer_exposure_method(title, abstract, ["Clinical (RCT)"], full_text=abstract)
        self.assertNotIn("injection cannabinoids", exposure)


if __name__ == "__main__":
    unittest.main()
