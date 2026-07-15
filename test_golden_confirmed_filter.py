"""Tests for endpoint-scoped golden confirmed filtering."""

import unittest

import golden_confirmed_store as gcs


class GoldenConfirmedFilterTests(unittest.TestCase):
    """Confirmed-store filters used by the golden guard."""

    def test_filter_by_endpoint_id(self):
        """Endpoint filter keeps only matching endpoint papers."""
        papers = [
            {"paper_id": 1, "endpoint_id": "node2a.clinical_observational.inhaled", "scope_subnode": "node2a"},
            {"paper_id": 2, "endpoint_id": "node2a.clinical_rct.oral", "scope_subnode": "node2a"},
            {"paper_id": 3, "endpoint_id": "node2b.animal_models_mouse.injection_cannabinoids", "scope_subnode": "node2b"},
        ]
        filtered = gcs.filter_by_endpoint_id(
            papers,
            "node2a.clinical_observational.inhaled",
        )
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["paper_id"], 1)

    def test_filter_by_scope_then_endpoint(self):
        """Subnode then endpoint narrows the guard set as intended."""
        papers = [
            {"paper_id": 1, "endpoint_id": "node2a.clinical_observational.inhaled", "scope_subnode": "node2a"},
            {"paper_id": 2, "endpoint_id": "node2a.clinical_rct.oral", "scope_subnode": "node2a"},
        ]
        scoped = gcs.filter_by_scope_subnode(papers, "node2a")
        endpoint = gcs.filter_by_endpoint_id(scoped, "node2a.clinical_rct.oral")
        self.assertEqual([p["paper_id"] for p in endpoint], [2])


if __name__ == "__main__":
    unittest.main()
