# test_subnode_field_scopes.py
"""Tests for sub-node field scopes and candidate mode mapping."""

import unittest

import subnode_field_scopes
from calibration_agent import select_candidates


class SubnodeFieldScopeTests(unittest.TestCase):
    """Node 7 path inference and field scope resolution."""

    def test_infer_node7_in_vivo_paths(self):
        """In vivo exposure_method values map to Node 7a–7g."""
        self.assertEqual(
            subnode_field_scopes.infer_node7_in_vivo_path(["whole body. smoke/vapor"]),
            "7a",
        )
        self.assertEqual(
            subnode_field_scopes.infer_node7_in_vivo_path("nose only smoke/vapor"),
            "7b",
        )
        self.assertEqual(
            subnode_field_scopes.infer_node7_in_vivo_path(["intratracheal"]),
            "7g",
        )

    def test_infer_node7_in_vitro_paths(self):
        """In vitro exposure_method values map to Node 7a–7c."""
        self.assertEqual(
            subnode_field_scopes.infer_node7_in_vitro_path(["smoke/vapor conditioned media"]),
            "7a",
        )
        self.assertEqual(
            subnode_field_scopes.infer_node7_in_vitro_path(["cannabinoids dissolved in media"]),
            "7c",
        )

    def test_resolve_scope_key_refines_node2b(self):
        """Node 2B scope refines to node7_in_vivo when exposure_method is present."""
        llm = {"exposure_method": ["injection cannabinoids"]}
        key = subnode_field_scopes.resolve_scope_key("node2b", llm)
        self.assertEqual(key, "node7_in_vivo.7c")

    def test_mode_to_calibration_label(self):
        """Sub-node modes produce dedicated classifier_version labels."""
        self.assertEqual(
            subnode_field_scopes.calibration_label_for_subnode("node2b", "node2b_in_vivo"),
            "node2b-calibration",
        )


class SubnodeCandidateSelectionTests(unittest.TestCase):
    """SQL candidate selection for sub-node modes."""

    def test_node2b_mode_returns_list(self):
        """node2b_in_vivo mode executes without error."""
        rows = select_candidates(
            mode="node2b_in_vivo",
            fetch_limit=5,
            confidence_max=0.6,
            require_full_text=False,
            exclude_locked=True,
            exclude_calibrated=True,
            calibration_label="node2b-calibration",
        )
        self.assertIsInstance(rows, list)


if __name__ == "__main__":
    unittest.main()
