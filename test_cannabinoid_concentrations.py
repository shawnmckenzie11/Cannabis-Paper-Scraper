#!/usr/bin/env python3
"""Unit tests for unified THC/CBD concentration extraction."""

import unittest

import extractor


class CannabinoidConcentrationTests(unittest.TestCase):
    """Tests pct, mg/mL, mg/g, mg/kg, and µM extractors."""

    def test_thc_pct_and_range(self):
        """THC percentage and range averaging."""
        self.assertEqual(extractor.extract_thc_pct("The material contained 12.5% THC."), 12.5)
        self.assertEqual(extractor.extract_thc_pct("The variety had 5-10% THC."), 7.5)

    def test_cbd_pct_from_full_text_style(self):
        """CBD percentage in methods/full-text phrasing (golden paper 8709 pattern)."""
        text = "Cannabis extract with 13% CBD was administered to mice."
        self.assertEqual(extractor.extract_cbd_pct(text), 13.0)

    def test_cbd_pct_rejects_co2_context(self):
        """Lab reagent percentages must not populate cbd_pct."""
        text = "Cells were cultured in 5% CO2 at 37°C with CBD treatment."
        self.assertIsNone(extractor.extract_cbd_pct(text))

    def test_thc_pct_acid_context_rejected(self):
        """THCA/CBDA acid ratios must not populate thc_pct."""
        text = "THCA to THC acid ratio was 3:1 with 20% THCA."
        self.assertIsNone(extractor.extract_thc_pct(text))

    def test_thc_mg_ml_and_ug_ml(self):
        """THC mg/mL and µg/mL conversion."""
        self.assertEqual(extractor.extract_thc_mg_ml("Solution contained 5 mg/mL THC."), 5.0)
        self.assertAlmostEqual(extractor.extract_thc_mg_ml("Stock: THC 500 µg/mL in ethanol."), 0.5)

    def test_cbd_mg_ml_and_ug_ml(self):
        """CBD mg/mL and µg/mL conversion."""
        self.assertEqual(extractor.extract_cbd_mg_ml("Cells treated with 10 mg/mL CBD."), 10.0)
        self.assertAlmostEqual(extractor.extract_cbd_mg_ml("CBD 250 µg/mL in media."), 0.25)

    def test_mg_g_extraction(self):
        """mg/g concentrations near compound mentions."""
        text = "Tincture contained 0.001 mg/g THC and 0.002 mg/g CBD."
        self.assertAlmostEqual(extractor.extract_thc_mg_g(text), 0.001)
        self.assertAlmostEqual(extractor.extract_cbd_mg_g(text), 0.002)

    def test_mg_kg_extraction(self):
        """mg/kg dose concentrations."""
        thc, cbd, _ = extractor.extract_thc_cbd_mg_kg("Mice received THC 3 mg/kg i.p.")
        self.assertEqual(thc, 3.0)
        self.assertIsNone(cbd)

    def test_uM_extraction_and_nM_conversion(self):
        """µM concentrations with nM conversion."""
        self.assertEqual(extractor.extract_cbd_uM("Cells exposed to 5 µM CBD for 24 h."), 5.0)
        self.assertAlmostEqual(extractor.extract_thc_uM("THC at 500 nM was applied."), 0.5)

    def test_uM_rejects_non_cannabinoid_context(self):
        """Non-cannabinoid micromolar mentions must not populate thc_uM."""
        text = "DMSO vehicle at 0.1% and dopamine 10 µM were used."
        self.assertIsNone(extractor.extract_thc_uM(text))

    def test_extract_thc_concentrations_unified(self):
        """Unified THC dict returns all populated unit fields."""
        text = "Plant material: 18% THC. Mice received THC 2 mg/kg."
        result = extractor.extract_thc_concentrations(text)
        self.assertAlmostEqual(result["thc_pct"], 18.0)
        self.assertEqual(result["thc_mg_kg"], 2.0)

    def test_extract_cbd_concentrations_unified(self):
        """Unified CBD dict returns populated fields."""
        text = "CBD 10 mg/mL stock; cells treated with 5 µM CBD."
        result = extractor.extract_cbd_concentrations(text)
        self.assertEqual(result["cbd_mg_ml"], 10.0)
        self.assertEqual(result["cbd_uM"], 5.0)

    def test_text_has_concentration_signals(self):
        """Fast-path signal detector for bulk backfill."""
        self.assertTrue(extractor.text_has_concentration_signals("12% THC"))
        self.assertTrue(extractor.text_has_concentration_signals("5 mg/kg THC"))
        self.assertFalse(extractor.text_has_concentration_signals("Cannabis use disorder study"))

    def test_extract_all_heuristics_cbd_pct_full_text_fallback(self):
        """extract_all_heuristics scans full_text for cbd_pct."""
        title = "Cannabis study in mice"
        abstract = "We tested behavioral effects."
        full_text = "Methods: extract with 13% CBD was injected daily."
        result = extractor.extract_all_heuristics(title, abstract, full_text=full_text)
        self.assertEqual(result.get("cbd_pct"), 13.0)

    def test_extract_all_heuristics_cbd_mg_ml_not_gated_to_invitro(self):
        """cbd_mg_ml is extracted regardless of study type."""
        title = "CBD vapor study"
        abstract = "Rats inhaled CBD 2 mg/mL solution."
        result = extractor.extract_all_heuristics(title, abstract)
        self.assertEqual(result.get("cbd_mg_ml"), 2.0)


if __name__ == "__main__":
    unittest.main()
