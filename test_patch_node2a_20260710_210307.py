"""Regression tests for golden cycle node2a.clinical_observational.inhaled_20260710_205138."""

import unittest

import extractor as ex


class PatchNode2aGoldenObsInhaled210307Tests(unittest.TestCase):
    """Staged-patch regressions for demographics, epi route, and outcome cues."""

    def test_healthy_volunteers_map_to_adult(self):
        """Healthy volunteers / aged-18+ cohorts map population_age to adult."""
        text = (
            "Methods: 17 healthy volunteers experienced with cannabis but not regular users. "
            "Participants aged 18 to 45 years completed the protocol."
        )
        self.assertEqual(ex.extract_population_age(text), "adult")

    def test_percent_fragment_not_pediatric(self):
        """Numeric prevalence fragments like 13.2% must not trigger pediatric age."""
        text = (
            "Trauma-exposed civilians presented to an emergency department. "
            "PSE-tobacco in 13.2%, and PSE-marijuana in 5.6% of the adult sample. "
            "Participants provided self-report data on past 30-day cannabis use."
        )
        self.assertEqual(ex.extract_population_age(text), "adult")

    def test_youth_org_name_not_pediatric_when_adult_band(self):
        """Org names containing 'youth' do not override an explicit adult age band."""
        text = (
            "All participants were 19 to 55 years of age. Cannabis history was collected "
            "using the Substance Use History tool (Orygen Youth Health Research Centre)."
        )
        self.assertEqual(ex.extract_population_age(text), "adult")

    def test_twelve_healthy_men_is_male(self):
        """Single-sex 'twelve healthy men' cohorts map to male, not both."""
        text = (
            "Methods Twelve healthy men aged 18–45 years who identified as chronic and "
            "heavy users of inhaled cannabis were enrolled. References discuss men and women."
        )
        self.assertEqual(ex.extract_population_sex(text), "male")

    def test_epi_self_report_exposure_unknown(self):
        """Past-30-day / CUD epi studies without route stay exposure unknown."""
        title = (
            "Associations of alcohol and cannabis use with change in posttraumatic stress "
            "disorder and depression symptoms"
        )
        abstract = (
            "Recently trauma-exposed civilians presenting to an emergency department provided "
            "self-report data on past 30-day alcohol and cannabis use and PTSD symptoms."
        )
        exposure = ex.infer_exposure_method(
            title, abstract, ["Clinical (observational)"], full_text=abstract,
        )
        self.assertEqual(exposure, ["unknown"])
        types = ex.infer_cannabis_type(
            title, abstract, ["Clinical (observational)"], exposure, full_text=abstract,
        )
        self.assertEqual(types, ["unknown"])

    def test_prenatal_pse_exposure_unknown(self):
        """Prenatal substance exposure cohorts keep route/product unknown."""
        title = "Prenatal substance exposure and child health"
        abstract = (
            "Using data from the adolescent brain cognitive development cohort, we tested "
            "associations of PSE-marijuana with sleep and mental health problems in children."
        )
        exposure = ex.infer_exposure_method(
            title, abstract, ["Clinical (observational)"], full_text=abstract,
        )
        self.assertEqual(exposure, ["unknown"])

    def test_chronic_inhaled_users_dried_flower(self):
        """Chronic users of inhaled cannabis map cannabis_type to dried flower."""
        title = (
            "Delta-9 THC can be detected and quantified in the semen of men who are "
            "chronic users of inhaled cannabis"
        )
        abstract = (
            "Twelve healthy men aged 18–45 years who identified as chronic and heavy users "
            "of inhaled cannabis provided semen samples for THC quantification."
        )
        types = ex.infer_cannabis_type(
            title, abstract, ["Clinical (observational)"], ["inhaled"], full_text=abstract,
        )
        self.assertEqual(types, ["dried flower"])

    def test_sleep_disorder_outcome(self):
        """Sleep disorder indication lists map to the sleep outcome domain."""
        title = (
            "A Semi-Naturalistic, Open-Label Trial Examining the Effect of Prescribed "
            "Medical Cannabis on Neurocognitive Performance"
        )
        abstract = (
            "Patients prescribed medical cannabis attended a laboratory session. "
            "Chronic non-cancer pain was common, followed by sleep disorder and anxiety."
        )
        outcomes = ex.extract_outcomes(
            title, abstract, full_text=abstract, study_type=["Clinical (observational)"],
        )
        self.assertIn("sleep", outcomes)
        self.assertIn("anxiety", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_prosociality_outcome_other(self):
        """Cannabis consumption and prosociality surveys map to other, not neuroprotection."""
        title = "Cannabis consumption and prosociality"
        abstract = (
            "Healthy young adults aged 18-25 years with varying detectable levels of THC "
            "in urine completed prosociality questionnaires."
        )
        outcomes = ex.extract_outcomes(
            title, abstract, full_text=abstract, study_type=["Clinical (observational)"],
        )
        self.assertEqual(outcomes, ["other"])


if __name__ == "__main__":
    unittest.main()
