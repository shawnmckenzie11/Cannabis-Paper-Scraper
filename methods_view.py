"""Read-only presentation helpers for the methods contract (#117, wp1.7.0-draft).

Nothing here computes a tier. No tier badge is rendered yet: the API cannot call
methods_completeness.compute() until a study-type mapping exists (contract gap, see PR #120).
Unknown route or value_source values are passed through with a neutral label instead of raising.
"""
import functools
import hashlib
import json
import logging
import pathlib

log = logging.getLogger(__name__)

SCHEMAS = pathlib.Path(__file__).resolve().parent / "schemas"
CONDITION_LIST_PATH = SCHEMAS / "condition_list.json"
CONDITION_ROLLUP_PATH = SCHEMAS / "condition_rollup.json"

VALUE_SOURCE_LABELS = {
    "paper": "From the paper",
    "signed_off": "Signed-off reference value",
    "legacy_llm_fullpdf": "Earlier AI classification, not reviewed",
    "expert": "Expert-entered",
}
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


def detail_view(record):
    """Plain data for the paper detail drawer. All strings are raw; the template escapes them."""
    record = record or {}
    rep = record.get("reporting") or {}
    duration = rep.get("duration") or {}
    return {
        "fields": [
            {"key": k, "value_source": s, "value_source_label": value_source_label(s)}
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

# Runtime pins for the JSON the app loads (published by Methods; match main @ e56de84).
# The CSV pins (list 40a9be45..., rollup 16e12f46...) live in condition_list.json["approval"];
# rebuilding the JSON from those CSVs is Methods' check, not this module's.
CONDITION_LIST_SHA256 = "4d62e3d632d7ba37ccb3a9fc7dd2d629fc880163aa2ef9437cc1ddcf2182a16c"
CONDITION_ROLLUP_SHA256 = "fe996d39dcd587300e460f330bc4139da96575cb238121853b170db2168512f9"


def _read_pinned(path, expected):
    """Bytes of `path` if present and its sha256 matches `expected`; else None (logged)."""
    try:
        raw = pathlib.Path(path).read_bytes()
    except OSError as exc:
        log.error("condition filter disabled: cannot read %s (%s)", path, exc.__class__.__name__)
        return None
    got = hashlib.sha256(raw).hexdigest()
    if got != expected:
        log.error("condition filter disabled: sha256 mismatch for %s (got %s, expected %s)", path, got, expected)
        return None
    return raw


@functools.lru_cache(maxsize=None)
def _load_conditions_cached(list_path, rollup_path):
    lraw = _read_pinned(list_path, CONDITION_LIST_SHA256)
    rraw = _read_pinned(rollup_path, CONDITION_ROLLUP_SHA256)
    if lraw is None or rraw is None:
        return None
    try:
        return json.loads(lraw), json.loads(rraw)
    except ValueError:
        log.error("condition filter disabled: condition JSON did not parse")
        return None


def load_conditions(list_path=CONDITION_LIST_PATH, rollup_path=CONDITION_ROLLUP_PATH):
    """(condition_list, rollup) after the sha256 check, loaded once; None disables the filter."""
    return _load_conditions_cached(str(list_path), str(rollup_path))


def condition_filter_enabled():
    return load_conditions() is not None


def _under(tree, parents):
    return any(tree == p or tree.startswith(p + ".") for p in parents)


def expand_condition(key, clist=None, rollup=None):
    """MeSH descriptor ids matched by picking `key`: itself, approved entries nested under it,
    and every rollup child mapped to it. Standalone study-design headings never fold into a parent."""
    if clist is None or rollup is None:
        loaded = load_conditions()
        if loaded is None:
            return set()
        clist, rollup = loaded
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


# --- record predicates ---------------------------------------------------------------------------
# Reference semantics only. Not wired into search: these move into SQL (repository/filters.py)
# before any search/count/CSV path uses them, so all three stay on one server-side rule.

def matches_filters(record, *, duration_categories=(), condition_keys=(), outcome_domains=(),
                    primary_outcome_only=False, clist=None, rollup=None):
    """OR within a facet, AND across facets. Unknown/absent values never match an active facet."""
    rep = (record or {}).get("reporting") or {}
    if duration_categories:
        if ((rep.get("duration") or {}).get("duration_category")) not in set(duration_categories):
            return False
    if condition_keys:
        if (clist is None or rollup is None) and not condition_filter_enabled():
            return True  # filter disabled (missing/mismatched condition JSON): facet ignored, logged at load
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
