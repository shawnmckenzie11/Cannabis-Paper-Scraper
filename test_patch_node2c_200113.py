"""Tests for node2c patch from batch node2c_calibration_20260622_200113_934."""

import unittest

import extractor


class PatchNode2c200113Tests(unittest.TestCase):
    """Regression tests from the offset-10 node2c RL holdout."""

    def test_invitro_only_defaults_to_dissolved_in_media(self):
        """Cell-culture-only papers route to dissolved-in-media when no explicit route."""
        title = "CBD cytotoxicity in glioblastoma cells"
        abstract = "METHODS: U87 cells were treated with cannabidiol dissolved in DMSO."
        study_type = ["Cell Culture (Cell Lines)"]
        routes = extractor.infer_exposure_method(title, abstract, study_type)
        self.assertEqual(routes, ["cannabinoids dissolved in media"])

    def test_invitro_only_replaces_animal_oral_route(self):
        """In-vitro-only studies drop spurious oral-administration routes."""
        title = "THC in primary microglia"
        abstract = (
            "METHODS: Cells were incubated with THC in culture medium for 24 hours. "
            "Oral administration was not used."
        )
        study_type = ["Cell Culture (Primary Cells)"]
        routes = extractor.infer_exposure_method(title, abstract, study_type)
        self.assertEqual(routes, ["cannabinoids dissolved in media"])

    def test_catalog_compound_strain_sigma(self):
        """Vendor-qualified CBD descriptions populate strain_reported for in-vitro work."""
        text = "Cannabidiol solution in methanol, Sigma C-045 was added to the medium."
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Cell Lines)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("Sigma", reported)

    def test_treatment_duration_hours_to_days_range(self):
        """Hour-to-day incubation ranges normalize to canonical labels."""
        text = "Cells were exposed for 24 hours to 6 days before assay."
        self.assertEqual(extractor.extract_treatment_duration(text), "24 hours to 6 days")


if __name__ == "__main__":
    unittest.main()
