"""Tests for staged patch node2b_20260622_154633 implementation."""

import unittest

import extractor
import maude_classifier


class Patch154633Tests(unittest.TestCase):
    """Regression tests from the second node2b RL feedback handoff."""

    def test_animal_and_compound_strain_reported(self):
        """Animal strains and synthetic agonists populate strain_reported."""
        text = "Sprague-Dawley rats received CP-55940 daily for 14 days."
        reported, _ = extractor.extract_strain_info(text)
        self.assertIsNotNone(reported)
        self.assertIn("Sprague-Dawley", reported)
        self.assertIn("CP-55940", reported)

    def test_extract_thc_mg_ml(self):
        """THC mg/mL concentration is parsed for inhalation papers."""
        text = "Rats received vapor from THC at 5 mg/mL in propylene glycol."
        self.assertEqual(extractor.extract_thc_mg_ml(text), 5.0)

    def test_vapor_defaults_nose_only_not_whole_body(self):
        """Unqualified vapor inhalation in rodents defaults to nose-only exposure."""
        title = "Vapor study"
        abstract = (
            "METHODS: Rats received THC via 30-min sessions of vapor inhalation daily for 10 days."
        )
        study = ["Animal Models (Rat)"]
        methods = extractor.infer_exposure_method(title, abstract, study)
        self.assertIn("nose only smoke/vapor", methods)
        self.assertNotIn("whole body. smoke/vapor", methods)

    def test_in_vivo_override_before_review_route(self):
        """In vivo animal abstracts route to original research before review cues."""
        title = "Systematic aspects of in vivo rat THC dosing"
        abstract = "In vivo experiments were conducted in rats with daily THC."
        pub, _, nodes, _ = maude_classifier.route_publication_type(title, abstract)
        self.assertEqual(pub, "original research")
        self.assertIn("node1a_original", nodes)

    def test_administration_frequency_weekly_and_on_off(self):
        """Weekly and on/off schedules normalize correctly."""
        self.assertEqual(extractor.extract_administration_frequency("dosed weekly"), "weekly")
        self.assertEqual(
            extractor.extract_administration_frequency("5 days on / 2 days off schedule"),
            "5d on / 2d off",
        )


if __name__ == "__main__":
    unittest.main()
