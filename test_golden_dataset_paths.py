"""Tests for tree path endpoint matching used in golden dataset construction."""

import unittest

import golden_dataset_paths


class GoldenDatasetPathTests(unittest.TestCase):
    """Unit tests for endpoint definitions and paper matching."""

    def test_non_review_endpoint_catalog(self):
        """Non-review catalog includes clinical, in vivo, and in vitro only."""
        endpoints = golden_dataset_paths.non_review_tree_path_endpoints()
        branches = {endpoint.branch for endpoint in endpoints}
        self.assertEqual(branches, {"clinical", "in_vivo", "in_vitro"})
        self.assertEqual(len(endpoints), 20 + 40 + 18)

    def test_clinical_oral_rct_matches(self):
        """Clinical RCT + oral exposure routes to the expected endpoint."""
        endpoint = next(
            ep for ep in golden_dataset_paths.non_review_tree_path_endpoints()
            if ep.id == "node2a.clinical_rct.oral"
        )
        paper = {
            "study_type": ["Clinical (RCT)"],
            "exposure_method": ["oral"],
            "publication_type": "original research",
        }
        self.assertTrue(golden_dataset_paths.paper_matches_endpoint(paper, endpoint))

    def test_in_vivo_injection_alias_matches(self):
        """injection cannabinoids matches in vivo injection endpoint."""
        endpoint = next(
            ep for ep in golden_dataset_paths.non_review_tree_path_endpoints()
            if ep.id.startswith("node2b.animal_models_mouse.injection_cannabinoids")
        )
        paper = {
            "study_type": ["Animal Models (Mouse)"],
            "exposure_method": ["injection cannabinoids"],
            "publication_type": "original research",
        }
        self.assertTrue(golden_dataset_paths.paper_matches_endpoint(paper, endpoint))

    def test_keyword_fallback_matches_title_abstract(self):
        """Keyword cues match when classification fields are missing."""
        endpoint = next(
            ep for ep in golden_dataset_paths.non_review_tree_path_endpoints()
            if ep.id == "node2a.clinical_observational.inhaled"
        )
        paper = {
            "publication_type": "original research",
            "title": "Cross-sectional survey of cannabis smoking in adults",
            "abstract": "Participants reported inhalation via smoking and vaping.",
            "study_type": [],
            "exposure_method": [],
        }
        self.assertFalse(golden_dataset_paths.paper_matches_endpoint(paper, endpoint))
        self.assertTrue(golden_dataset_paths.paper_matches_endpoint_keywords(paper, endpoint))

    def test_review_papers_excluded(self):
        """Review publication types are flagged for exclusion."""
        paper = {
            "publication_type": "review",
            "study_type": ["systematic review"],
        }
        self.assertTrue(golden_dataset_paths.is_review_paper(paper))

    def test_sort_endpoints_by_pdf_class_pool(self):
        """Endpoint summary sort orders by PDF classification pool descending."""
        endpoints = [
            {"endpoint_id": "a", "pool_size_pdf_classification": 3},
            {"endpoint_id": "b", "pool_size_pdf_classification": 10},
            {"endpoint_id": "c", "pool_size_pdf_classification": 0},
        ]
        ordered = golden_dataset_paths.sort_endpoints_by_pdf_class_pool(endpoints)
        self.assertEqual([ep["endpoint_id"] for ep in ordered], ["b", "a", "c"])

    def test_sort_uses_full_text_pool_as_tiebreaker(self):
        """Equal PDF class pools sort by full-text pool descending."""
        endpoints = [
            {
                "endpoint_id": "same_pdf_high_ft",
                "pool_size_pdf_classification": 5,
                "pool_size_full_text_keywords": 200,
            },
            {
                "endpoint_id": "same_pdf_low_ft",
                "pool_size_pdf_classification": 5,
                "pool_size_full_text_keywords": 50,
            },
            {
                "endpoint_id": "higher_pdf",
                "pool_size_pdf_classification": 12,
                "pool_size_full_text_keywords": 1,
            },
        ]
        ordered = golden_dataset_paths.sort_endpoints_by_pdf_class_pool(endpoints)
        self.assertEqual(
            [ep["endpoint_id"] for ep in ordered],
            ["higher_pdf", "same_pdf_high_ft", "same_pdf_low_ft"],
        )

    def test_endpoint_by_id(self):
        """endpoint_by_id resolves known tree path endpoints."""
        endpoint = golden_dataset_paths.endpoint_by_id("node2a.clinical_observational.inhaled")
        self.assertIsNotNone(endpoint)
        self.assertEqual(endpoint.scope_subnode, "node2a")

    def test_sorted_non_review_first_row(self):
        """Sorted endpoints place clinical_observational.inhaled first when golden JSON exists."""
        ordered = golden_dataset_paths.sorted_non_review_endpoints()
        self.assertTrue(ordered)
        self.assertEqual(ordered[0].id, "node2a.clinical_observational.inhaled")


if __name__ == "__main__":
    unittest.main()
