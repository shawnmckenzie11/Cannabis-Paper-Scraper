"""Compute reporting completeness from a methods record's `reporting` block (contract v0.7).

Completeness is derived, never stored. Output is per tier; there is no combined score.
Rule: schemas/completeness_rules.json (pending_biomedical).
"""
import json
import pathlib

RULES_PATH = pathlib.Path(__file__).resolve().parent / "schemas" / "completeness_rules.json"


def load_rule(path=RULES_PATH):
    return json.loads(pathlib.Path(path).read_text())


def _status(reporting, key):
    return (reporting.get(key) or {}).get("status", "not_yet_assessed")


def compute(record, study_type, rule=None):
    """Return {rule_id, rule_version, universal, study_type, good_practice} for one record."""
    rule = rule or load_rule()
    if study_type in rule.get("not_scored_study_types", []):
        ns = {"not_scored": True, "reason": "study_type_" + study_type}
        return {"rule_id": rule["rule_id"], "rule_version": rule["version"],
                "universal": ns, "study_type": ns, "good_practice": ns}
    rep = record.get("reporting") or {}
    tiers = rule["tiers"]

    def counts(items):
        c = {"reported": 0, "not_reported": 0, "could_not_check": 0, "not_applicable": 0, "not_yet_assessed": 0}
        for i in items:
            c[_status(rep, i["key"])] += 1
        applicable = c["reported"] + c["not_reported"]
        return c, (c["reported"] / applicable if applicable else None)

    def universal_ok(i):
        r = rep.get(i["key"]) or {}
        if r.get("status") != "reported":
            return False
        return "required_location" not in i or r.get("location") == i["required_location"]

    uitems = tiers["universal"]["items"]
    uc, _ = counts(uitems)
    out = {"rule_id": rule["rule_id"], "rule_version": rule["version"],
           "universal": {"pass": all(universal_ok(i) for i in uitems), "counts": uc},
           "study_type": None}
    if study_type in tiers and study_type not in ("universal", "good_practice"):
        c, frac = counts(tiers[study_type]["items"])
        out["study_type"] = {"tier": study_type, "fraction": frac, "counts": c}
    gc, gfrac = counts(tiers["good_practice"]["items"])
    out["good_practice"] = {"fraction": gfrac, "counts": gc}
    return out
