"""Tests for staged patch node2b_20260622_163733 implementation."""

import unittest

import extractor


class Patch163733Tests(unittest.TestCase):
    """Regression tests from the third node2b RL feedback handoff."""

    def test_compound_provenance_strain_for_pure_cannabinoid(self):
        """Pure-cannabinoid papers populate strain_reported from compound vendor."""
        text = "CBD obtained from Cayman Chemical (>98% purity) was administered to rats."
        reported, _ = extractor.extract_strain_info(
            text, cannabis_type=["pure cannabinoid"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("Cayman Chemical", reported)
        self.assertIn("CBD", reported)

    def test_animal_strain_takes_priority_over_compound(self):
        """Animal strain wins when both animal and compound cues are present."""
        text = "C57BL/6 mice (Jackson Laboratories) received CBD from Cayman Chemical."
        reported, _ = extractor.extract_strain_info(
            text, cannabis_type=["pure cannabinoid"],
        )
        self.assertIn("C57BL/6", reported)
        self.assertNotIn("Cayman", reported or "")

    def test_dissolved_in_media_exposure(self):
        """Ex vivo bath and zebrafish immersion map to dissolved-in-media route."""
        title = "Colon strip study"
        abstract = "METHODS: Isolated rat colon strips in isometric conditions were incubated with CBD."
        study = ["Animal Models (Rat)"]
        methods = extractor.infer_exposure_method(title, abstract, study)
        self.assertEqual(methods, ["cannabinoids dissolved in media"])

        zeb_title = "Zebrafish THC"
        zeb_abstract = "METHODS: Embryos were exposed to THC dissolved in tank water."
        methods2 = extractor.infer_exposure_method(zeb_title, zeb_abstract, study)
        self.assertEqual(methods2, ["cannabinoids dissolved in media"])

    def test_injection_requires_explicit_route_near_cannabinoid(self):
        """Injection route only fires with explicit abbreviations near cannabinoid."""
        title = "THC dosing"
        abstract = "METHODS: Rats received i.p. injections of THC (5 mg/kg)."
        study = ["Animal Models (Rat)"]
        methods = extractor.infer_exposure_method(title, abstract, study)
        self.assertIn("injection cannabinoids", methods)

        vague = "METHODS: Animals were injected with vehicle before oral THC."
        methods_vague = extractor.infer_exposure_method(title, vague, study)
        self.assertNotIn("injection cannabinoids", methods_vague)

    def test_dietary_pure_compound_not_edibles(self):
        """Sigma/Cayman compound in chow stays pure cannabinoid, not edibles."""
        title = "Dietary THC"
        abstract = (
            "METHODS: THC (Sigma-Aldrich, >99% purity) was incorporated into standard rodent chow."
        )
        study = ["Animal Models (Rat)"]
        exposure = extractor.infer_exposure_method(title, abstract, study)
        types = extractor.infer_cannabis_type(title, abstract, study, exposure)
        self.assertIn("pure cannabinoid", types)
        self.assertNotIn("edibles", types)

    def test_duration_week_conversion_and_following_days(self):
        """Week expressions convert to days; following N days is captured."""
        self.assertEqual(extractor.extract_duration_days("Animals were treated for 6 weeks"), 42.0)
        self.assertEqual(
            extractor.extract_duration_days("following a 28-day protocol"),
            28.0,
        )

    def test_mg_kg_per_day_parsing(self):
        """mg/kg/day and mg/kg per day populate cbd_mg_kg or thc_mg_kg."""
        cbd_text = "CBD (10 mg/kg/day) was administered by gavage."
        _, cbd, _ = extractor.extract_thc_cbd_mg_kg(cbd_text)
        self.assertEqual(cbd, 10.0)

        thc_text = "THC at 5 mg/kg per day was given i.p."
        thc, _, _ = extractor.extract_thc_cbd_mg_kg(thc_text)
        self.assertEqual(thc, 5.0)

    def test_administration_frequency_no_default_and_daily_normalize(self):
        """No frequency keyword returns null; once daily normalizes to daily."""
        self.assertIsNone(extractor.extract_administration_frequency("Rats received CBD for 14 days."))
        self.assertEqual(extractor.extract_administration_frequency("once daily gavage"), "daily")

    def test_dose_mg_no_inference_from_mg_kg(self):
        """Only explicit absolute doses populate dose_mg."""
        text = "THC 5 mg/kg i.p. daily."
        self.assertIsNone(extractor.extract_dose_mg(text))
        thc, _, _ = extractor.extract_thc_cbd_mg_kg(text)
        self.assertEqual(thc, 5.0)

        absolute = "Each rat received 2 mg THC total."
        self.assertEqual(extractor.extract_dose_mg(absolute), 2.0)


if __name__ == "__main__":
    unittest.main()
