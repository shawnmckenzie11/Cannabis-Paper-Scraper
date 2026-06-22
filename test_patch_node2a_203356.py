"""Tests for node2a patch from batch node2a_calibration_20260622_203356_002."""

import unittest

import extractor
import maude_classifier


class PatchNode2a203356Tests(unittest.TestCase):
    """Regression tests from the offset-40 node2a RL holdout."""

    def test_sample_size_prefers_completed_survey_cohort(self):
        """Completed survey participants beat total sign-ups."""
        text = (
            "While 654 participants have signed up for the survey, 321 of them have completed all "
            "of the assessments. Participants (n=321) completed a set of online surveys."
        )
        self.assertEqual(extractor.extract_sample_size(text), 321)

    def test_sample_size_prefers_final_sample_over_partial_scans(self):
        """Final analysis sample beats partial imaging completion counts."""
        text = (
            "Due to scheduling problems and technical issues, 36 subjects completed the scan. "
            "The final sample (n=41) consisted of 19 HR individuals and 22 LR individuals."
        )
        self.assertEqual(extractor.extract_sample_size(text), 41)

    def test_sample_size_prefers_total_postmortem_samples(self):
        """Total post-mortem sample counts beat regional disease-subset cohorts."""
        text = (
            "In total, 703 DLPFC, 452 hippocampus, and 468 caudate postmortem brain samples were used. "
            "The DLPFC cohort consists of 175 subjects with schizophrenia."
        )
        self.assertEqual(extractor.extract_sample_size(text), 703)

    def test_sample_size_prefers_reported_on_final_cohort(self):
        """Final analyzed group totals beat inclusion-criteria arm descriptions."""
        text = (
            "30 Subjects included in the HC group did not score on the nicotine addiction questionnaire. "
            "Results are therefore reported on 11 HC and 10 NAD."
        )
        self.assertEqual(extractor.extract_sample_size(text), 21)

    def test_sample_size_prefers_total_volunteers_over_use_subgroups(self):
        """Total volunteer counts beat heavy/light use subgroup sizes."""
        text = (
            "Individuals who used cannabis more than 10 times in a month were assigned to the "
            "heavy use condition (n=10), whereas light users were n=10. "
            "Endocannabinoids were tested in 33 volunteers (20 cannabis users)."
        )
        self.assertEqual(extractor.extract_sample_size(text), 33)

    def test_sample_size_uses_first_split_disease_cohort(self):
        """Split disease-group participation sentences prefer the first cohort count."""
        text = (
            "Nineteen patients with UC and 30 patients with CD participated in the study."
        )
        self.assertEqual(extractor.extract_sample_size(text), 19)

    def test_frequency_null_for_user_quote_daily_use(self):
        """Survey quote text about daily use is not an administration schedule."""
        text = 'One participant wrote: "I love Delta 8 because I do not need to take it daily."'
        study_type = ["Clinical (observational)"]
        self.assertIsNone(
            extractor.extract_administration_frequency(text, study_type=study_type)
        )

    def test_frequency_null_for_neuroleptic_daily_dose(self):
        """Psychiatric medication daily dosing is not cannabis administration_frequency."""
        text = "Estimated lifetime neuroleptic exposure, average daily neuroleptic dose, and final dose were recorded."
        study_type = ["Clinical (observational)"]
        self.assertIsNone(
            extractor.extract_administration_frequency(text, study_type=study_type)
        )

    def test_frequency_null_for_background_starting_dose(self):
        """Background protocol descriptions without an active cohort do not set frequency."""
        text = (
            "In a prior trial the starting dose was one drop twice daily before meals, "
            "gradually raised until the patient felt a satisfactory effect."
        )
        study_type = ["Clinical (observational)"]
        self.assertIsNone(
            extractor.extract_administration_frequency(text, study_type=study_type)
        )

    def test_frequency_multiple_doses_per_session_from_scan_protocol(self):
        """Repeated in-session THC doses map to multiple doses per session."""
        text = (
            "During the experiment, upload dosages of 1 mg were used, 30 min apart, "
            "in between scan sessions of different paradigms."
        )
        study_type = ["Clinical (RCT)"]
        self.assertEqual(
            extractor.extract_administration_frequency(text, study_type=study_type),
            "multiple doses per session",
        )

    def test_biomarker_study_clears_administration_frequency(self):
        """Endocannabinoid biomarker observational papers do not emit dosing schedules."""
        title = "Endocannabinoid Levels in Ulcerative Colitis Patients Correlate With Clinical Parameters"
        abstract = "Plasma anandamide and 2-AG were quantified in IBD patients."
        methods = (
            "Nineteen patients with UC and 30 patients with CD participated in the study. "
            "In a prior trial the starting dose was one drop twice daily before meals."
        )
        result = maude_classifier.classify_paper(title, abstract, full_text=methods)
        self.assertIsNone(result.get("administration_frequency"))


if __name__ == "__main__":
    unittest.main()
