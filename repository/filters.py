"""Catalog filter SQL assembled per live dialect (no runtime SQL rewrite)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from repository.constants import (
    NUMERIC_RANGE_FILTERS,
    SPECIES_FILTER_TARGETS,
    SQL_HAS_FULL_TEXT_LINK,
    SQL_HAS_PDF_LINK,
    TAB_SQL,
    TAB_SQL_LIKE,
)
from repository.dialect import DialectSQL, expand_study_types


def clean_fts_query(query: str) -> str:
    """Sanitize a query string for SQLite FTS5 / Postgres websearch_to_tsquery."""
    if not query:
        return ""
    terms = query.split()
    cleaned_terms = []
    for term in terms:
        if (term.startswith('"') and term.endswith('"')) or (
            term.startswith("'") and term.endswith("'")
        ):
            cleaned_terms.append(term)
            continue
        has_wildcard = term.endswith("*")
        clean_term = term[:-1] if has_wildcard else term
        if any(char in clean_term for char in ("-", ":", "/", "\\", "+", "~")):
            escaped_term = clean_term.replace('"', '""')
            if has_wildcard:
                cleaned_terms.append(f'"{escaped_term}"*')
            else:
                cleaned_terms.append(f'"{escaped_term}"')
        else:
            cleaned_terms.append(term)
    return " ".join(cleaned_terms)


def resolve_tab_sql(
    tab: Optional[str],
    *,
    dialect: DialectSQL,
    tab_columns_exist: bool,
) -> str:
    """Return tab WHERE SQL, preferring indexed tab_* columns."""
    if not tab:
        return ""
    from paper_tab_flags import dashboard_tab_sql, legacy_tab_sql_for

    if tab_columns_exist:
        indexed_sql = dashboard_tab_sql(tab)
        if indexed_sql:
            return indexed_sql
    if dialect.is_postgres:
        return TAB_SQL_LIKE.get(tab, "")
    legacy_sql = legacy_tab_sql_for(tab)
    if legacy_sql:
        return legacy_sql
    return TAB_SQL.get(tab, "")


def _as_list(value: Any) -> List[str]:
    """Split a comma-separated string or pass through a sequence of strings."""
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _json_and_or_clauses(
    dialect: DialectSQL,
    column: str,
    values: List[str],
    logic: str,
) -> Tuple[List[str], List[Any]]:
    """Build JSON array/scalar membership clauses for AND vs OR filter groups."""
    clauses: List[str] = []
    params: List[Any] = []
    if logic.lower() == "and":
        for value in values:
            clauses.append(dialect.json_contains_value(column))
            params.extend([value, value])
    else:
        clauses.append(dialect.json_contains_any(column, len(values)))
        params.extend(values)
        params.extend(values)
    return clauses, params


def _species_match_clause(dialect: DialectSQL, species_key: str) -> Tuple[str, List[Any]]:
    """Build species filter SQL for one dashboard checkbox value."""
    normalized = (species_key or "").strip().lower()
    targets = SPECIES_FILTER_TARGETS.get(normalized)
    if not targets:
        return "LOWER(COALESCE(papers.species, '')) LIKE ?", [f"%{normalized}%"]

    parts: List[str] = []
    params: List[Any] = []
    for pattern in targets.get("species_like", []):
        parts.append("LOWER(COALESCE(papers.species, '')) LIKE ?")
        params.append(pattern)
    for study_label in targets.get("study_types", []):
        parts.append(dialect.json_contains_value("papers.study_type"))
        params.extend([study_label, study_label])
    return "(" + " OR ".join(parts) + ")", params


def build_filter_clauses(
    filters: Dict[str, Any],
    *,
    dialect: DialectSQL,
    tab_columns_exist: bool,
) -> Tuple[List[str], List[Any]]:
    """Return (WHERE fragments, positional binds) for catalog search/count."""
    where_clauses: List[str] = []
    params: List[Any] = []

    query_val = filters.get("query")
    if query_val:
        where_clauses.append(dialect.fts_match_clause())
        params.append(clean_fts_query(query_val))

    if filters.get("year_min") is not None:
        where_clauses.append("papers.year >= ?")
        params.append(int(filters["year_min"]))

    if filters.get("year_max") is not None:
        where_clauses.append("papers.year <= ?")
        params.append(int(filters["year_max"]))

    study_types = _as_list(filters.get("study_type"))
    if study_types:
        expanded = expand_study_types(study_types)
        extra_clauses, extra_params = _json_and_or_clauses(
            dialect,
            "papers.study_type",
            expanded,
            filters.get("study_logic", "or"),
        )
        where_clauses.extend(extra_clauses)
        params.extend(extra_params)

    if filters.get("citations_min") is not None:
        where_clauses.append("papers.citation_count >= ?")
        params.append(int(filters["citations_min"]))

    cannabis_types = _as_list(filters.get("cannabis_type"))
    if cannabis_types:
        extra_clauses, extra_params = _json_and_or_clauses(
            dialect,
            "papers.cannabis_type",
            cannabis_types,
            filters.get("cannabis_logic", "or"),
        )
        where_clauses.extend(extra_clauses)
        params.extend(extra_params)

    exposure_methods = _as_list(filters.get("exposure_method"))
    if exposure_methods:
        extra_clauses, extra_params = _json_and_or_clauses(
            dialect,
            "papers.exposure_method",
            exposure_methods,
            filters.get("exposure_logic", "or"),
        )
        where_clauses.extend(extra_clauses)
        params.extend(extra_params)

    if filters.get("thc_min") is not None:
        where_clauses.append("papers.thc_pct >= ?")
        params.append(float(filters["thc_min"]))

    if filters.get("thc_max") is not None:
        where_clauses.append("papers.thc_pct <= ?")
        params.append(float(filters["thc_max"]))

    if filters.get("cbd_min") is not None:
        where_clauses.append("papers.cbd_pct >= ?")
        params.append(float(filters["cbd_min"]))

    if filters.get("cbd_max") is not None:
        where_clauses.append("papers.cbd_pct <= ?")
        params.append(float(filters["cbd_max"]))

    has_pdf = filters.get("has_pdf")
    has_full_text = filters.get("has_full_text")
    if has_pdf is not None or has_full_text is not None:
        if isinstance(has_pdf, str):
            has_pdf = has_pdf.lower() in ("true", "1", "yes")
        if isinstance(has_full_text, str):
            has_full_text = has_full_text.lower() in ("true", "1", "yes")
        pdf_active = bool(has_pdf)
        full_text_active = bool(has_full_text)
        if pdf_active and full_text_active:
            where_clauses.append(f"({SQL_HAS_PDF_LINK} OR {SQL_HAS_FULL_TEXT_LINK})")
        elif pdf_active:
            where_clauses.append(SQL_HAS_PDF_LINK)
        elif full_text_active:
            where_clauses.append(SQL_HAS_FULL_TEXT_LINK)

    if filters.get("open_access") is not None:
        val = filters["open_access"]
        if isinstance(val, str):
            val = 1 if val.lower() in ("true", "1", "yes") else 0
        else:
            val = 1 if val else 0
        where_clauses.append("papers.open_access = ?")
        params.append(val)

    tab = filters.get("tab")
    if tab == "original":
        tab = "all_original"
    if tab == "recent":
        tab = None
    tab_sql = resolve_tab_sql(tab, dialect=dialect, tab_columns_exist=tab_columns_exist)
    if tab_sql:
        where_clauses.append(tab_sql)

    recent_range = filters.get("recent_range")
    if recent_range or filters.get("recent"):
        from paper_tab_flags import recent_range_sql

        recent_clause, recent_params = recent_range_sql(recent_range or "180d")
        where_clauses.append(recent_clause)
        params.extend(recent_params)

    for filter_key, column, operator in NUMERIC_RANGE_FILTERS:
        raw = filters.get(filter_key)
        if raw is not None and raw != "":
            where_clauses.append(f"{column} {operator} ?")
            params.append(float(raw))

    population_age = _as_list(filters.get("population_age"))
    if population_age:
        placeholders = ",".join(["?"] * len(population_age))
        where_clauses.append(f"LOWER(COALESCE(papers.population_age, '')) IN ({placeholders})")
        params.extend([item.lower() for item in population_age])

    population_sex = _as_list(filters.get("population_sex"))
    if population_sex:
        placeholders = ",".join(["?"] * len(population_sex))
        where_clauses.append(f"LOWER(COALESCE(papers.population_sex, '')) IN ({placeholders})")
        params.extend([item.lower() for item in population_sex])

    species_values = _as_list(filters.get("species"))
    if species_values:
        species_clauses = []
        for species in species_values:
            clause, clause_params = _species_match_clause(dialect, species)
            species_clauses.append(clause)
            params.extend(clause_params)
        where_clauses.append("(" + " OR ".join(species_clauses) + ")")

    regimen_values = _as_list(filters.get("exposure_regimen_bin"))
    if regimen_values:
        placeholders = ",".join(["?"] * len(regimen_values))
        where_clauses.append(
            f"LOWER(COALESCE(papers.exposure_regimen_bin, '')) IN ({placeholders})"
        )
        params.extend([value.lower() for value in regimen_values])

    outcomes = _as_list(filters.get("outcome"))
    if outcomes:
        if filters.get("outcome_logic", "or").lower() == "and":
            for outcome in outcomes:
                where_clauses.append(dialect.json_exists_value("papers.outcome_domain"))
                params.append(outcome)
        else:
            where_clauses.append(
                dialect.json_exists_any("papers.outcome_domain", len(outcomes))
            )
            params.extend(outcomes)

    if filters.get("claude_classified"):
        where_clauses.append("papers.classifier_version LIKE 'llm-%'")

    publication_types = _as_list(filters.get("publication_type"))
    if publication_types:
        if filters.get("publication_type_logic", "or").lower() == "and":
            for pub_type in publication_types:
                where_clauses.append("LOWER(papers.publication_type) = LOWER(?)")
                params.append(pub_type)
        else:
            placeholders = ",".join(["LOWER(?)"] * len(publication_types))
            where_clauses.append(f"LOWER(papers.publication_type) IN ({placeholders})")
            params.extend(publication_types)

    class_level = filters.get("classification_level")
    if class_level and class_level != "ALL":
        if class_level == "claude_abstract":
            where_clauses.append(
                "(papers.classifier_version LIKE 'llm-reclassify-%' AND papers.classifier_version NOT LIKE 'llm-pdf-%')"
            )
        elif class_level == "claude_pdf":
            where_clauses.append("papers.classifier_version LIKE 'llm-pdf-reclassify-%'")
        elif class_level == "manual":
            where_clauses.append(
                "(papers.expert_locked_fields IS NOT NULL AND papers.expert_locked_fields != '[]' AND papers.expert_locked_fields != '')"
            )
        elif class_level == "optimal":
            where_clauses.append(
                "(papers.classifier_version LIKE 'llm-pdf-reclassify-%' OR (papers.expert_locked_fields IS NOT NULL AND papers.expert_locked_fields != '[]' AND papers.expert_locked_fields != ''))"
            )
        elif class_level == "maude":
            where_clauses.append("papers.classifier_version LIKE 'maude-%'")

    content_tier = filters.get("content_tier")
    if content_tier and content_tier not in ("any", "all"):
        import content_tiers

        tier_clause, tier_params = content_tiers.content_tier_sql_clause(content_tier)
        if tier_clause:
            where_clauses.append(tier_clause)
            params.extend(tier_params)

    return where_clauses, params


def apply_sort_sql(
    sql: str,
    filters: Dict[str, Any],
    *,
    dialect: DialectSQL,
    query_val: Any,
) -> str:
    """Append ORDER BY for catalog list queries."""
    sort_by = filters.get("sort_by")
    sort_dir = filters.get("sort_dir", "DESC").upper()
    if sort_dir not in ("ASC", "DESC"):
        sort_dir = "DESC"
    collate = dialect.collate_c()

    sort_map = {
        "year": f" ORDER BY papers.year {sort_dir}, papers.id DESC",
        "citations": f" ORDER BY papers.citation_count {sort_dir}, papers.year DESC",
        "title": f" ORDER BY papers.title{collate} {sort_dir}, papers.year DESC",
        "duration": f" ORDER BY papers.duration_days {sort_dir}, papers.year DESC",
        "study_type": f" ORDER BY papers.study_type{collate} {sort_dir}, papers.year DESC",
        "exposure_method": f" ORDER BY papers.exposure_method{collate} {sort_dir}, papers.year DESC",
        "population_age": f" ORDER BY papers.population_age{collate} {sort_dir}, papers.year DESC",
        "population_sex": f" ORDER BY papers.population_sex{collate} {sort_dir}, papers.year DESC",
        "publication_type": f" ORDER BY papers.publication_type{collate} {sort_dir}, papers.year DESC",
        "cannabis_type": f" ORDER BY papers.cannabis_type{collate} {sort_dir}, papers.year DESC",
        "outcome_domain": f" ORDER BY papers.outcome_domain{collate} {sort_dir}, papers.year DESC",
        "dose_mg": f" ORDER BY papers.dose_mg {sort_dir}, papers.year DESC",
        "puff_count": f" ORDER BY papers.puff_count {sort_dir}, papers.year DESC",
        "administration_frequency": (
            f" ORDER BY papers.administration_frequency{collate} {sort_dir}, papers.year DESC"
        ),
        "thc_mg_ml": f" ORDER BY papers.thc_mg_ml {sort_dir}, papers.year DESC",
        "cbd_mg_ml": f" ORDER BY papers.cbd_mg_ml {sort_dir}, papers.year DESC",
        "thc_mg_kg": f" ORDER BY papers.thc_mg_kg {sort_dir}, papers.year DESC",
        "cbd_mg_kg": f" ORDER BY papers.cbd_mg_kg {sort_dir}, papers.year DESC",
        "thc_uM": f" ORDER BY papers.thc_uM {sort_dir}, papers.year DESC",
        "cbd_uM": f" ORDER BY papers.cbd_uM {sort_dir}, papers.year DESC",
        "treatment_duration": (
            f" ORDER BY papers.treatment_duration{collate} {sort_dir}, papers.year DESC"
        ),
        "strain_reported": f" ORDER BY papers.strain_reported{collate} {sort_dir}, papers.year DESC",
        "strain_normalized": (
            f" ORDER BY papers.strain_normalized{collate} {sort_dir}, papers.year DESC"
        ),
    }
    extra = sort_map.get(sort_by)
    if extra:
        return sql + extra
    if query_val:
        return sql + " ORDER BY rank ASC"
    return sql + " ORDER BY papers.year DESC, papers.id DESC"
