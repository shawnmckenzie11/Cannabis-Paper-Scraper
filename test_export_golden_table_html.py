"""Tests for golden dataset HTML export helpers."""

from scripts.export_golden_table_html import (
    _endpoint_characteristics_cells,
    _format_label_list,
)


def test_format_label_list_empty():
    """Empty or missing lists render as em dash."""
    assert _format_label_list(None) == "—"
    assert _format_label_list([]) == "—"


def test_format_label_list_joins_values():
    """Non-empty lists are comma-joined for display."""
    assert _format_label_list(["Clinical (RCT)", "case study"]) == (
        "Clinical (RCT), case study"
    )


def test_endpoint_characteristics_cells_renders_gate_and_scored_fields():
    """Summary row includes study type, exposure, required gates, and scored fields."""
    html = _endpoint_characteristics_cells(
        {
            "study_types": ["Clinical (observational)"],
            "exposure_methods": ["inhaled"],
            "required_gate_fields": ["population_age", "population_sex"],
            "scored_fields": [
                "study_type",
                "exposure_method",
                "cannabis_type",
                "population_age",
            ],
        }
    )
    assert "<td>Clinical (observational)</td>" in html
    assert "<td>inhaled</td>" in html
    assert "population_age, population_sex" in html
    assert "study_type, exposure_method, cannabis_type, population_age" in html
