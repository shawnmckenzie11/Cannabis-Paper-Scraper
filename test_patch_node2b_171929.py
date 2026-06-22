"""Tests for node2b patch from batch node2b_calibration_20260622_171929_756."""

import unittest

import extractor


class PatchNode2b171929Tests(unittest.TestCase):
    """Regression tests from the 10-paper node2b RL feedback handoff."""

    def test_mixed_invitro_primary_null_strain(self):
        """Mixed cell-culture + ethics-only animal mention yields null strain."""
        text = (
            "Ethics: Wistar rats were approved. "
            "Cells were incubated in vitro for 24 h with THC dissolved in media."
        )
        study_type = ["Animal Models (Rat)", "Cell Culture (Cell Lines)"]
        reported, _ = extractor.extract_strain_info(text, study_type=study_type)
        self.assertIsNone(reported)

    def test_invivo_synthetic_only_cp55940(self):
        """Synthetic agonist studies report the test article, not animal vendor strains."""
        text = "C57BL/6 mice from Harlan received CP-55,940 (3 mg/kg, i.p.) daily for 4 days."
        study_type = ["Animal Models (Mouse)"]
        reported, _ = extractor.extract_strain_info(text, study_type=study_type)
        self.assertIsNotNone(reported)
        self.assertIn("CP-55,940", reported)
        self.assertNotIn("Harlan", reported or "")
        self.assertNotIn("C57BL/6", reported or "")

    def test_named_cultivar_profiles(self):
        """Named cultivar THC/CBD profiles are captured for in-vivo exposure studies."""
        text = (
            "Skywalker Kush (high-THC, 18% THC:0.1% CBD) and "
            "Treasure Island Kush (high-CBD, 0.7% THC:13% CBD) were vaporized."
        )
        study_type = ["Animal Models (Rat)"]
        reported, _ = extractor.extract_strain_info(text, study_type=study_type)
        self.assertIsNotNone(reported)
        self.assertIn("Skywalker Kush", reported)
        self.assertIn("Treasure Island", reported)

    def test_administration_frequency_twice_a_week(self):
        """Whitespace-normalized twice-a-week maps to twice weekly."""
        text = "Treatments were administered twice \na week for 2 weeks."
        self.assertEqual(extractor.extract_administration_frequency(text), "twice weekly")

    def test_sample_size_prefers_largest_n(self):
        """When multiple N values appear, the largest cohort size wins."""
        text = "Seven mice per group (n=7). Total N=40 animals were used."
        self.assertEqual(extractor.extract_sample_size(text), 40)

    def test_duration_skips_incubation_context(self):
        """Cell-culture incubation windows do not populate duration_days."""
        text = "Cells were incubated for 14 days at 37 °C before assay."
        self.assertIsNone(extractor.extract_duration_days(text))

    def test_exposure_whole_body_over_nose(self):
        """Whole-body chamber exposure wins over nose-only when both are mentioned."""
        title = "Vapor exposure in mice"
        abstract = (
            "METHODS: Mice received THC vapor. "
            "Mice were placed in a whole body chamber for whole-body vapor exposure. "
            "A nose-only port was also available."
        )
        study_type = ["Animal Models (Mouse)"]
        methods = extractor.infer_exposure_method(title, abstract, study_type)
        self.assertIn("whole body. smoke/vapor", methods)
        self.assertNotIn("nose only smoke/vapor", methods)


if __name__ == "__main__":
    unittest.main()
