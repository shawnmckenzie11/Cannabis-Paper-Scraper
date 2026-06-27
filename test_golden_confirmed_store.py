"""Tests for golden_confirmed_store."""

import json
import tempfile
import unittest
from pathlib import Path

import golden_confirmed_store as gcs


class GoldenConfirmedStoreTests(unittest.TestCase):
    """Unit tests for confirmed golden store helpers."""

    def test_append_dedupes_by_paper_and_endpoint(self):
        """Second append with same keys replaces the prior record."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "golden_confirmed.json"
            first = {
                "paper_id": 1,
                "endpoint_id": "node2a.clinical_observational.inhaled",
                "scope_subnode": "node2a",
                "ground_truth": {"study_type": ["Clinical (observational)"]},
            }
            second = dict(first)
            second["ground_truth"] = {"study_type": ["Clinical (RCT)"]}
            gcs.append_papers([first], path=path)
            gcs.append_papers([second], path=path)
            store = gcs.load_confirmed(path)
            self.assertEqual(len(store["papers"]), 1)
            self.assertEqual(
                store["papers"][0]["ground_truth"]["study_type"],
                ["Clinical (RCT)"],
            )

    def test_filter_by_scope_subnode(self):
        """Subnode filter returns only matching confirmed papers."""
        papers = [
            {"paper_id": 1, "scope_subnode": "node2a"},
            {"paper_id": 2, "scope_subnode": "node2b"},
        ]
        filtered = gcs.filter_by_scope_subnode(papers, "node2b")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["paper_id"], 2)

    def test_build_ground_truth_includes_routing_fields(self):
        """Ground truth builder includes routing fields when populated."""
        row = {
            "study_type": ["Clinical (observational)"],
            "exposure_method": ["inhaled"],
            "publication_type": "original research",
            "species": "human",
            "outcome_domain": ["psychiatric"],
        }
        gt = gcs.build_ground_truth_from_row(row, ["outcome_domain"])
        self.assertIn("study_type", gt)
        self.assertIn("exposure_method", gt)
        self.assertIn("publication_type", gt)
        self.assertIn("species", gt)
        self.assertIn("outcome_domain", gt)


if __name__ == "__main__":
    unittest.main()
