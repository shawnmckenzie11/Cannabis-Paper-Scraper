"""Tests for node2c targeted patch from batch node2c_calibration_20260622_201423_837."""

import unittest

import classification_schema
import extractor


class PatchNode2c201423Tests(unittest.TestCase):
    """Regression tests from the offset-20 node2c RL holdout targeted pass."""

    def test_strain_vendor_compound_overlap_scores_agreement(self):
        """Sigma + CBD tokens on both sides count as strain_reported agreement."""
        maude = "CBD 2; CBD; Sigma-Aldrich"
        llm = "Cannabidiol (CBD), Sigma, USA; 2.5 µM optimal concentration"
        self.assertTrue(classification_schema.compare_field_values(maude, llm))

    def test_catalog_strain_captures_donated_by(self):
        """Donated-by phrasing populates in-vitro strain_reported."""
        text = "CBD donated by Dr. Renato Filev, CEBRID/UNIFESP; diluted in DMSO."
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Cell Lines)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("donated by", reported.lower())

    def test_catalog_strain_captures_cerilliant_panel(self):
        """Certified reference material vendor panels populate strain_reported."""
        text = (
            "CBD, Δ9-THC, CBN, CBG standards from Sigma-Aldrich and "
            "Cerilliant (Supelco) certified reference material in methanol."
        )
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Cell Lines)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("Sigma", reported)

    def test_treatment_duration_minute_range(self):
        """Minute-to-minute ranges normalize for in-vitro exposure windows."""
        text = "Cells were treated for 0.5 to 60 minutes before assay."
        self.assertEqual(extractor.extract_treatment_duration(text), "0.5 minutes to 60 minutes")

    def test_treatment_duration_ex_vivo_minutes(self):
        """Ex-vivo tissue bath exposures capture minute-scale durations."""
        text = "Rat colon motility was assessed ex vivo in an organ bath for 15 minutes."
        self.assertEqual(extractor.extract_treatment_duration(text), "15 minutes")


if __name__ == "__main__":
    unittest.main()
