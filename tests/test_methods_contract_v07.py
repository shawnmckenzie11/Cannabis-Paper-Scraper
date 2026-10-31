"""Contract tests for methods schema v0.7 (wp1.7.0-draft): per-field reporting block and computed completeness tiers."""
import copy, json, pathlib
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
RULES = json.loads((ROOT / "schemas/normalization_rules.json").read_text())
TIERS = json.loads((ROOT / "schemas/completeness_rules.json").read_text())
import sys; sys.path.insert(0, str(ROOT))
import methods_completeness as mc

STATUSES = ["reported", "not_reported", "could_not_check", "not_applicable", "not_yet_assessed"]
LOCATIONS = ["methods", "supplementary", "cited_prior_paper", "elsewhere_in_paper", "unknown"]
REASONS = ["fetch_failed", "parse_failed", "source_not_available", "budget_cap"]


def ok(name, rec):
    s = dict(SCHEMA["$defs"][name], **{"$defs": SCHEMA["$defs"]})
    return jsonschema.Draft202012Validator(s).is_valid(rec)


def whole(rec):
    return jsonschema.Draft202012Validator(SCHEMA).is_valid(rec)


def nf(raw, val, rule="mck425_product_origin"):
    return {"raw_text": raw, "normalized_value": val, "normalization_status": "pending_biomedical", "rule_id": rule, "vocabulary_version": "0.1.0"}


def rep(loc="methods", q="x"):
    return {"status": "reported", "location": loc, "evidence_quote": q}


FULL_V07 = {
    "schema_version": "wp1.7.0-draft", "paper_id": 1, "record_id": "rec-fake-7",
    "provenance": {"schema_version": "0.7", "extractor_id": "fake-extractor", "model_id": None, "prompt_id": None,
                   "confidence": None, "source_tier": "pmc_oa_xml", "source_span": None, "human_override": False, "reviewed_at": None},
    "fields": {
        "research_type": {"value": "in_vivo", "missingness": "reported"},
        "product": {"value": [{"origin": nf("plant", "plant_derived"),
                               "preparation": nf("flower", "whole_flower", rule="mck425_product_preparation")}], "missingness": "reported"},
        "strain_normalized": {"value": nf("OG Kush", "og kush", rule="mck425_strain_name_cleaning"), "missingness": "reported"},
        "supplier": {"value": "NIDA Drug Supply Program", "missingness": "reported"},
        "form": {"value": ["dried_flower"], "missingness": "reported"},
        "cannabinoids": {"value": [{"name": "THC", "amount": 5.9, "unit": "%", "source_span": "5.9% THC"}, {"name": "CBG", "amount": None, "unit": None}],
                         "missingness": "reported", "source_span": "5.9% THC, trace CBG"},
        "route": {"value": ["oral_gavage"], "missingness": "reported"},
        "vehicle_solvent": {"value": None, "missingness": "not_applicable"},
        "storage": {"value": "-20C, protected from light", "missingness": "reported"},
    },
    "reporting": {
        "research_type": rep(), "strain_or_product_name": rep(), "supplier": rep(), "form": rep(),
        "major_cannabinoids": rep("supplementary"), "route": rep(),
        "dose": {"status": "not_reported", "location": None, "evidence_quote": None},
        "frequency": {"status": "could_not_check", "check_failure_reason": "parse_failed"},
        "vehicle_solvent": {"status": "not_applicable"},
        "storage": rep(),
    },
}


def test_version_bump():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["x-contract-version"] == "0.7"
    assert {"wp1.6.0-draft", "wp1.7.0-draft"} <= set(SCHEMA["properties"]["schema_version"]["enum"])
    assert SCHEMA["$defs"]["extraction_run_manifest"]["properties"]["contract_version"]["enum"] == ["0.5", "0.6", "0.7"]
    assert TIERS["contract_version"] == "0.7"


def test_enums():
    assert SCHEMA["$defs"]["reporting_status"]["enum"] == STATUSES
    assert SCHEMA["$defs"]["reporting_location"]["enum"] == LOCATIONS
    assert SCHEMA["$defs"]["check_failure_reason"]["enum"] == REASONS
    assert not ok("field_reporting", {"status": "partially_reported"})
    assert not ok("field_reporting", {"status": "reported", "location": "abstract"})


def test_full_v07_record_validates():
    assert whole(FULL_V07)
    assert not whole({k: v for k, v in FULL_V07.items() if k != "record_id"})
    bad = copy.deepcopy(FULL_V07); bad["reporting"]["made_up"] = rep()
    assert not whole(bad)


def test_default_is_not_yet_assessed_and_existing_records_stay_valid():
    assert SCHEMA["$defs"]["reporting_status"]["default"] == "not_yet_assessed"
    assert ok("field_reporting", {"status": "not_yet_assessed"})
    old = copy.deepcopy(FULL_V07); del old["reporting"]; old["schema_version"] = "wp1.6.0-draft"
    for k in ("supplier", "form", "cannabinoids", "vehicle_solvent", "storage", "research_type"):
        del old["fields"][k]
    old["fields"]["route"]["value"] = ["inhaled"]
    assert whole(old)
    res = mc.compute(old, "in_vivo", TIERS)
    assert res["universal"]["counts"]["not_yet_assessed"] == 5 and res["universal"]["counts"]["not_reported"] == 0
    assert res["universal"]["pass"] is False and res["study_type"]["fraction"] is None


def test_location_only_when_reported():
    assert ok("field_reporting", rep())
    assert not ok("field_reporting", {"status": "reported"})
    assert not ok("field_reporting", {"status": "reported", "location": None})
    for st in STATUSES[1:]:
        extra = {"check_failure_reason": "fetch_failed"} if st == "could_not_check" else {}
        assert ok("field_reporting", dict({"status": st}, **extra))
        assert ok("field_reporting", dict({"status": st, "location": None}, **extra))
        assert not ok("field_reporting", dict({"status": st, "location": "methods"}, **extra))


def test_check_failure_reason_only_for_could_not_check():
    for r in REASONS:
        assert ok("field_reporting", {"status": "could_not_check", "check_failure_reason": r})
    assert not ok("field_reporting", {"status": "could_not_check"})
    assert not ok("field_reporting", {"status": "could_not_check", "check_failure_reason": None})
    assert not ok("field_reporting", {"status": "could_not_check", "check_failure_reason": "timeout"})
    assert ok("field_reporting", dict(rep(), check_failure_reason=None))
    for st in ("reported", "not_reported", "not_applicable", "not_yet_assessed"):
        r = rep() if st == "reported" else {"status": st}
        assert not ok("field_reporting", dict(r, check_failure_reason="fetch_failed"))


def test_tier_rule_is_valid_and_every_field_exists():
    assert TIERS["status"] == "pending_biomedical" and TIERS["rule_id"] and TIERS["version"]
    assert set(TIERS["tiers"]) == {"universal", "in_vivo", "in_vitro", "clinical", "good_practice"}
    fields = SCHEMA["properties"]["fields"]["properties"]
    keys = SCHEMA["properties"]["reporting"]["properties"]
    for tier in TIERS["tiers"].values():
        for item in tier["items"]:
            assert item["key"] in keys
            assert item["fields"] and all(f in fields for f in item["fields"]), item
    assert {i["key"] for i in TIERS["tiers"]["universal"]["items"]} == {"research_type", "strain_or_product_name", "supplier", "form", "major_cannabinoids"}
    assert {i["key"] for i in TIERS["tiers"]["in_vivo"]["items"]} == {"route", "dose", "frequency"}
    assert {i["key"] for i in TIERS["tiers"]["in_vitro"]["items"]} == {"vehicle_solvent", "dose"}
    assert {i["key"] for i in TIERS["tiers"]["clinical"]["items"]} == {"dose", "frequency", "duration"}
    assert {i["key"] for i in TIERS["tiers"]["good_practice"]["items"]} == {"storage"}


def test_completeness_is_computed_per_tier_without_combined_score():
    res = mc.compute(FULL_V07, "in_vivo", TIERS)
    assert set(res) == {"rule_id", "rule_version", "universal", "study_type", "good_practice"}
    assert res["universal"]["pass"] is False  # cannabinoids only in supplementary
    assert res["study_type"]["fraction"] == 0.5 and res["study_type"]["counts"]["could_not_check"] == 1
    assert res["good_practice"]["fraction"] == 1.0
    rec = copy.deepcopy(FULL_V07); rec["reporting"]["major_cannabinoids"] = rep()
    assert mc.compute(rec, "in_vivo", TIERS)["universal"]["pass"] is True
    assert "completeness" not in SCHEMA["properties"]


def test_new_fields_and_vocabularies():
    f = SCHEMA["properties"]["fields"]["properties"]
    assert {"supplier", "vehicle_solvent", "storage", "form", "cannabinoids", "route", "product"} <= set(f)
    assert "topical" in [v["value"] for v in RULES["vocabularies"]["product_form"]["values"]]
    routes = [v["value"] for v in RULES["vocabularies"]["route"]["values"]]
    assert {"oral_diet", "oral_gavage"} <= set(routes) and "oral" not in routes
    assert not ok("product_entry", dict(FULL_V07["fields"]["product"]["value"][0], route=nf("gavage", "oral_gavage")))
    for v in ("product_form", "route"):
        assert RULES["vocabularies"][v]["status"] == "phd_to_confirm"


def test_cannabinoids_list_gated_like_chemotype():
    good = FULL_V07["fields"]["cannabinoids"]
    assert ok("cannabinoids_field", good)
    assert not ok("cannabinoids_field", {k: v for k, v in good.items() if k != "source_span"})
    assert not ok("cannabinoids_field", dict(good, missingness="uncertain"))
    assert ok("cannabinoids_field", {"value": None, "missingness": "not_in_available_source"})
    assert ok("cannabinoids_field", {"value": [], "missingness": "not_in_available_source"})


def test_run_event_field_enum_matches_fields_keys_v07():
    assert set(SCHEMA["$defs"]["run_event"]["properties"]["field"]["enum"]) == set(SCHEMA["properties"]["fields"]["properties"])
