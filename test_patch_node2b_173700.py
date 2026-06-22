"""Tests for node2b cycle — strain vendor priority, injection routing, plant cultivar."""

import unittest

import calibration_pdf
import extractor as ex
from calibration_agent import get_rules_version


class PatchNode2b173700Tests(unittest.TestCase):
    """Regression tests from node2b holdout batch node2b_calibration_20260622_200722_248."""

    def test_injection_route_guard_wider_window(self):
        """Intraperitoneal mg/kg dosing is detected even when cannabinoid is earlier in sentence."""
        text = (
            "Control rats received cannabidiol (CBD, 10 mg/kg) via intraperitoneal (ip) "
            "administration for 21 days."
        )
        self.assertTrue(ex._has_injection_route_guard(text))

    def test_invivo_strain_prefers_vendor_over_animal_label(self):
        """Pure cannabinoid in-vivo studies report vendor catalog ids, not Wistar/C57 labels."""
        text = (
            "Male Wistar rats received synthetic CBD, purity ≥99%; THC Pharm GmbH, "
            "Frankfurt, Germany for 21 days."
        )
        strain = ex._extract_invivo_strain_reported(text, ["pure cannabinoid"])
        self.assertIsNotNone(strain)
        self.assertNotIn(strain.lower(), {"wistar", "c57bl/6"})

    def test_full_text_upgrades_bare_compound_strain(self):
        """Bare title-level CBD labels yield to vendor-rich full-text extraction."""
        title = "Cannabidiol may prevent congestive hepatopathy in rats"
        pdf_snippet = (
            "Methods: rats received CBD (THC-1073G-1; THC Pharm, Frankfurt, Germany) "
            "10 mg/kg i.p. once daily for 21 days."
        )
        heuristics = ex.extract_all_heuristics(title, "", full_text=pdf_snippet, study_type_override=["Animal Models (Rat)"])
        strain = heuristics.get("strain_reported") or ""
        self.assertFalse(ex._is_bare_compound_strain_label(strain))

    def test_plant_cultivation_cultivar_from_abstract(self):
        """Hemp variety names in abstract map to strain_reported for plant stress studies."""
        abstract = (
            "A hemp variety, Green-Thunder (5-8% CBD/mg of dry weight), was treated with "
            "drought stress during flowering."
        )
        strain, _ = ex.extract_strain_info(abstract, study_type=["Animal Models (Other)"])
        self.assertIn("Green-Thunder", strain or "")

    def test_holdout_paper_11824_exposure_injection(self):
        """Springer holdout paper routes injection cannabinoids with PDF text."""
        title = (
            "Cannabidiol may prevent the development of congestive hepatopathy "
            "secondary to right ventricular hypertrophy associated with pulmonary hypertension in rats"
        )
        rules = get_rules_version()
        maude_out, pdf_used = calibration_pdf.classify_maude_for_calibration(
            title,
            "",
            full_text_link="https://link.springer.com/content/pdf/10.1007/s43440-024-00579-4.pdf",
            rules_version=rules,
        )
        self.assertTrue(pdf_used)
        self.assertIn("injection cannabinoids", maude_out.get("exposure_method") or [])


if __name__ == "__main__":
    unittest.main()
