"""Tests for golden dataset candidate scoring gates and ranking."""

from __future__ import annotations

import golden_candidate_scoring as gcs
import golden_dataset_paths


def _clinical_endpoint() -> golden_dataset_paths.TreePathEndpoint:
    """Return a clinical branch endpoint for gate tests."""
    for endpoint in golden_dataset_paths.non_review_tree_path_endpoints():
        if endpoint.branch == "clinical":
            return endpoint
    raise RuntimeError("no clinical endpoint")


def _in_vivo_endpoint() -> golden_dataset_paths.TreePathEndpoint:
    """Return an in vivo branch endpoint for preclinical scoring tests."""
    for endpoint in golden_dataset_paths.non_review_tree_path_endpoints():
        if endpoint.branch == "in_vivo":
            return endpoint
    raise RuntimeError("no in_vivo endpoint")


def test_clinical_gate_requires_population_age_and_sex():
    """Clinical candidates must have population_age and population_sex populated."""
    endpoint = _clinical_endpoint()
    missing_sex = {"population_age": "adult", "population_sex": None}
    assert not gcs.golden_gates_pass(missing_sex, endpoint)

    complete = {"population_age": "adult", "population_sex": "mixed"}
    assert gcs.golden_gates_pass(complete, endpoint)


def test_clinical_scored_fields_include_pct_and_population():
    """Clinical scoring counts routing, population, and all THC/CBD concentration fields."""
    endpoint = _clinical_endpoint()
    paper = {
        "study_type": ["Clinical (observational)"],
        "exposure_method": ["inhaled"],
        "cannabis_type": ["whole plant"],
        "outcome_domain": ["pain"],
        "species": "human",
        "duration_days": 14,
        "population_age": "adult",
        "population_sex": "mixed",
        "thc_pct": 10,
        "cbd_pct": 2,
    }
    assert gcs.characteristic_count(paper, endpoint) == 10
    assert "thc_mg_kg" in gcs.CLINICAL_SCORED_FIELDS
    assert "cbd_uM" in gcs.CLINICAL_SCORED_FIELDS


def test_preclinical_no_gate_but_counts_rich_fields():
    """In vivo papers rank by characteristic count without hard gates."""
    endpoint = _in_vivo_endpoint()
    sparse = {"dose_mg": 5}
    rich = {
        "study_type": ["animal"],
        "exposure_method": ["inhaled"],
        "cannabis_type": ["whole plant"],
        "outcome_domain": ["inflammation"],
        "species": "mouse",
        "sample_size": 12,
        "duration_days": 7,
        "puff_count": 3,
        "inhaled_exposure_duration": "10 min",
        "repeat_exposure_count": 5,
        "exposure_regimen_bin": "chronic",
        "strain_reported": "ACDC",
        "administration_frequency": "daily",
        "thc_pct": 12,
        "cbd_pct": 1,
    }
    assert gcs.golden_gates_pass(sparse, endpoint)
    assert gcs.characteristic_count(rich, endpoint) > gcs.characteristic_count(sparse, endpoint)


def test_golden_sort_key_prefers_higher_characteristic_count():
    """Sort key ranks papers with more populated scored fields first."""
    endpoint = _clinical_endpoint()
    high = {
        "study_type": ["Clinical (observational)"],
        "exposure_method": ["inhaled"],
        "cannabis_type": ["whole plant"],
        "outcome_domain": ["pain"],
        "species": "human",
        "duration_days": 7,
        "population_age": "adult",
        "population_sex": "mixed",
        "classification_confidence": 0.5,
        "citation_count": 1,
        "title": "A",
    }
    low = {
        "population_age": "adult",
        "population_sex": "mixed",
        "classification_confidence": 0.9,
        "citation_count": 99,
        "title": "B",
    }
    assert gcs.golden_sort_key(high, endpoint) > gcs.golden_sort_key(low, endpoint)


def test_preclinical_counts_mg_concentration_fields():
    """In vivo scoring includes all THC/CBD concentration unit fields."""
    endpoint = _in_vivo_endpoint()
    paper = {
        "thc_pct": 10,
        "thc_mg_ml": 2.5,
        "thc_mg_g": 1.0,
        "thc_mg_kg": 5,
        "thc_uM": 3.0,
        "cbd_pct": 1,
        "cbd_mg_ml": 0.5,
        "cbd_mg_g": 3,
        "cbd_mg_kg": 8,
        "cbd_uM": 2.0,
    }
    assert gcs.characteristic_count(paper, endpoint) == 10


def test_tangential_papers_fail_golden_gates():
    """Tangential papers are excluded from golden candidate selection."""
    endpoint = _in_vivo_endpoint()
    paper = {"ingestion_status": "tangential", "publication_type": "original research"}
    assert not gcs.is_searchable_golden_candidate(paper)
    assert not gcs.golden_gates_pass(paper, endpoint)


def test_prefer_published_over_biorxiv_duplicate():
    """Published journal records win over bioRxiv preprints for the same title."""
    title = (
        "Adult consequences of repeated nicotine and Δ9-tetrahydrocannabinol (THC) "
        "vapor inhalation in adolescent rats"
    )
    preprint = {
        "id": 9281,
        "doi": "10.1101/2023.09.08.556932",
        "year": 2023,
        "ingestion_status": "relevant",
        "title": title,
    }
    published = {
        "id": 8182,
        "doi": "10.1007/s00213-024-06545-5",
        "year": 2024,
        "ingestion_status": "relevant",
        "title": title,
    }
    assert golden_dataset_paths.prefer_golden_candidate(published, preprint)
    assert not golden_dataset_paths.prefer_golden_candidate(preprint, published)
    assert (
        golden_dataset_paths.normalize_title_match_key(preprint["title"])
        == golden_dataset_paths.normalize_title_match_key(
            "Adult consequences of repeated nicotine and Δ 9 -tetrahydrocannabinol (THC) vapor inhalation in adolescent rats"
        )
    )
