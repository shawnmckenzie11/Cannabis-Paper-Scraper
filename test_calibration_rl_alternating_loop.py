# test_calibration_rl_alternating_loop.py
"""Tests for alternating RL loop gate helpers."""

import unittest

import calibration_rl_alternating_loop as loop


class AlternatingLoopGateTests(unittest.TestCase):
    """Holdout gate and offset-0 scheduling."""

    def test_should_run_offset0_every_three_cycles(self):
        """Offset-0 generalization runs every third completed cycle."""
        state = {"cycles_completed": 3, "offset0_every_n_cycles": 3}
        self.assertTrue(loop.should_run_offset0_batch(state))
        state["cycles_completed"] = 2
        self.assertFalse(loop.should_run_offset0_batch(state))
        state["cycles_completed"] = 6
        self.assertTrue(loop.should_run_offset0_batch(state))

    def test_target_met_uses_holdout_alignment(self):
        """95% gate checks latest_holdout_alignment_pct when gate_mode is holdout_field_subset."""
        state = {
            "gate_mode": "holdout_field_subset",
            "target_alignment_pct": 95.0,
            "latest_holdout_alignment_pct": {
                "node2a": 96.0,
                "node2b": 95.5,
                "node2c": 95.0,
            },
            "latest_offset0_alignment_pct": {
                "node2a": 70.0,
                "node2b": 72.0,
                "node2c": 74.0,
            },
        }
        self.assertTrue(loop.target_met(state))


if __name__ == "__main__":
    unittest.main()
