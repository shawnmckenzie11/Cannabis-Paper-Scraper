"""Tests for decision-tree node traversal in calibration dashboard metrics."""

import unittest

import calibration_metrics


class TestCalibrationNodeTraversal(unittest.TestCase):
    """Unit tests for Node 2 original-research dashboard routing."""

    def _sample_payload(self, **result_overrides):
        """Builds a minimal native Maude A/B batch payload for traversal tests."""
        result = {
            "paper_id": 99901,
            "pmid": "9999001",
            "title": "THC reduces anxiety in mice",
            "abstract": "We administered THC to C57BL/6 mice and measured behavior.",
            "variant": "control",
            "after_confidence": 0.88,
            "status": "updated",
            "llm": {
                "publication_type": "original research",
                "study_type": ["Animal Models (Mouse)"],
                "ingestion_status": "relevant",
            },
            "maude": {
                "publication_type": "review",
                "study_type": ["review"],
                "ingestion_status": "relevant",
                "nodes_visited": ["node0_ingestion", "node1b_reviews"],
            },
        }
        result.update(result_overrides)
        return {
            "batch_id": "node1_calibration_test_original",
            "mode": "node1_routing",
            "automation_node": "node1",
            "results": [result],
        }

    def test_original_research_routes_under_node2(self):
        """Original research papers appear in Node 2 and the matching subtype branch."""
        payload = self._sample_payload()
        traversal = calibration_metrics.build_node_traversal([payload])
        node2 = traversal["nodes"]["node2"]
        node2b = traversal["nodes"]["node2b"]

        self.assertEqual(traversal["original_research_paper_count"], 1)
        self.assertEqual(node2["stats"]["paper_count"], 1)
        self.assertEqual(node2b["stats"]["paper_count"], 1)
        self.assertEqual(node2["papers"][0]["routing_subnode"], "node2b")

    def test_stale_review_routing_subnode_is_recomputed_for_originals(self):
        """Stored review routing does not hide original-research papers from Node 2."""
        payload = self._sample_payload(routing_subnode="node1b")
        traversal = calibration_metrics.build_node_traversal([payload])

        self.assertEqual(traversal["nodes"]["node2"]["stats"]["paper_count"], 1)
        self.assertEqual(traversal["nodes"]["node2b"]["papers"][0]["paper_id"], 99901)


if __name__ == "__main__":
    unittest.main()
