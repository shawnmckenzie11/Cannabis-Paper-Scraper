"""Tests for dashboard filter tier policy and Filter Agent audit."""
import unittest

from dashboard_ui_config import (
    FILTER_PROFILES,
    FILTER_SECTION_REGISTRY,
    GLOBAL_FILTER_PARAMS,
    allowed_global_filter_params,
    sections_for_tab,
    validate_filter_config,
)
from filter_agent import run_audit


class TestFilterTierPolicy(unittest.TestCase):
    """Validate global vs tab filter configuration."""

    def test_global_params_are_tier_51_only(self):
        """Global bar params must not include routing or extraction keys."""
        forbidden = {
            "classification_level",
            "study_type",
            "exposure_method",
            "cannabis_type",
            "outcome",
            "sample_size_min",
        }
        self.assertFalse(forbidden & allowed_global_filter_params())
        self.assertIn("query", GLOBAL_FILTER_PARAMS)
        self.assertIn("has_pdf", GLOBAL_FILTER_PARAMS)
        self.assertIn("has_full_text", GLOBAL_FILTER_PARAMS)

    def test_classification_details_removed_from_ui_profiles(self):
        """Classification Model filter was removed from sidebar profiles (API param may remain)."""
        self.assertNotIn("classification_level", GLOBAL_FILTER_PARAMS)
        self.assertIn("classification_details", FILTER_SECTION_REGISTRY)
        for tab in ("all_original", "clinical", "preclinical", "review", "unclassified"):
            self.assertNotIn("classification_details", sections_for_tab(tab))

    def test_review_tab_has_publication_type(self):
        """Review tab exposes publication_type (§5.2) filters."""
        self.assertIn("publication_type", sections_for_tab("review"))
        self.assertEqual(FILTER_SECTION_REGISTRY["publication_type"]["tier"], "5.2")

    def test_preclinical_sections_are_extraction_tier(self):
        """Preclinical dose/exposure sections map to §5.3."""
        for section_id in ("dose_in_vivo", "exposure_in_vivo", "species"):
            self.assertEqual(FILTER_SECTION_REGISTRY[section_id]["tier"], "5.3")

    def test_validate_filter_config_passes(self):
        """Full audit against index.html should pass with no violations."""
        errors = validate_filter_config()
        self.assertEqual(errors, [], msg="\n".join(errors))

    def test_run_audit_ok(self):
        """Filter Agent report marks policy as satisfied."""
        report = run_audit()
        self.assertTrue(report["ok"], msg=report.get("errors"))


if __name__ == "__main__":
    unittest.main()
