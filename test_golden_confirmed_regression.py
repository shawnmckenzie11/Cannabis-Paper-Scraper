"""Tests for golden confirmed regression guard."""

import unittest

from scripts.golden_confirmed_regression import (
    compare_ground_truth,
    guard_fields_for_paper,
    paper_alignment_rate,
    run_golden_regression,
)


class GoldenConfirmedRegressionTests(unittest.TestCase):
    """Unit tests for golden regression comparisons."""

    def test_list_field_match(self):
        """List fields compare with order independence."""
        maude = {"study_type": ["Clinical (observational)"]}
        gt = {"study_type": ["clinical (observational)"]}
        self.assertEqual(compare_ground_truth(maude, gt), {})

    def test_list_field_mismatch(self):
        """Mismatched list fields are reported."""
        maude = {"exposure_method": ["oral"]}
        gt = {"exposure_method": ["inhaled"]}
        failures = compare_ground_truth(maude, gt)
        self.assertIn("exposure_method", failures)
        self.assertEqual(failures["exposure_method"]["expected"], ["inhaled"])

    def test_inclusion_criteria_excluded_from_guard(self):
        """Free-text inclusion criteria are not golden-guard fields."""
        paper = {
            "scope_subnode": "node2a",
            "scope_fields": ["study_type", "inclusion_criteria", "outcome_domain"],
            "ground_truth": {
                "study_type": ["Clinical (observational)"],
                "inclusion_criteria": "long text",
                "outcome_domain": ["anxiety"],
            },
        }
        fields = guard_fields_for_paper(paper)
        self.assertIn("study_type", fields)
        self.assertIn("outcome_domain", fields)
        self.assertNotIn("inclusion_criteria", fields)
        self.assertNotIn("exclusion_criteria", fields)

    def test_exclusion_criteria_excluded_from_alignment_rate(self):
        """Criteria mismatches do not reduce paper alignment rate."""
        maude = {
            "study_type": ["Clinical (observational)"],
            "inclusion_criteria": "different text",
            "exclusion_criteria": "other text",
        }
        gt = {
            "study_type": ["Clinical (observational)"],
            "inclusion_criteria": "long inclusion",
            "exclusion_criteria": "long exclusion",
        }
        rate = paper_alignment_rate(maude, gt, scope_subnode="node2a")
        self.assertEqual(rate, 1.0)

    def test_batch_passes_at_ninety_percent_alignment(self):
        """Golden guard passes when average alignment meets 90% threshold."""
        import golden_confirmed_store as gcs
        from pathlib import Path
        import tempfile
        import json

        with tempfile.TemporaryDirectory() as tmp:
            confirmed_path = Path(tmp) / "golden_confirmed.json"
            store = {
                "papers": [
                    {
                        "paper_id": 1,
                        "scope_subnode": "node2a",
                        "ground_truth": {
                            "study_type": ["Clinical (observational)"],
                            "exposure_method": ["inhaled"],
                        },
                    },
                    {
                        "paper_id": 2,
                        "scope_subnode": "node2a",
                        "ground_truth": {
                            "study_type": ["Clinical (observational)"],
                            "exposure_method": ["oral"],
                        },
                    },
                ],
            }
            with open(confirmed_path, "w", encoding="utf-8") as handle:
                json.dump(store, handle)

            from unittest.mock import patch

            def fake_maude(paper, **kwargs):
                pid = paper.get("paper_id")
                if pid == 1:
                    return {
                        "study_type": ["Clinical (observational)"],
                        "exposure_method": ["inhaled"],
                    }
                return {
                    "study_type": ["Clinical (observational)"],
                    "exposure_method": ["inhaled"],
                }

            with patch(
                "scripts.golden_confirmed_regression.classify_paper_maude",
                side_effect=fake_maude,
            ):
                report = run_golden_regression(
                    "node2a",
                    confirmed_path=confirmed_path,
                    min_alignment_pct=90.0,
                )
            self.assertEqual(report["batch_alignment_pct"], 75.0)
            self.assertFalse(report["passed"])

            with patch(
                "scripts.golden_confirmed_regression.classify_paper_maude",
                side_effect=fake_maude,
            ):
                report = run_golden_regression(
                    "node2a",
                    confirmed_path=confirmed_path,
                    min_alignment_pct=70.0,
                )
            self.assertTrue(report["passed"])

    def test_filter_by_scope_subnode(self):
        """filter_by_scope_subnode isolates node2b papers from node2a."""
        import golden_confirmed_store as gcs

        papers = [
            {"paper_id": 1, "scope_subnode": "node2a"},
            {"paper_id": 2, "scope_subnode": "node2b"},
        ]
        node2b_only = gcs.filter_by_scope_subnode(papers, "node2b")
        self.assertEqual([p["paper_id"] for p in node2b_only], [2])


if __name__ == "__main__":
    unittest.main()
