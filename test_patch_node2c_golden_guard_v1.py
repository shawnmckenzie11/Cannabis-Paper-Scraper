"""Regression tests for node2c golden guard patch (20260627-node2c-golden-guard-v1)."""

import unittest

import golden_confirmed_store
import extractor
from maude_classifier import classify_paper
from scripts.golden_confirmed_regression import classify_paper_maude, compare_ground_truth, paper_alignment_rate


class PatchNode2cGoldenGuardV1Tests(unittest.TestCase):
    """Holdout-derived checks for node2c cell-culture dissolved-in-media golden guard."""

    HERG_TITLE = (
        "The electrophysiological effect of cannabidiol on hERG current and in "
        "guinea-pig and rabbit cardiac preparations"
    )

    def test_herg_outcome_domain_is_other_not_addiction(self):
        """Cardiac electrophysiology papers map to other, not addiction."""
        outcomes = extractor.extract_outcomes(
            self.HERG_TITLE,
            "hERG current and action potential duration were measured.",
            study_type=["Cell Culture (Cell Lines)"],
        )
        self.assertIn("other", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_herg_ic50_uM_extraction(self):
        """IC50 mean values parse before ± standard deviation."""
        text = (
            "Cannabidiol blocked hERG channels with an IC50 value of 2.07 µM at room temperature. "
            "THC elicited higher IC50 values of 10.30 ± 0.55 µM (n = 6 at room temperature)."
        )
        self.assertAlmostEqual(extractor.extract_cbd_uM(text), 2.07, places=2)
        self.assertAlmostEqual(extractor.extract_thc_uM(text), 10.30, places=2)

    def test_minute_range_en_dash(self):
        """En-dash minute ranges normalize for patch-clamp windows."""
        text = "Effect of CBD after 3–5 min on hERG current."
        self.assertEqual(extractor.extract_treatment_duration(text), "3-5 minutes")

    def test_cbd_ug_ml_converts_to_mg_ml(self):
        """µg/mL CBD converts to mg/mL for in-vitro dosing."""
        text = "CBD (10 μ g/ml) alone was evaluated in cell culture."
        self.assertAlmostEqual(extractor.extract_cbd_mg_ml(text), 0.01, places=4)

    def test_colorectal_title_prefers_cell_lines(self):
        """Cancer cell-line titles refine Other In Vitro to Cell Lines."""
        title = (
            "Cell death induction and intracellular vesicle formation in human "
            "colorectal cancer cells treated with Δ9-Tetrahydrocannabinol"
        )
        types = extractor._refine_study_type_list(
            ["Cell Culture (Other In Vitro)"],
            title.lower(),
            title,
            "",
        )
        self.assertIn("Cell Culture (Cell Lines)", types)
        self.assertNotIn("Cell Culture (Other In Vitro)", types)

    def test_glioblastoma_mixed_paper_classifies(self):
        """Orthotopic glioblastoma papers retain cell-culture study type."""
        store = golden_confirmed_store.load_confirmed()
        paper = next(
            item for item in store["papers"]
            if item["paper_id"] == 13866
            and "cell_lines" in item.get("endpoint_id", "")
        )
        result = classify_paper_maude(paper, rules_version="2.6.0")
        study_type = result.get("study_type") or []
        self.assertIn("Cell Culture (Cell Lines)", study_type)

    def test_golden_guard_key_papers(self):
        """Promoted golden rows for this endpoint meet guard alignment."""
        store = golden_confirmed_store.load_confirmed()
        targets = {
            15569: 0.85,
            9099: 0.85,
            11640: 0.85,
            13866: 0.85,
            18360: 0.85,
        }
        for paper_id, min_rate in targets.items():
            paper = next(
                item for item in store["papers"]
                if item["paper_id"] == paper_id
                and item.get("scope_subnode") == "node2c"
            )
            result = classify_paper_maude(paper, rules_version="2.6.0")
            rate = paper_alignment_rate(
                result,
                paper["ground_truth"],
                scope_subnode="node2c",
                scope_fields=paper.get("scope_fields"),
            )
            self.assertIsNotNone(rate)
            self.assertGreaterEqual(
                rate,
                min_rate,
                msg=f"paper {paper_id} alignment {rate} fields {compare_ground_truth(result, paper['ground_truth'], scope_subnode='node2c', scope_fields=paper.get('scope_fields'))}",
            )


if __name__ == "__main__":
    unittest.main()
