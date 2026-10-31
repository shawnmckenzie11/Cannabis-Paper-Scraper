"""Compute reporting completeness from a methods record's `reporting` block (contract v0.7).

Completeness is derived, never stored. Output is per tier; there is no combined score.
Universal grade: gold / near_gold (something not_reported) / unverified (nothing missing, something unchecked).
Rule: schemas/completeness_rules.json (pending_biomedical).
"""
import hashlib
import json
import logging
import numbers
import pathlib

log = logging.getLogger(__name__)

RULES_PATH = pathlib.Path(__file__).resolve().parent / "schemas" / "completeness_rules.json"
CONDITION_LIST_PATH = RULES_PATH.parent / "condition_list.json"


def condition_list_version(path=CONDITION_LIST_PATH):
    return json.loads(pathlib.Path(path).read_text())["list_version"]


def load_rule(path=RULES_PATH):
    return json.loads(pathlib.Path(path).read_text())


def _status(reporting, key):
    return (reporting.get(key) or {}).get("status", "not_yet_assessed")


def signoff_set_hash(reference_links):
    """sha256 of the active (not revoked) paper_reference_link rows, order-independent."""
    active = sorted(json.dumps(l, sort_keys=True, default=str)
                    for l in (reference_links or []) if not l.get("revoked_at"))
    return hashlib.sha256(json.dumps(active).encode()).hexdigest()


def _apply_references(rep, reference_links):
    """P1: a numeric signed-off value satisfies a not_reported item (value_source=signed_off).

    Non-numeric signed-off values follow option A: no tier effect, only noted.
    The paper's own status is never overwritten in the record; this works on a copy.
    """
    rep = dict(rep)
    ref_fields, noted = [], []
    for l in reference_links or []:
        if l.get("revoked_at"):
            continue
        k = l["field"]
        if _status(rep, k) != "not_reported":
            continue
        v = l.get("value")
        if isinstance(v, numbers.Number) and not isinstance(v, bool):
            rep[k] = {"status": "reported", "location": None, "value_source": "signed_off"}
            if k not in ref_fields:
                ref_fields.append(k)
        elif k not in noted:
            noted.append(k)
    return rep, ref_fields, [k for k in noted if k not in ref_fields]


CANNABINOID_FIELD_MARKERS = ("thc", "cbd")


def _measured_cannabinoids(fields):
    """Default 6/8: entries in Pipeline's fields.cannabinoids with a numeric amount and a unit.

    A number without units is not measured (TOP3, missing_units). Raw fields are only read.
    """
    c = (fields or {}).get("cannabinoids") or {}
    if c.get("missingness") != "reported":
        return []
    out = []
    for e in c.get("value") or []:
        a = e.get("amount")
        if isinstance(a, numbers.Number) and not isinstance(a, bool) and e.get("unit"):
            out.append(e.get("name"))
    return out


def _field_status(rep, fields, f):
    """Status of one flowchart field: the reporting item if present, else Pipeline's per-field missingness."""
    if f in rep:
        return _status(rep, f)
    m = ((fields or {}).get(f) or {}).get("missingness")
    if m == "reported":
        return "reported"
    if m in ("not_reported", "could_not_check", "not_applicable"):
        return m
    return "not_yet_assessed"


def _apply_tier_defaults(rep, record, rule):
    """Approved defaults 3, 6, 8 (Biomedical lead, 2026-10-31), applied at read time on a copy.

    Default 8: major_cannabinoids counts as reported when any cannabinoid content was measured.
    Pipeline's own status and reason codes are kept; never applied over an explicit
    not_reported_reason other than absent, nor when nothing was measured.
    """
    rep = dict(rep)
    applied = []
    measured = _measured_cannabinoids(record.get("fields"))
    item = [i for i in rule["tiers"]["universal"]["items"] if i["key"] == "major_cannabinoids"]
    if item and item[0].get("satisfied_by") == "any_measured_cannabinoid" and measured:
        cur = rep.get("major_cannabinoids") or {}
        reason = cur.get("not_reported_reason")
        if cur.get("status", "not_yet_assessed") in ("not_reported", "not_yet_assessed") and reason in (None, "absent"):
            rep["major_cannabinoids"] = {"status": "reported", "location": cur.get("location"),
                                         "derived_from": "fields.cannabinoids", "default": 8}
            applied.append({"default": 8, "item": "major_cannabinoids", "measured": measured})
    return rep, applied, measured


def _reason(rep, fields, f):
    """Pipeline's not_reported_reason for one flowchart field (reporting item first, then fields)."""
    if f in rep:
        return (rep.get(f) or {}).get("not_reported_reason")
    return ((fields or {}).get(f) or {}).get("not_reported_reason")


def route_fields_status(record, study_type, rule, rep=None, measured=None):
    """Defaults 3 + 6: a route group is reported if ANY one of its flowchart fields is reported,
    or (for groups with cannabinoid fields) any measured cannabinoid, for every route.
    Returns None when the route is not reported or has no requirement."""
    rr = route_requirements(record, study_type, rule)
    if not rr["route_reported"] or not rr["expected_fields"]:
        return None
    rep = rep if rep is not None else (record.get("reporting") or {})
    fields = record.get("fields") or {}
    measured = measured if measured is not None else _measured_cannabinoids(fields)
    sts = {f: _field_status(rep, fields, f) for f in rr["expected_fields"]}
    hit = [f for f, st in sts.items() if st == "reported"]
    if hit:
        return {"status": "reported", "satisfied_by": hit[0], "default": 3}
    # v0.7.2 (ruling 3): default 6 is per-field, like default 8. A field with an explicit
    # not_reported_reason other than absent (e.g. missing_units, cited_elsewhere) fails only that
    # field; default 6 can still satisfy the group via its other cannabinoid fields.
    eligible = [f for f in sts if _reason(rep, fields, f) in (None, "absent")
                and any(m in f for m in CANNABINOID_FIELD_MARKERS)]
    if measured and eligible:
        return {"status": "reported", "satisfied_by": "fields.cannabinoids", "default": 6}
    if any(st == "could_not_check" for st in sts.values()):
        whys = []
        for f, st in sts.items():
            if st != "could_not_check":
                continue
            r = ((rep.get(f) or {}).get("check_failure_reason")
                 or (((fields or {}).get(f) or {}).get("check_failure_reason")))
            if r and r not in whys:
                whys.append(r)
        why = next(iter(whys), None)
        return {"status": "could_not_check", "satisfied_by": None, "check_failure_reason": why,
                "check_failure_reasons": whys}
    if all(st == "not_yet_assessed" for st in sts.values()):
        return {"status": "not_yet_assessed", "satisfied_by": None}
    return {"status": "not_reported", "satisfied_by": None}


def compute(record, study_type, rule=None, secondary_cutoff=None, reference_links=None):
    """Return {rule_id, rule_version, universal, study_type, good_practice, reference_fields, signoff_set_hash}.

    Computed at read time, never stored.
    """
    rule = rule or load_rule()
    cut_used = secondary_cutoff if secondary_cutoff is not None else rule["secondary_cutoff"]["default"]
    meta = {"secondary_cutoff": cut_used, "signoff_set_hash": signoff_set_hash(reference_links),
            "condition_list_version": condition_list_version()}

    def flat(grade, reason):
        g = {"grade": grade, "reason": reason}
        if grade == "not_scored":
            g["not_scored"] = True
        return dict({"rule_id": rule["rule_id"], "rule_version": rule["version"],
                     "universal": g, "study_type": g, "route_requirements": None, "good_practice": g,
                     "reference_fields": [], "tier_uses_reference_values": False,
                     "missing_reference_available": [], "defaults_applied": []}, **meta)

    if study_type in rule.get("not_scored_study_types", []):
        return flat("not_scored", "study_type_" + study_type)
    if study_type == "clinical":
        r = (((record.get("fields") or {}).get("randomized") or {}).get("value"))
        gate = rule["tiers"]["clinical"]["scope_gate"].get(r or "absent", "not_yet_assessed")
        if gate != "scored":
            return flat(gate, "randomized_" + (r or "absent"))
    rep, ref_fields, ref_noted = _apply_references(record.get("reporting") or {}, reference_links)
    rep, defaults_applied, measured = _apply_tier_defaults(rep, record, rule)
    tiers = rule["tiers"]

    def counts(items):
        c = {"reported": 0, "not_reported": 0, "could_not_check": 0, "not_applicable": 0, "not_yet_assessed": 0}
        for i in items:
            c[_status(rep, i["key"])] += 1
        applicable = c["reported"] + c["not_reported"]
        return c, (c["reported"] / applicable if applicable else None)

    uitems = tiers["universal"]["items"]
    uc, _ = counts(uitems)
    lists = {"missing": [], "not_checked": [], "not_yet_assessed": [], "not_applicable": []}
    for i in uitems:
        r = rep.get(i["key"]) or {}
        st = r.get("status", "not_yet_assessed")
        if study_type in i.get("not_applicable_for", []):
            st = "not_applicable"
        if st == "not_reported":
            lists["missing"].append(i["key"])
        elif st == "could_not_check":
            lists["not_checked"].append({"key": i["key"], "check_failure_reason": r.get("check_failure_reason")})
        elif st in ("not_yet_assessed", "not_applicable"):
            lists[st].append(i["key"])
    rfs = route_fields_status(record, study_type, rule, rep, measured)
    if rfs and rfs.get("default"):
        defaults_applied.append({"default": rfs["default"], "item": "route_fields", "satisfied_by": rfs["satisfied_by"]})
    if rfs and rfs["status"] == "not_reported":
        lists["missing"].append("route_fields")
    elif rfs and rfs["status"] == "could_not_check":
        nc = {"key": "route_fields", "check_failure_reason": rfs.get("check_failure_reason")}
        if len(rfs.get("check_failure_reasons") or []) > 1:
            nc["check_failure_reasons"] = rfs["check_failure_reasons"]
        lists["not_checked"].append(nc)
    if uc["not_yet_assessed"] == len(uitems):
        grade = "not_yet_assessed"
    elif lists["missing"]:
        grade = "near_gold"
    elif lists["not_checked"] or lists["not_yet_assessed"]:
        grade = "unverified"
    else:
        grade = "gold"
    out = {"rule_id": rule["rule_id"], "rule_version": rule["version"],
           "universal": dict({"grade": grade, "counts": uc}, **lists),
           "study_type": None}
    if study_type in tiers and study_type not in ("universal", "good_practice", "classification"):
        tier = tiers[study_type]
        c, frac = counts(tier["items"])
        out["study_type"] = {"tier": study_type, "fraction": frac, "counts": c}
        if "required_always" in tier:
            out["study_type"].update(grade_required_secondary(rep, tier, cut_used))
    out["route_requirements"] = dict(route_requirements(record, study_type, rule), fields_status=rfs)
    out["defaults_applied"] = defaults_applied
    gc, _ = counts(tiers["good_practice"]["items"])
    out["good_practice"] = {"recorded_only": True, "counts": gc,
                            "reported": [i["key"] for i in tiers["good_practice"]["items"] if _status(rep, i["key"]) == "reported"]}
    out["reference_fields"] = ref_fields
    out["tier_uses_reference_values"] = bool(ref_fields)
    out["missing_reference_available"] = ref_noted
    out.update(meta)
    return out


def route_requirements(record, study_type, rule=None):
    """Fields the extraction flowchart expects for this study type + route (TOP2; route questions closed).

    Route not reported: no route-dependent requirement (route itself is already missing in universal).
    Approved defaults (Biomedical lead, 2026-10-31): routes in no_requirement_routes add no expected
    fields and no misses; any one of an item's flowchart fields counts (satisfied_by any_one_field);
    any measured cannabinoid counts for every route; one track per paper.
    """
    rule = rule or load_rule()
    rr = rule.get("route_requirements", {})
    st = ((record.get("reporting") or {}).get("route") or {}).get("status", "not_yet_assessed")
    routes = (((record.get("fields") or {}).get("route") or {}).get("value")) or []
    if st != "reported" or not routes:
        return {"route_reported": False, "expected_fields": [], "status": rr.get("status"),
                "satisfied_by": rr.get("satisfied_by"), "tracks_per_paper": tracks_per_paper(rule)}
    groups = rr.get("route_groups", {}).get(study_type, {})
    expected, matched = [], set()
    for g in groups.values():
        hit = set(routes) & set(g["routes"])
        if hit:
            matched |= hit
            expected += [f for f in g["fields"] if f not in expected]
    no_req = set(rr.get("no_requirement_routes", {}).get(study_type, []))
    return {"route_reported": True, "routes": routes, "expected_fields": expected,
            "no_requirement_routes": [r for r in routes if r in no_req and r not in matched],
            "unmapped_routes": [r for r in routes if r not in matched and r not in no_req],
            "status": rr.get("status"), "satisfied_by": rr.get("satisfied_by"),
            "tracks_per_paper": tracks_per_paper(rule)}


def tracks_per_paper(rule=None):
    """Approved default 9: one track per paper until Evaluation's dev counts are in."""
    return int(((rule or load_rule()).get("tracks") or {}).get("per_paper", 1))


UNCHECKED = ("could_not_check", "not_yet_assessed")


def grade_required_secondary(rep, tier, cutoff):
    """Grade a tier with required_always + secondary items (see completeness_rules.json computation)."""
    def look(i):
        r = rep.get(i["key"]) or {}
        return r.get("status", "not_yet_assessed"), r.get("check_failure_reason")

    missing, not_checked = [], []
    for i in tier["required_always"]:
        st, why = look(i)
        if st == "not_reported":
            missing.append(i["key"])
        elif st in UNCHECKED:
            not_checked.append({"key": i["key"], "check_failure_reason": why})
    reported = unchecked = applicable = 0
    sec_missing = []
    for i in tier["secondary"]:
        st, why = look(i)
        if st == "not_applicable":
            continue
        applicable += 1
        if st == "reported":
            reported += 1
        elif st == "not_reported":
            sec_missing.append(i["key"])
        else:
            unchecked += 1
            not_checked.append({"key": i["key"], "check_failure_reason": why})
    worst = reported / applicable if applicable else 1.0
    best = (reported + unchecked) / applicable if applicable else 1.0
    if missing:
        grade, reason = "near_gold", "required_not_reported"
    elif any(n["key"] in {i["key"] for i in tier["required_always"]} for n in not_checked):
        grade, reason = "unverified", "required_not_checked"
    elif worst >= cutoff:
        grade, reason = "gold", None
    elif best < cutoff:
        grade, reason = "near_gold", "secondary_share_too_low"
    else:
        grade, reason = "unverified", "secondary_share_depends_on_unchecked"
    return {"grade": grade, "grade_reason": reason, "missing": missing, "secondary_not_reported": sec_missing,
            "not_checked": not_checked, "secondary_share_worst": worst, "secondary_share_best": best,
            "secondary_cutoff": cutoff}


def topic_gate(topic_match):
    """B3: topic gate ON, tri-state.

    None (not yet assessed) -> topic_gate_pass None, not filtered, no score override.
    0 (assessed, not about cannabis) -> False, relevance score 0, filtered out.
    > 0 -> True. Only an explicit False is filtered out.
    """
    if topic_match is None:
        return {"topic_gate_pass": None, "score_override": None, "filtered_out": False}
    passed = topic_match > 0
    return {"topic_gate_pass": passed, "score_override": None if passed else 0, "filtered_out": not passed}


BLINDED_TOP_LEVEL = ("mesh_from_pubmed",)
BLINDED_FIELD_KEYS = ("legacy_llm_fullpdf",)


def labelling_export(record):
    """Copy of a record for annotation / scoring-label exports.

    Drops mesh_from_pubmed and every legacy_llm_fullpdf value: reviewers never see them while labelling,
    and they never become labels or test truth.
    """
    out = json.loads(json.dumps(record))
    for k in BLINDED_TOP_LEVEL:
        out.pop(k, None)
    return _strip_legacy(out)


def _is_legacy(node):
    return isinstance(node, dict) and node.get("value_source") in BLINDED_FIELD_KEYS


def _strip_legacy(node):
    """Recursively drop legacy keys and legacy-sourced dicts, including items inside lists (e.g. conditions)."""
    if isinstance(node, dict):
        return {k: _strip_legacy(v) for k, v in node.items() if k not in BLINDED_FIELD_KEYS and not _is_legacy(v)}
    if isinstance(node, list):
        return [_strip_legacy(v) for v in node if not _is_legacy(v)]
    return node


def legacy_backfill_attribution(row, rule=None):
    """Per-column value_source for one papers row under the legacy backfill rule (pure function, no DB).

    Returns {column: {value_source, source_attribution, classifier_version, classification_timestamp}}
    for non-null classification columns; {} when the row is not from the legacy full-PDF pass.
    """
    rule = (rule or load_rule())["legacy_backfill"]
    version = row.get("classifier_version") or ""
    if not version.startswith(rule["classifier_version_like"].rstrip("%")):
        return {}
    locked = row.get("expert_locked_fields") or []
    if isinstance(locked, str):
        try:
            locked = json.loads(locked)
        except ValueError:
            log.warning("expert_locked_fields is not valid JSON; using comma-split fallback")
            locked = [x.strip() for x in locked.split(",") if x.strip()]
    out = {}
    for col in rule["columns"]:
        if row.get(col) is None:
            continue
        out[col] = {"value_source": "expert" if col in locked else "legacy_llm_fullpdf",
                    "source_attribution": rule["source_attribution"],
                    "classifier_version": version,
                    "classification_timestamp": row.get("classification_timestamp")}
    return out
