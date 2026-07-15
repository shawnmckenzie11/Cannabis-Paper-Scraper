"""Regression tests for clinical study population fields (age, sex, inclusion/exclusion criteria) in node2a."""

import unittest
import extractor as ex

class PatchNode2aStudyPopTests(unittest.TestCase):
    """Tests for clinical study population parameter extraction in extractor.py."""

    def test_extract_population_age_pediatric(self):
        """Children/adolescents and age < 18 are mapped to pediatric."""
        text = "A clinical trial enrolled children aged 5 to 12 years with epilepsy."
        self.assertEqual(ex.extract_population_age(text), "pediatric")

        text_under18 = "Participants under 18 years were recruited for the youth cohort."
        self.assertEqual(ex.extract_population_age(text_under18), "pediatric")

    def test_extract_population_age_geriatric(self):
        """Elderly, geriatric, and aged >= 65 are mapped to geriatric."""
        text = "We studied the effect of CBD oil on geriatric patients suffering from dementia."
        self.assertEqual(ex.extract_population_age(text), "geriatric")

        text_older = "Older adults aged >= 65 years were included in the cohort."
        self.assertEqual(ex.extract_population_age(text_older), "geriatric")

    def test_extract_population_age_both(self):
        """Co-occurrence of pediatric and geriatric indicators maps to both."""
        text = "The study population included both children and elderly participants."
        self.assertEqual(ex.extract_population_age(text), "both")

    def test_extract_population_age_adult(self):
        """Explicit adult age bands map to adult; silent clinical text stays unset."""
        text = "Patients aged 18 to 60 years were included in this trial."
        self.assertEqual(ex.extract_population_age(text), "adult")

        text_exclusion = (
            "Adult patients with chronic pain were studied. "
            "Individuals under 18 years were excluded."
        )
        self.assertEqual(ex.extract_population_age(text_exclusion), "adult")

        text_silent = "Patients with chronic pain were studied in this cohort."
        self.assertIsNone(ex.extract_population_age(text_silent))

    def test_extract_population_sex_male(self):
        """Exclusively male cohorts are mapped to male."""
        text = "Subjects consisted of 25 males who underwent cannabis administration."
        self.assertEqual(ex.extract_population_sex(text), "male")

    def test_extract_population_sex_female(self):
        """Exclusively female cohorts are mapped to female."""
        text = "Only female patients aged 18-40 were enrolled in the study."
        self.assertEqual(ex.extract_population_sex(text), "female")

    def test_extract_population_sex_both(self):
        """Mixed sex cohorts are mapped to both; silent text stays unset."""
        text = "We enrolled both men and women in the study."
        self.assertEqual(ex.extract_population_sex(text), "both")

        text_nums = "The study population consisted of 15 males and 18 females."
        self.assertEqual(ex.extract_population_sex(text_nums), "both")

        text_silent = "Patients with chronic pain were studied in this cohort."
        self.assertIsNone(ex.extract_population_sex(text_silent))

    def test_extract_inclusion_criteria(self):
        """Inclusion criteria is extracted from strong eligibility triggers only."""
        text = "Inclusion criteria were chronic neuropathic pain for more than 6 months."
        self.assertEqual(ex.extract_inclusion_criteria(text), "Chronic neuropathic pain for more than 6 months")

        text_passive = "Patients with severe treatment-resistant epilepsy were included in the trial."
        self.assertIsNone(ex.extract_inclusion_criteria(text_passive))

    def test_extract_exclusion_criteria(self):
        """Exclusion criteria is extracted or defaults to None."""
        text = "Exclusion criteria included pregnancy, history of psychosis, or cardiac disease."
        self.assertEqual(ex.extract_exclusion_criteria(text), "Pregnancy, history of psychosis, or cardiac disease")

        text_none = "No subjects were excluded during the trial."
        self.assertIsNone(ex.extract_exclusion_criteria(text_none))

    def test_all_heuristics_integration(self):
        """extract_all_heuristics correctly returns the study population fields for clinical papers."""
        title = "Efficacy of CBD on anxiety in adults"
        abstract = (
            "Adult patients aged 18 to 65 years. Inclusion criteria: generalized anxiety disorder. "
            "Exclusion criteria: pregnancy. We enrolled 40 men and women."
        )
        
        # We override study_type to Clinical (RCT) to trigger the clinical flag
        result = ex.extract_all_heuristics(title, abstract, study_type_override=["Clinical (RCT)"])
        
        self.assertEqual(result.get("population_age"), "adult")
        self.assertEqual(result.get("population_sex"), "both")
        self.assertEqual(result.get("inclusion_criteria"), "Generalized anxiety disorder")
        self.assertEqual(result.get("exclusion_criteria"), "Pregnancy")

if __name__ == "__main__":
    unittest.main()
