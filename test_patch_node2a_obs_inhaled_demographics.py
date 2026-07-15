# test_patch_node2a_obs_inhaled_demographics.py
"""Narrow node2a observational.inhaled demographics + 12-week duration cues."""

from __future__ import annotations

import unittest

import maude_classifier


class Node2aObsInhaledDemographicsTests(unittest.TestCase):
    """Holdout-derived cues for sex/age/duration on observational clinical PDFs."""

    def test_mixed_sex_counts_and_adult_age_range(self):
        """Demographics past Methods truncate still yield both + adult."""
        title = "Midfrontal conflict theta and externalising disorders"
        abstract = "We linked EEG markers to DSM externalising disorders."
        full_text = (
            "Introduction " + ("background text. " * 800)
            + "Age range and gender profile for the 206 participants consisted of "
            "92 males, 114 females; mean age = 36; SD = 9; range = 18– 56)."
        )
        out = maude_classifier.classify_paper(
            title,
            abstract,
            full_text=full_text,
            rules_version="2.7.0",
            abstract_only_extraction=False,
        )
        self.assertEqual(out.get("population_sex"), "both")
        self.assertEqual(out.get("population_age"), "adult")

    def test_twelve_weeks_posttrauma_duration(self):
        """Observational follow-up schedule 12 weeks posttrauma → duration_days 84."""
        title = "Alcohol and cannabis use after trauma"
        abstract = "Recently trauma-exposed civilians were assessed over time."
        full_text = (
            "Methods. In total, 1618 (1037 female) participants provided self-report data. "
            "We reassessed participant substance use and clinical symptoms 2, 8, and "
            "12 weeks posttrauma. Latent class mixture modeling determined trajectories."
        )
        out = maude_classifier.classify_paper(
            title,
            abstract,
            full_text=full_text,
            rules_version="2.7.0",
            abstract_only_extraction=False,
        )
        self.assertEqual(out.get("duration_days"), 84.0)
        self.assertEqual(out.get("population_sex"), "both")


if __name__ == "__main__":
    unittest.main()
