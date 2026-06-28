"""Tests for classification_regression_guard."""

import unittest

import classification_regression_guard as guard
import maude_classifier


PAPER_5533_PRIOR = {
    "study_type": [
        "Cell Culture (Other In Vitro)",
        "Animal Models (Other)",
        "Cell Culture (Cell Lines)",
    ],
    "species": "other_mammal",
    "exposure_method": ["cannabinoids dissolved in media"],
    "cannabis_type": ["pure cannabinoid"],
    "outcome_domain": ["inflammation", "oncology"],
    "sample_size": 18,
    "thc_pct": 0.08,
    "cbd_pct": 6,
    "thc_mg_ml": 0.05,
    "cbd_mg_ml": 1,
    "administration_frequency": "daily",
    "dose_mg": 584,
    "treatment_duration": "24 hours",
    "classifier_version": "maude-ft-2.7.0",
}

PAPER_5533_SPARSE = {
    "study_type": ["Cell Culture (Other In Vitro)"],
    "species": None,
    "exposure_method": [],
    "cannabis_type": [],
    "outcome_domain": [],
    "sample_size": None,
    "thc_pct": None,
    "cbd_pct": None,
    "thc_mg_ml": None,
    "cbd_mg_ml": None,
    "administration_frequency": None,
    "dose_mg": None,
    "treatment_duration": None,
    "classifier_version": "maude-2.7.0",
}

PAPER_2929_PRIOR = {
    "study_type": ["Cell Culture (Other In Vitro)", "Animal Models (Mouse)"],
    "species": "mouse",
    "exposure_method": ["cannabinoids dissolved in media", "oral administration"],
    "cannabis_type": ["pure cannabinoid", "CB receptor antagonist"],
    "outcome_domain": ["pain", "inflammation", "neuroprotection"],
    "sample_size": 6,
    "strain_reported": "CBDV 20; THC",
    "duration_days": 14,
    "administration_frequency": "every other day",
    "cbd_mg_kg": 30,
    "dose_mg": 13,
    "treatment_duration": "24 hours",
    "classifier_version": "maude-ft-2.7.0",
}

PAPER_6255_PRIOR = {
    "study_type": [
        "Animal Models (Other)",
        "Cell Culture (Cell Lines)",
        "Animal Models (Mouse)",
        "Cell Culture (Co-Culture)",
    ],
    "species": "mouse",
    "exposure_method": ["cannabinoids dissolved in media", "injection cannabinoids"],
    "cannabis_type": ["pure cannabinoid"],
    "outcome_domain": ["pain", "anxiety", "inflammation", "oncology", "neuroprotection"],
    "sample_size": 6,
    "strain_reported": "CBD; Sigma-Aldrich",
    "cbd_mg_ml": 0.05,
    "duration_days": 2,
    "administration_frequency": "single treatment",
    "cbd_mg_kg": 10,
    "treatment_duration": "24 hours",
    "classifier_version": "maude-ft-2.7.0",
}

PAPER_9370_PRIOR = {
    "study_type": ["Cell Culture (Other In Vitro)", "Animal Models (Mouse)"],
    "species": "mouse",
    "exposure_method": [
        "exposure of cells to smoke/vapor",
        "whole body. smoke/vapor",
        "injection cannabinoids",
        "cannabinoids dissolved in media",
    ],
    "cannabis_type": ["pure cannabinoid"],
    "outcome_domain": ["other"],
    "sample_size": 36,
    "inhaled_exposure_duration": "3 puffs",
    "thc_pct": 6.2,
    "cbd_pct": 0.01,
    "thc_mg_ml": 0.1,
    "dose_mg": 40,
    "treatment_duration": "7 days",
    "classifier_version": "maude-ft-2.7.0",
}


def _sparse_from_prior(prior: dict) -> dict:
    """Simulate abstract-only fast-pass wipe."""
    sparse = dict(prior)
    sparse["study_type"] = [prior["study_type"][0]] if prior.get("study_type") else []
    for field in guard.EXTRACTABLE_PROPERTY_FIELDS:
        if field == "study_type":
            continue
        if field in guard._LIST_FIELDS:
            sparse[field] = []
        else:
            sparse[field] = None
    sparse["classifier_version"] = "maude-2.7.0"
    return sparse


class TestClassificationRegressionGuard(unittest.TestCase):
    """Unit tests for anti-regression merge logic."""

    def test_title_cues_in_vitro(self):
        title = (
            "Evaluation of the Efficacy of a Full-Spectrum Low-THC Cannabis Plant Extract "
            "Using In Vitro Models of Inflammation and Excitotoxicity"
        )
        self.assertTrue(guard.title_has_explicit_study_cues(title))

    def test_blocks_wipe_paper_5533(self):
        title = (
            "Evaluation of the Efficacy of a Full-Spectrum Low-THC Cannabis Plant Extract "
            "Using In Vitro Models of Inflammation and Excitotoxicity"
        )
        blocked, reasons = guard.would_regress_classification(
            PAPER_5533_PRIOR,
            PAPER_5533_SPARSE,
            title,
        )
        self.assertTrue(blocked)
        self.assertIn("explicit_title_cues", reasons)

        merged, meta = guard.merge_regression_safe(
            PAPER_5533_PRIOR,
            PAPER_5533_SPARSE,
            title=title,
        )
        self.assertTrue(meta["merged"])
        self.assertGreater(
            guard.count_extractable_properties(merged),
            guard.count_extractable_properties(PAPER_5533_SPARSE),
        )
        self.assertEqual(merged["classifier_version"], "maude-ft-2.7.0")
        self.assertEqual(merged["dose_mg"], 584)

    def test_blocks_wipe_paper_2929(self):
        title = (
            "Analgesic and toxicological evaluation of cannabidiol-rich Moroccan Cannabis sativa L. "
            "(Khardala variety) extract: Evidence from an in vivo and in silico study"
        )
        sparse = _sparse_from_prior(PAPER_2929_PRIOR)
        merged, meta = guard.merge_regression_safe(
            PAPER_2929_PRIOR,
            sparse,
            title=title,
        )
        self.assertTrue(meta["merged"])
        self.assertIn("Animal Models (Mouse)", merged["study_type"])
        self.assertEqual(merged["cbd_mg_kg"], 30)

    def test_blocks_wipe_paper_6255(self):
        title = (
            "Cannabidiol Enhances Atezolizumab Efficacy by Upregulating PD-L1 Expression "
            "via the cGAS-STING Pathway in Triple-Negative Breast Cancer Cells"
        )
        sparse = _sparse_from_prior(PAPER_6255_PRIOR)
        merged, meta = guard.merge_regression_safe(
            PAPER_6255_PRIOR,
            sparse,
            title=title,
        )
        self.assertTrue(meta["merged"])
        self.assertEqual(merged["cbd_mg_kg"], 10)
        self.assertEqual(merged["strain_reported"], "CBD; Sigma-Aldrich")

    def test_blocks_wipe_paper_9370(self):
        title = (
            "Pharmacokinetics of delta-9-tetrahydrocannabinol following acute cannabis smoke "
            "exposure in mice; effects of sex, age, and strain"
        )
        sparse = _sparse_from_prior(PAPER_9370_PRIOR)
        merged, meta = guard.merge_regression_safe(
            PAPER_9370_PRIOR,
            sparse,
            title=title,
        )
        self.assertTrue(meta["merged"])
        self.assertEqual(merged["thc_pct"], 6.2)
        self.assertEqual(merged["inhaled_exposure_duration"], "3 puffs")

    def test_allows_update_when_new_is_richer(self):
        prior = {"study_type": ["Animal Models (Rat)"], "classifier_version": "maude-2.7.0"}
        new = {
            "study_type": ["Animal Models (Rat)"],
            "exposure_method": ["injection cannabinoids"],
            "cannabis_type": ["pure cannabinoid"],
            "dose_mg": 300,
            "classifier_version": "maude-pdf-2.7.0",
        }
        blocked, _ = guard.would_regress_classification(prior, new, "Rat THC study")
        self.assertFalse(blocked)
        merged, meta = guard.merge_regression_safe(prior, new, title="Rat THC study")
        self.assertFalse(meta["merged"])
        self.assertEqual(merged["dose_mg"], 300)

    def test_tier_downgrade_preserves_ft_version(self):
        merged, _ = guard.merge_regression_safe(
            PAPER_5533_PRIOR,
            PAPER_5533_SPARSE,
            title="In Vitro Models of Inflammation",
        )
        self.assertEqual(merged["classifier_version"], "maude-ft-2.7.0")

    def test_should_skip_fast_pass_for_ft_tier(self):
        paper = {"classifier_version": "maude-ft-2.7.0"}
        self.assertTrue(guard.should_skip_fast_pass_for_tier(paper, "2.7.0"))
        self.assertFalse(
            guard.should_skip_fast_pass_for_tier(
                {"classifier_version": "maude-2.7.0"},
                "2.7.0",
            )
        )

    def test_abstract_downstream_preclinical_title(self):
        title = (
            "Cannabidiol Enhances Atezolizumab Efficacy by Upregulating PD-L1 Expression "
            "via the cGAS-STING Pathway in Triple-Negative Breast Cancer Cells"
        )
        self.assertTrue(maude_classifier._abstract_allows_downstream_extraction(title, ""))


if __name__ == "__main__":
    unittest.main()
