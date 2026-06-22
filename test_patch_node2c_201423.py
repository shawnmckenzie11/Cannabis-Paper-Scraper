"""Tests for node2c targeted patch from batch node2c_calibration_20260622_201423_837."""

import unittest

import classification_schema
import extractor


class PatchNode2c201423Tests(unittest.TestCase):
    """Regression tests from the offset-20 node2c RL holdout targeted pass."""

    def test_strain_vendor_compound_overlap_scores_agreement(self):
        """Sigma + CBD tokens on both sides count as strain_reported agreement."""
        maude = "CBD 2; CBD; Sigma-Aldrich"
        llm = "Cannabidiol (CBD), Sigma, USA; 2.5 µM optimal concentration"
        self.assertTrue(classification_schema.compare_field_values(maude, llm))

    def test_catalog_strain_captures_donated_by(self):
        """Donated-by phrasing populates in-vitro strain_reported."""
        text = "CBD donated by Dr. Renato Filev, CEBRID/UNIFESP; diluted in DMSO."
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Cell Lines)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("donated by", reported.lower())

    def test_catalog_strain_captures_cerilliant_panel(self):
        """Certified reference material vendor panels populate strain_reported."""
        text = (
            "CBD, Δ9-THC, CBN, CBG standards from Sigma-Aldrich and "
            "Cerilliant (Supelco) certified reference material in methanol."
        )
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Cell Lines)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("Sigma", reported)

    def test_treatment_duration_minute_range(self):
        """Minute-to-minute ranges normalize for in-vitro exposure windows."""
        text = "Cells were treated for 0.5 to 60 minutes before assay."
        self.assertEqual(extractor.extract_treatment_duration(text), "0.5 to 60 minutes")

    def test_treatment_duration_range_compare(self):
        """Trailing-unit and per-bound unit ranges count as agreement."""
        llm = "0.5 to 60 minutes"
        maude = "0.5 minutes to 60 minutes"
        self.assertTrue(classification_schema.compare_field_values(maude, llm))

    def test_analytical_gc_study_type(self):
        """Gas chromatography degradation papers route to in-vitro analytical."""
        title = "Effect of temperature in the degradation of cannabinoids"
        abstract = "gas chromatography/flame ionization detector was used for cannabinoid monitoring."
        self.assertTrue(extractor.is_analytical_or_computational(f"{title} {abstract}"))
        self.assertIn(
            "Cell Culture",
            extractor.infer_study_type_for_publication(title, abstract, "original research")[0],
        )

    def test_ex_vivo_colon_exposure(self):
        """Ex-vivo colon bath maps to cannabinoids dissolved in media."""
        title = "CBD on rat colon motility ex vivo"
        abstract = (
            "Experiments on isolated rat colon strips in isometric conditions. "
            "CBD (80 μM) was applied to the organ bath for 15 minutes."
        )
        study = ["Animal Models (Rat)"]
        exposure = extractor.infer_exposure_method(title, abstract, study)
        self.assertEqual(exposure, ["cannabinoids dissolved in media"])

    def test_ecigarette_pyrolysis_exposure(self):
        """E-cigarette pyrolysis maps to smoke/vapor cell exposure."""
        title = "CBD, a precursor of THC in e-cigarettes"
        abstract = (
            "CBD in electronic cigarettes. Pyrolysis at 250-400 °C; products quantified by GC-MS."
        )
        study = ["Cell Culture (Other In Vitro)"]
        exposure = extractor.infer_exposure_method(title, abstract, study)
        self.assertEqual(exposure, ["exposure of cells to smoke/vapor"])

    def test_cell_line_sigma_strain(self):
        """HC69.5 cell line + Sigma cannabinoid panel populates strain_reported."""
        text = "CBD and Δ(9)-THC (Sigma Aldrich) in human microglial cells (HC69.5)."
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Cell Lines)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("HC69", reported)

    def test_olivetol_formulation_strain(self):
        """MOF/liposome olivetol formulations populate strain_reported."""
        text = (
            "Olivetol (OLV), as a cannabidiol (CBD) analog, was incorporated in "
            "γ-CD-MOFs and DPPC liposomes as delivery systems."
        )
        reported, _ = extractor.extract_strain_info(
            text, study_type=["Cell Culture (Other In Vitro)"],
        )
        self.assertIsNotNone(reported)
        self.assertIn("olivetol", reported.lower())

    def test_extraction_study_pure_cannabinoid(self):
        """Bioactive extraction papers infer pure cannabinoid over dried flower."""
        title = "Extraction of Bioactive Compounds From Cannabis sativa L. Flowers"
        abstract = "Deep eutectic solvents were used for extraction yield of cannabinoids."
        study = ["Cell Culture (Other In Vitro)"]
        ctype = extractor.infer_cannabis_type(title, abstract, study, ["unknown"])
        self.assertIn("pure cannabinoid", ctype)
        self.assertNotIn("dried flower", ctype)

    def test_ex_vivo_classify_treatment_duration(self):
        """Ex-vivo bath papers extract minute-scale CBD exposure from full PDF text."""
        import json
        from pathlib import Path
        import paper_text_cache as ptc
        from maude_classifier import classify_paper

        batch = json.loads(
            Path("scratch/calibration_runs/node2c_calibration_20260622_201423_837.json").read_text()
        )
        row = next(item for item in batch["results"] if item["paper_id"] == 11988)
        full, _ = ptc.resolve_paper_text(paper_id=11988, full_text_link=row.get("full_text_link"))
        self.assertIsNotNone(full)
        out = classify_paper(row["title"], full[:3000], full_text=full)
        self.assertEqual(out.get("treatment_duration"), "15 minutes")

    def test_cbd_pct_rejects_co2_context(self):
        """Lab reagent percentages (CO2, serum) must not populate cbd_pct."""
        text = "Cells at 37°C and 5% CO2. CBD was added at 5 µM for 24 hours."
        self.assertIsNone(extractor.extract_cbd_pct(text))

    def test_analytical_extraction_not_smoke_exposure(self):
        """GC degradation papers route to dissolved media, not smoke/vapor ALI false positives."""
        title = "Effect of temperature in the degradation of cannabinoids"
        abstract = (
            "Gas chromatography/flame ionization detector monitoring; "
            "e-cigarette pyrolysis cited in discussion only."
        )
        study = ["Cell Culture (Other In Vitro)"]
        full = (
            f"{title}\n{abstract}\n"
            "Abdulmomen Ali Mohammed reviewed the degradation profile. "
            "Cannabinoids dissolved in extraction solvent for 90 min ultrasonic bath."
        )
        exposure = extractor.infer_exposure_method(title, abstract, study, full_text=full)
        self.assertEqual(exposure, ["cannabinoids dissolved in media"])


if __name__ == "__main__":
    unittest.main()
