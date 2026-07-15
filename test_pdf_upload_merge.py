"""Tests for PDF upload fuzzy title merge helpers."""

import unittest

from pdf_upload_merge import (
    apply_review_selections,
    build_merge_field_rows,
    build_review_field_rows,
    clean_title_for_matching,
    parse_custom_field_value,
    significant_title_tokens,
    title_similarity,
)


class TestPdfUploadMerge(unittest.TestCase):
    """Fuzzy title matching and review row generation."""

    def test_title_similarity_close_match(self):
        left = "Cannabis Use and Anxiety in Adults"
        right = "Cannabis use and anxiety in adults: a cohort study"
        self.assertGreaterEqual(title_similarity(left, right), 0.81)

    def test_clean_title_strips_author_filename_prefix(self):
        raw = (
            "Milad - Dried Cannabis Use, Tobacco Smoking, and COVID-19 Infection - "
            "Findings from a Longitudinal Observational Cohort Study"
        )
        cleaned = clean_title_for_matching(raw)
        self.assertTrue(cleaned.startswith("Dried Cannabis Use"))
        self.assertNotIn("Milad", cleaned)

    def test_title_similarity_ignores_author_prefix(self):
        uploaded = (
            "Milad - Dried Cannabis Use, Tobacco Smoking, and COVID-19 Infection - "
            "Findings from a Longitudinal Observational Cohort Study"
        )
        existing = (
            "Dried Cannabis Use, Tobacco Smoking, and COVID-19 Infection - "
            "Findings from a Longitudinal Observational Cohort Study"
        )
        self.assertGreaterEqual(title_similarity(uploaded, existing), 0.95)

    def test_title_similarity_boosts_truncated_containment(self):
        short = "Cannabis smoke suppresses antiviral immune responses"
        full = "Cannabis smoke suppresses antiviral immune responses to influenza A in mice"
        self.assertGreaterEqual(title_similarity(short, full), 0.9)

    def test_title_token_like_pattern_tolerates_punctuation(self):
        from pdf_upload_merge import title_token_like_pattern

        pattern = title_token_like_pattern(
            "Dried Cannabis Use, Tobacco Smoking, and COVID-19 Infection"
        )
        self.assertIn("dried", pattern)
        self.assertIn("covid", pattern)
        self.assertIn("19", pattern)
        self.assertTrue(pattern.startswith("%") and pattern.endswith("%"))

    def test_collapse_title_match_rows_prefers_richer_full_title(self):
        from pdf_upload_merge import collapse_title_match_rows

        rows = [
            {
                "id": 32348,
                "title": "Cannabis smoke suppresses antiviral immune responses",
                "similarity": 1.0,
                "year": None,
                "pmid": None,
                "doi": None,
                "journal": "",
                "full_text_link": "",
            },
            {
                "id": 8730,
                "title": "Cannabis smoke suppresses antiviral immune responses to influenza A in mice",
                "similarity": 0.95,
                "year": 2023,
                "pmid": "38020563",
                "doi": "10.1183/23120541.00219-2023",
                "journal": "ERJ open research",
                "full_text_link": "https://example.com/paper.pdf",
            },
            {
                "id": 32349,
                "title": "Cannabis smoke suppresses antiviral immune responses",
                "similarity": 1.0,
                "year": None,
                "pmid": None,
                "doi": None,
                "journal": "",
                "full_text_link": "",
            },
        ]
        collapsed = collapse_title_match_rows(
            rows,
            query_title="Milad - Cannabis smoke suppresses antiviral immune responses to influenza A in mice",
            limit=5,
        )
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["id"], 8730)

    def test_significant_tokens_prefer_content_words(self):
        title = (
            "Milad - Dried Cannabis Use, Tobacco Smoking, and COVID-19 Infection - "
            "Findings from a Longitudinal Observational Cohort Study"
        )
        tokens = significant_title_tokens(title)
        self.assertTrue(tokens)
        self.assertNotIn("milad", tokens)
        self.assertTrue(any("cannabis" in t or "tobacco" in t or "covid" in t for t in tokens))

    def test_build_merge_field_rows_only_differs(self):
        existing = {"study_type": ["Clinical (observational)"], "sample_size": 120}
        proposed = {"study_type": ["Clinical (RCT)"], "sample_size": 120}
        rows = build_merge_field_rows(existing, proposed)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field"], "study_type")

    def test_build_review_field_rows_includes_all_fields(self):
        existing = {"study_type": ["Clinical (observational)"], "sample_size": 120}
        proposed = {"study_type": ["Clinical (RCT)"], "sample_size": 120, "dose_mg": 10}
        rows = build_review_field_rows(existing, proposed, is_new_paper=False)
        self.assertGreater(len(rows), 20)
        study_row = next(r for r in rows if r["field"] == "study_type")
        dose_row = next(r for r in rows if r["field"] == "dose_mg")
        self.assertEqual(study_row["row_status"], "conflict")
        self.assertEqual(dose_row["row_status"], "new")

    def test_build_review_field_rows_new_paper_marks_new_values(self):
        proposed = {"study_type": ["Clinical (RCT)"], "sample_size": 40}
        rows = build_review_field_rows({}, proposed, is_new_paper=True)
        study_row = next(r for r in rows if r["field"] == "study_type")
        self.assertEqual(study_row["row_status"], "new")
        self.assertEqual(study_row["default_pick"], "uploaded")

    def test_apply_review_selections_custom_value(self):
        existing = {"study_type": ["Clinical (observational)"], "sample_size": 120}
        proposed = {"study_type": ["Clinical (RCT)"], "sample_size": 200}
        merged = apply_review_selections(
            existing,
            proposed,
            {"study_type": "custom", "sample_size": "existing"},
            {"study_type": "Clinical (prospective)"},
            is_new_paper=False,
        )
        self.assertEqual(merged["study_type"], ["Clinical (prospective)"])
        self.assertEqual(merged["sample_size"], 120)

    def test_parse_custom_field_value_list(self):
        self.assertEqual(
            parse_custom_field_value("study_type", "Clinical (RCT), Clinical (observational)"),
            ["Clinical (RCT)", "Clinical (observational)"],
        )

    def test_parse_custom_rejects_invalid_enum_and_negative(self):
        self.assertIsNone(parse_custom_field_value("publication_type", "not-a-real-type"))
        self.assertEqual(parse_custom_field_value("publication_type", "review"), "review")
        self.assertIsNone(parse_custom_field_value("dose_mg", "-5"))
        self.assertEqual(parse_custom_field_value("dose_mg", "12.5"), 12.5)
        self.assertEqual(parse_custom_field_value("sample_size", "40"), 40)


if __name__ == "__main__":
    unittest.main()
