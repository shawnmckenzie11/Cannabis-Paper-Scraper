"""Compute reporting completeness from a methods record's `reporting` block (contract v0.7).

Completeness is derived, never stored. Output is per tier; there is no combined score.
Universal grade: gold / near_gold (something not_reported) / unverified (nothing missing, something unchecked).
Rule: schemas/completeness_rules.json (pending_biomedical).
"""
import hashlib
import json
import numbers
import pathlib

RULES_PATH = pathlib.Path(__file__).resolve().parent / "schemas" / "completeness_rules.json"


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


def compute(record, study_type, rule=None, secondary_cutoff=None, reference_links=None):
    """Return {rule_id, rule_version, universal, study_type, good_practice, reference_fields, signoff_set_hash}.

    Computed at read time, never stored.
    """
    rule = rule or load_rule()
    cut_used = secondary_cutoff if secondary_cutoff is not None else rule["secondary_cutoff"]["default"]
    meta = {"secondary_cutoff": cut_used, "signoff_set_hash": signoff_set_hash(reference_links)}

    def flat(grade, reason):
        g = {"grade": grade, "reason": reason}
        if grade == "not_scored":
            g["not_scored"] = True
        return dict({"rule_id": rule["rule_id"], "rule_version": rule["version"],
                     "universal": g, "study_type": g, "route_requirements": None, "good_practice": g,
                     "reference_fields": [], "missing_reference_available": []}, **meta)

    if study_type in rule.get("not_scored_study_types", []):
        return flat("not_scored", "study_type_" + study_type)
    if study_type == "clinical":
        r = (((record.get("fields") or {}).get("randomized") or {}).get("value"))
        gate = rule["tiers"]["clinical"]["scope_gate"].get(r or "absent", "not_yet_assessed")
        if gate != "scored":
            return flat(gate, "randomized_" + (r or "absent"))
    rep, ref_fields, ref_noted = _apply_references(record.get("reporting") or {}, reference_links)
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
    out["route_requirements"] = route_requirements(record, study_type, rule)
    gc, _ = counts(tiers["good_practice"]["items"])
    out["good_practice"] = {"recorded_only": True, "counts": gc,
                            "reported": [i["key"] for i in tiers["good_practice"]["items"] if _status(rep, i["key"]) == "reported"]}
    out["reference_fields"] = ref_fields
    out["missing_reference_available"] = ref_noted
    out.update(meta)
    return out


def route_requirements(record, study_type, rule=None):
    """Fields the extraction flowchart expects for this study type + route (provisional, TOP2).

    Route not reported: no route-dependent requirement (route itself is already missing in universal).
    """
    rule = rule or load_rule()
    rr = rule.get("route_requirements", {})
    st = ((record.get("reporting") or {}).get("route") or {}).get("status", "not_yet_assessed")
    routes = (((record.get("fields") or {}).get("route") or {}).get("value")) or []
    if st != "reported" or not routes:
        return {"route_reported": False, "expected_fields": [], "status": rr.get("status")}
    groups = rr.get("route_groups", {}).get(study_type, {})
    expected, matched = [], set()
    for g in groups.values():
        hit = set(routes) & set(g["routes"])
        if hit:
            matched |= hit
            expected += [f for f in g["fields"] if f not in expected]
    return {"route_reported": True, "routes": routes, "expected_fields": expected,
            "unmapped_routes": [r for r in routes if r not in matched], "status": rr.get("status")}


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
