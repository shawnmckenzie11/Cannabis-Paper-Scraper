"""Read-only presentation helpers for the methods contract (#117, wp1.7.0-draft).

Nothing here computes a tier. Badges are read from `methods_completeness.compute(...)` output
(rule_version, secondary_cutoff, universal.grade, tier_uses_reference_values). Unknown tier,
route or value_source values are passed through with a neutral label instead of raising.
"""
import hashlib
import json
import pathlib

SCHEMAS = pathlib.Path(__file__).resolve().parent / "schemas"
CONDITION_LIST_PATH = SCHEMAS / "condition_list.json"
CONDITION_ROLLUP_PATH = SCHEMAS / "condition_rollup.json"

TIER_LABELS = {
    "gold": "Gold",
    "near_gold": "Near gold",
    "unverified": "Unverified",
    "not_scored": "Not scored",
    "out_of_scope": "Out of scope",
    "not_yet_assessed": "Not yet assessed",
}
GOLD_WITH_REFERENCE_LABEL = "gold, includes signed-off product values"

VALUE_SOURCE_LABELS = {
    "paper": "From the paper",
    "signed_off": "Signed-off reference value",
    "legacy_llm_fullpdf": "Earlier AI classification, not reviewed",
    "expert": "Expert-entered",
}
# Sources that may never count toward a badge (contract: legacy values are silver baseline only).
NON_BADGE_SOURCES = frozenset({"legacy_llm_fullpdf"})

DURATION_CATEGORIES = ("acute", "subacute", "subchronic", "chronic", "long_term", "other")
COMPARATOR_STATUS_LABEL = "Extracted, not yet validated"

# Approved rule: these study-design headings are their own entries and never fold into a parent.
STANDALONE_CONDITION_IDS = frozenset({"D004195", "D011297"})


def _label(value, labels):
    if value is None:
        return ""
    return labels.get(value) or str(value).replace("_", " ").capitalize()


def load_methods_record(paper):
    """Methods-contract record for a paper row, or None.

    Contract gap: #117 defines the record shape but nothing stores or serves it yet (no column,
    table or API field). Returns None until that lands, so CSV cells and the detail view stay blank.
    """
    return None


def tier_badge(computed):
    """Badge for one paper from compute() output. Returns None when there is no computed result."""
    if not computed:
        return None
    universal = computed.get("universal") or {}
    tier = universal.get("grade")
    if tier is None:
        tier = "not_yet_assessed"
    uses_ref = bool(computed.get("tier_uses_reference_values"))
    label = GOLD_WITH_REFERENCE_LABEL if (uses_ref and tier == "gold") else _label(tier, TIER_LABELS)
    return {
        "tier": tier,
        "known_tier": tier in TIER_LABELS,
        "label": label,
        "rule_version": computed.get("rule_version"),
        "cutoff": computed.get("secondary_cutoff"),
        "tier_uses_reference_values": uses_ref,
    }


def value_source_label(source):
    return _label(source or "paper", VALUE_SOURCE_LABELS)


def field_sources(record):
    """{reporting_key: value_source} for every reporting item that is a field_reporting dict."""
    out = {}
    for key, item in ((record or {}).get("reporting") or {}).items():
        if isinstance(item, dict) and "status" in item:
            out[key] = item.get("value_source") or "paper"
    return out


def comparator(record):
    """Comparator for the detail view only (not a filter). None when not present in the record."""
    rep = ((record or {}).get("reporting") or {}).get("comparison_group")
    fld = ((record or {}).get("fields") or {}).get("comparison_group")
    if not rep and not fld:
        return None
    rep = rep or {}
    return {
        "value": (fld or {}).get("value"),
        "status": rep.get("status"),
        "extraction_confidence": rep.get("extraction_confidence"),
        "confidence": rep.get("confidence"),
        "validated": bool(rep.get("comparator_validated")),
        "label": "Validated" if rep.get("comparator_validated") else COMPARATOR_STATUS_LABEL,
    }


def detail_view(record, computed=None):
    """Plain data for the paper detail drawer. All strings are raw; the template escapes them."""
    record = record or {}
    rep = record.get("reporting") or {}
    duration = rep.get("duration") or {}
    return {
        "badge": tier_badge(computed),
        "fields": [
            {"key": k, "value_source": s, "value_source_label": value_source_label(s),
             "counts_toward_badge": s not in NON_BADGE_SOURCES}
            for k, s in sorted(field_sources(record).items())
        ],
        "duration_category": duration.get("duration_category"),
        "conditions": [dict(c) for c in rep.get("conditions") or []],
        "outcome_domains": [dict(d) for d in rep.get("outcome_domains") or []],
        "experimental_model_verbatim": rep.get("experimental_model_verbatim"),  # free text only
        "comparator": comparator(record),
    }


CSV_COLUMNS = ("value_sources", "comparator_extraction_confidence", "comparator_confidence")


def csv_cells(record):
    """Extra CSV cells for one paper. Blank when no methods record is available."""
    if not record:
        return {c: "" for c in CSV_COLUMNS}
    comp = comparator(record) or {}
    conf = comp.get("confidence")
    return {
        "value_sources": json.dumps(field_sources(record), sort_keys=True),
        "comparator_extraction_confidence": comp.get("extraction_confidence") or "",
        "comparator_confidence": "" if conf is None else conf,
    }


# --- conditions -------------------------------------------------------------------------------

def _sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def verify_condition_sources(list_csv, rollup_csv, list_path=CONDITION_LIST_PATH):
    """Check the approved CSVs match the sha256 recorded in condition_list.json."""
    meta = json.loads(pathlib.Path(list_path).read_text())["approval"]
    return {
        "list": _sha256(list_csv) == meta["approved_csv_sha256"],
        "rollup": _sha256(rollup_csv) == meta["rollup_csv_sha256"],
    }


def load_conditions(list_path=CONDITION_LIST_PATH, rollup_path=CONDITION_ROLLUP_PATH):
    clist = json.loads(pathlib.Path(list_path).read_text())
    rollup = json.loads(pathlib.Path(rollup_path).read_text())
    return clist, rollup


def _under(tree, parents):
    return any(tree == p or tree.startswith(p + ".") for p in parents)


def expand_condition(key, clist=None, rollup=None):
    """MeSH descriptor ids matched by picking `key`: itself, approved entries nested under it,
    and every rollup child mapped to it. Standalone study-design headings never fold into a parent."""
    if clist is None or rollup is None:
        clist, rollup = load_conditions()
    entries = {e["key"]: e for e in clist["entries"]}
    entry = entries.get(key)
    if entry is None:
        return set()
    ids = {entry["mesh_descriptor_id"]}
    trees = entry.get("tree_numbers") or []
    for e in clist["entries"]:
        if e["mesh_descriptor_id"] in STANDALONE_CONDITION_IDS and e is not entry:
            continue
        if any(_under(t, trees) for t in e.get("tree_numbers") or []):
            ids.add(e["mesh_descriptor_id"])
    for row in rollup["rows"]:
        if row["parent_mesh_descriptor_id"] == entry["mesh_descriptor_id"] or _under(row["parent_tree_number"], trees):
            if row["child_mesh_descriptor_id"] not in STANDALONE_CONDITION_IDS:
                ids.add(row["child_mesh_descriptor_id"])
    return ids


# --- record predicates (same semantics the server filter must use) -----------------------------

def matches_filters(record, *, duration_categories=(), condition_keys=(), outcome_domains=(),
                    primary_outcome_only=False, clist=None, rollup=None):
    """OR within a facet, AND across facets. Unknown/absent values never match an active facet."""
    rep = (record or {}).get("reporting") or {}
    if duration_categories:
        if ((rep.get("duration") or {}).get("duration_category")) not in set(duration_categories):
            return False
    if condition_keys:
        wanted = set()
        for k in condition_keys:
            wanted |= expand_condition(k, clist, rollup)
        have = {c.get("mesh_descriptor_id") for c in rep.get("conditions") or []}
        if not (have & wanted):
            return False
    if outcome_domains:
        doms = rep.get("outcome_domains") or []
        have = {d.get("domain") for d in doms if d.get("is_primary") or not primary_outcome_only}
        if not (have & set(outcome_domains)):
            return False
    return True
