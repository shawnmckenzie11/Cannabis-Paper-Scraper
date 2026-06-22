# test_handoff_learning_log.py
"""Tests for handoff learning log timeline helpers."""

import unittest

import handoff_learning_log


class HandoffLearningLogTests(unittest.TestCase):
    """Node learning timeline construction."""

    def test_build_node_learning_timeline_orders_handoff_before_run(self):
        """Handoffs sort before batch runs when timestamps are earlier."""
        handoffs = [{
            "id": "node2b_handoff",
            "source_subnode": "node2b",
            "beneficiary_nodes": ["node2a", "node2b", "node2c"],
            "summary_title": "Node 2b PDF extraction handoff",
            "applied_at": "2026-06-22T16:00:00",
            "learning_notes": ["Note one", "Note two"],
        }]
        runs = [{
            "run_index": 1,
            "batch_id": "node2b_calibration_test",
            "created_at": "2026-06-22T17:00:00",
            "alignment_pct": 66.7,
            "maude_recall_pct": 50.0,
        }]
        timeline = handoff_learning_log.build_node_learning_timeline("node2b", handoffs, runs)
        self.assertEqual(len(timeline), 2)
        self.assertEqual(timeline[0]["kind"], "handoff")
        self.assertEqual(timeline[0]["learning_notes"], ["Note one", "Note two"])
        self.assertEqual(timeline[1]["kind"], "batch_run")

    def test_build_node_learning_timeline_skips_unrelated_nodes(self):
        """Only handoffs affecting the requested sub-node are included."""
        handoffs = [{
            "id": "node2c_only",
            "source_subnode": "node2c",
            "beneficiary_nodes": ["node2c"],
            "applied_at": "2026-06-22T16:00:00",
            "learning_notes": ["In vitro only"],
        }]
        timeline = handoff_learning_log.build_node_learning_timeline("node2b", handoffs, [])
        self.assertEqual(timeline, [])


if __name__ == "__main__":
    unittest.main()
