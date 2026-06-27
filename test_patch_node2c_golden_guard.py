"""Regression tests for node2c golden guard holdout papers."""

import unittest

import calibration_pdf
import paper_text_cache
from maude_classifier import (
    classify_paper,
    route_publication_type,
    should_route_invitro_before_review,
)


class PatchNode2cGoldenGuardTests(unittest.TestCase):
    """Tests aligned to promoted golden_confirmed papers for node2c cannabinoids dissolved in media."""

    PLGA_TITLE = (
        "Development, Characterization and In Vitro Gastrointestinal Release of "
        "PLGA Nanoparticles Loaded with Full-Spectrum Cannabis Extracts"
    )
    VCE_TITLE = (
        "The cannabinoid quinol VCE-004.8 alleviates bleomycin-induced scleroderma and "
        "exerts potent antifibrotic effects through peroxisome proliferator-activated "
        "receptor-γ and CB2 pathways"
    )

    def test_plga_title_routes_invitro_before_review(self):
        """In-vitro nanoparticle development titles bypass citation review noise."""
        pdf_text, _ = paper_text_cache.resolve_paper_text(paper_id=7134, use_disk_cache=True)
        blob = f"{self.PLGA_TITLE} {pdf_text[:15000] if pdf_text else ''}"
        self.assertTrue(should_route_invitro_before_review(self.PLGA_TITLE, blob))
        pub, _, nodes, _ = route_publication_type(
            self.PLGA_TITLE,
            pdf_text[:5000] if pdf_text else "",
            routing_blob=blob,
        )
        self.assertEqual(pub, "original research")
        self.assertIn("node1a_original", nodes)

    def test_plga_nanoparticle_extraction_fields(self):
        """PLGA full-spectrum extract papers extract media-dissolved cannabinoid dosing fields."""
        pdf_text, _ = paper_text_cache.resolve_paper_text(paper_id=7134, use_disk_cache=True)
        self.assertTrue(pdf_text)
        result = classify_paper(
            self.PLGA_TITLE,
            pdf_text[:5000],
            full_text=pdf_text,
        )
        self.assertEqual(result.get("publication_type"), "original research")
        self.assertIn("Cell Culture (Other In Vitro)", result.get("study_type") or [])
        self.assertIn("cannabinoids dissolved in media", result.get("exposure_method") or [])
        self.assertEqual(result.get("thc_pct"), 12.0)
        self.assertEqual(result.get("cbd_pct"), 7.0)
        self.assertEqual(result.get("cbd_mg_ml"), 1.0)
        self.assertEqual(result.get("treatment_duration"), "48 hours")

    def test_vce_mixed_paper_species_mouse(self):
        """Bleomycin scleroderma papers with in-vivo and in-vitro arms retain mouse species."""
        pdf_text, _ = paper_text_cache.resolve_paper_text(paper_id=18360, use_disk_cache=True)
        self.assertTrue(pdf_text)
        result, pdf_used = calibration_pdf.classify_maude_for_calibration(
            self.VCE_TITLE,
            "",
            full_text_link="https://www.nature.com/articles/srep21703.pdf",
            full_text=None,
            paper_id=18360,
            rules_version="2.6.0",
            use_disk_cache=True,
        )
        self.assertTrue(pdf_used)
        self.assertEqual(result.get("species"), "mouse")
        self.assertIn("Cell Culture (Other In Vitro)", result.get("study_type") or [])
        self.assertTrue(result.get("exposure_method"))


if __name__ == "__main__":
    unittest.main()
