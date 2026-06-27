"""Tests for ingestion_status inference edge cases."""

from __future__ import annotations

import classification_schema


def test_legal_restriction_in_abstract_is_not_tangential():
    """Research papers mentioning legal restrictions stay relevant, not tangential."""
    title = "Antiviral activities of hemp cannabinoids"
    abstract = (
        "Hemp is an understudied source of pharmacologically active compounds. "
        "After years of legal restriction, research on hemp has demonstrated "
        "antiviral activities in vitro for cannabidiol."
    )
    assert classification_schema.infer_ingestion_status(title, abstract) == "relevant"
