"""Unit tests for exposure-route duration extraction."""

import unittest

import duration_extraction


class TestDurationExtraction(unittest.TestCase):
    """Tests route-specific duration field population."""

    def test_invitro_conditioned_media_uses_treatment_duration(self):
        """Conditioned media route stores incubation length in treatment_duration."""
        profile = duration_extraction.extract_exposure_duration_profile(
            "Smoke extract study",
            "Cells were incubated with smoke/vapor conditioned media for 24 hours.",
            ["Cell Culture (Cell Lines)"],
            ["smoke/vapor conditioned media"],
        )
        self.assertEqual(profile["treatment_duration"], "24 hours")
        self.assertIsNone(profile["inhaled_exposure_duration"])
        self.assertIsNone(profile["repeat_exposure_count"])

    def test_invitro_direct_smoke_uses_per_exposure_fields(self):
        """Direct cell smoke exposure stores per-session duration and repeat count."""
        profile = duration_extraction.extract_exposure_duration_profile(
            "ALI smoke exposure",
            "Cells received direct smoke exposure for 30 minutes during 2 separate exposures.",
            ["Cell Culture (Cell Lines)"],
            ["exposure of cells to smoke/vapor"],
        )
        self.assertEqual(profile["inhaled_exposure_duration"], "30 minutes")
        self.assertEqual(profile["repeat_exposure_count"], 2)
        self.assertIsNone(profile["treatment_duration"])

    def test_invivo_smoke_bins_subchronic(self):
        """Whole-body smoke with 7 days of exposure is subchronic."""
        profile = duration_extraction.extract_exposure_duration_profile(
            "Mouse smoke chamber",
            "Mice were exposed in a whole body chamber for 30 minutes twice daily for 7 days.",
            ["Animal Models (Mouse)"],
            ["whole body. smoke/vapor"],
        )
        self.assertEqual(profile["inhaled_exposure_duration"], "30 minutes")
        self.assertEqual(profile["duration_days"], 7.0)
        self.assertEqual(profile["exposure_regimen_bin"], "subchronic")

    def test_invivo_smoke_bins_acute_on_two_exposures(self):
        """Two total smoke exposures classify as acute."""
        profile = duration_extraction.extract_exposure_duration_profile(
            "Acute smoke",
            "Rats received nose only smoke/vapor for 15 minutes in two exposures.",
            ["Animal Models (Rat)"],
            ["nose only smoke/vapor"],
        )
        self.assertEqual(profile["repeat_exposure_count"], 2)
        self.assertEqual(profile["exposure_regimen_bin"], "acute")

    def test_invivo_systemic_uses_frequency_and_days(self):
        """Injection/oral routes populate exposures/day and duration_days only."""
        profile = duration_extraction.extract_exposure_duration_profile(
            "THC injection study",
            "Mice received intraperitoneal THC once daily for 21 days.",
            ["Animal Models (Mouse)"],
            ["injection cannabinoids"],
        )
        self.assertEqual(profile["duration_days"], 21.0)
        self.assertIsNotNone(profile["administration_frequency"])
        self.assertIsNone(profile["exposure_regimen_bin"])
        self.assertIsNone(profile["inhaled_exposure_duration"])


if __name__ == "__main__":
    unittest.main()
