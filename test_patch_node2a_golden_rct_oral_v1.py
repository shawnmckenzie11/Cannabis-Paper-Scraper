"""Regression tests for node2a.clinical_rct.oral golden guard patch."""

import json
import unittest
from pathlib import Path

import extractor as ex

GOLDEN_CYCLE = Path(
    "scratch/golden_dataset/cycles/node2a.clinical_rct.oral/"
    "node2a.clinical_rct.oral_20260628_000101/llm_results.json"
)


def _golden_row(paper_id: int) -> dict:
    """Load one golden holdout row from the oral RCT cycle artifact."""
    payload = json.loads(GOLDEN_CYCLE.read_text())
    return next(row for row in payload["results"] if row["paper_id"] == paper_id)


class PatchNode2aGoldenRctOralV1Tests(unittest.TestCase):
    """Holdout-derived checks for node2a.clinical_rct.oral golden guard."""

    def test_cbd_oil_mg_kg_oral_not_sublingual(self):
        """CBD oil mg/kg/day trials without sublingual protocol map to oral route."""
        row = _golden_row(16088)
        exposure = ex.infer_exposure_method(
            row["title"], row["text"][:3000], ["Clinical (RCT)"], full_text=row["text"],
        )
        self.assertEqual(exposure, ["oral"])

    def test_cbg_tincture_ingestion_oral(self):
        """CBG tincture field trials with oral ingestion map to oral, not unknown."""
        row = _golden_row(6721)
        exposure = ex.infer_exposure_method(
            row["title"], row["text"][:3000], ["Clinical (RCT)"], full_text=row["text"],
        )
        self.assertEqual(exposure, ["oral"])

    def test_cbg_trial_outcomes_anxiety_cognition(self):
        """CBG anxiety trials with verbal memory tasks map to anxiety+cognition."""
        row = _golden_row(6721)
        outcomes = ex.extract_outcomes(
            row["title"], row["text"][:3000], full_text=row["text"], study_type=["Clinical (RCT)"],
        )
        self.assertIn("anxiety", outcomes)
        self.assertIn("cognition", outcomes)
        self.assertNotIn("addiction", outcomes)

    def test_cbd_pk_trial_neuroprotection_outcome(self):
        """CBD PK/safety trials for epilepsy indications map to neuroprotection."""
        row = _golden_row(17084)
        outcomes = ex.extract_outcomes(
            row["title"], row["text"][:3000], full_text=row["text"], study_type=["Clinical (RCT)"],
        )
        self.assertIn("neuroprotection", outcomes)
        self.assertNotIn("other", outcomes)

    def test_male_or_female_subjects_both_sex(self):
        """Male-or-female eligibility phrasing maps population_sex to both."""
        row = _golden_row(17084)
        sex = ex.extract_population_sex(row["text"][:8000])
        self.assertEqual(sex, "both")

    def test_fibromyalgia_sublingual_oil_unchanged(self):
        """Explicit sublingual oil drop trials still map to sublingual route."""
        abstract = (
            "The initial dose was one drop of THC-rich cannabis oil a day sublingually. "
            "17 women with fibromyalgia received cannabis oil 24.44 mg/ml of THC."
        )
        title = (
            "Ingestion of a THC-Rich Cannabis Oil in People with Fibromyalgia: "
            "A Randomized, Double-Blind, Placebo-Controlled Clinical Trial"
        )
        exposure = ex.infer_exposure_method(title, abstract, ["Clinical (RCT)"], full_text=abstract)
        self.assertEqual(exposure, ["sublingual"])


if __name__ == "__main__":
    unittest.main()
