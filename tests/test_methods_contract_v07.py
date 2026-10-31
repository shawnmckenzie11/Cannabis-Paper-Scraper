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
LOCATIONS = ["methods", "supplementary", "abstract", "discussion", "unknown"]
REASONS = ["fetch_failed", "parse_failed", "source_not_available", "budget_cap", "source_not_fetched"]


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
    assert not ok("field_reporting", rep("cited_prior_paper")) and not ok("field_reporting", rep("elsewhere_in_paper"))
    assert SCHEMA["$defs"]["check_failure_reason"]["enum"] == REASONS
    assert not ok("field_reporting", {"status": "partially_reported"})
    assert not ok("field_reporting", {"status": "reported", "location": "results"})


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
    assert res["universal"]["grade"] == "unverified" and res["universal"]["missing"] == [] and res["study_type"]["fraction"] is None


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
    assert set(TIERS["tiers"]) == {"classification", "universal", "in_vivo", "in_vitro", "clinical", "good_practice"}
    fields = SCHEMA["properties"]["fields"]["properties"]
    keys = SCHEMA["properties"]["reporting"]["properties"]
    for tier in TIERS["tiers"].values():
        for item in tier["items"]:
            assert item["key"] in keys
            assert item["fields"] and all(f in fields for f in item["fields"]), item
    assert {i["key"] for i in TIERS["tiers"]["universal"]["items"]} == {"strain_or_product_name", "supplier", "form", "major_cannabinoids", "route"}
    assert [i["key"] for i in TIERS["tiers"]["classification"]["items"]] == ["research_type"]
    assert not any("required_location" in i for t in TIERS["tiers"].values() for i in t["items"])
    assert set(TIERS["answered"]) >= {"U1", "U2", "TOP4"}
    assert {i["key"] for i in TIERS["tiers"]["in_vivo"]["items"]} == {"route", "dose", "frequency"}
    iv = TIERS["tiers"]["in_vitro"]
    assert [i["key"] for i in iv["required_always"]] == ["cell_line_or_tissue_source", "concentration", "exposure_time"]
    assert [i["key"] for i in iv["secondary"]] == ["species_of_origin", "vehicle_solvent", "replicates"]
    assert TIERS["secondary_cutoff"]["default"] == 0.80 and TIERS["secondary_cutoff"]["overridable"] is True
    assert {i["key"] for i in TIERS["tiers"]["clinical"]["items"]} == {"dose", "frequency", "duration"}
    assert {i["key"] for i in TIERS["tiers"]["good_practice"]["items"]} == {"storage"}


def test_completeness_is_computed_per_tier_without_combined_score():
    res = mc.compute(FULL_V07, "in_vivo", TIERS)
    assert set(res) == {"rule_id", "rule_version", "universal", "study_type", "route_requirements", "good_practice"}
    assert res["universal"]["grade"] == "gold"  # location (supplementary) is never a pass condition
    assert res["study_type"]["fraction"] == 0.5 and res["study_type"]["counts"]["could_not_check"] == 1
    assert res["good_practice"]["fraction"] == 1.0
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


def test_observational_and_self_reported_are_not_scored_on_any_tier():
    assert set(TIERS["not_scored_study_types"]) == {"observational", "self_reported"}
    for st in ("observational", "self_reported"):
        res = mc.compute(FULL_V07, st, TIERS)
        for tier in ("universal", "study_type", "good_practice"):
            assert res[tier]["not_scored"] is True
            assert "pass" not in res[tier] and "fraction" not in res[tier]


def test_as_previously_described_scored_not_reported_until_phd_answers():
    assert "not_reported" in TIERS["previously_described_policy"]
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"]["supplier"] = {"status": "not_reported", "location": None, "evidence_quote": "as previously described [12]"}
    assert whole(rec)
    u = mc.compute(rec, "in_vivo", TIERS)["universal"]
    assert u["grade"] == "near_gold" and u["missing"] == ["supplier"]
    assert "cited_reference" not in json.dumps(SCHEMA["$defs"]["field_reporting"])


def _u(reporting, study="in_vivo"):
    rec = copy.deepcopy(FULL_V07); rec["reporting"].update(reporting)
    assert whole(rec)
    return mc.compute(rec, study, TIERS)["universal"]


NR = {"status": "not_reported", "location": None}


def test_universal_gold_when_all_reported():
    u = _u({})
    assert u["grade"] == "gold" and u["missing"] == [] and u["not_checked"] == []


def test_universal_near_gold_lists_missing_and_not_checked_missing_takes_precedence():
    u = _u({"form": NR, "supplier": {"status": "could_not_check", "check_failure_reason": "source_not_fetched"}})
    assert u["grade"] == "near_gold" and u["missing"] == ["form"]
    assert u["not_checked"] == [{"key": "supplier", "check_failure_reason": "source_not_fetched"}]


def test_universal_unverified_when_only_unchecked():
    u = _u({"major_cannabinoids": {"status": "could_not_check", "check_failure_reason": "fetch_failed"}})
    assert u["grade"] == "unverified" and u["missing"] == []
    assert u["not_checked"] == [{"key": "major_cannabinoids", "check_failure_reason": "fetch_failed"}]


def test_source_not_fetched_distinct_from_fetch_failed():
    assert "source_not_fetched" in REASONS and "fetch_failed" in REASONS
    assert "deliberate scope" in SCHEMA["$defs"]["check_failure_reason"]["description"]


def test_supplier_not_applicable_for_clinical_never_missing():
    sup = [i for i in TIERS["tiers"]["universal"]["items"] if i["key"] == "supplier"][0]
    assert sup["fields"] == ["supplier"] and sup["not_applicable_for"] == ["clinical"]
    assert "supplier" not in [i["key"] for i in TIERS["tiers"]["clinical"]["items"]]
    u = _u({"supplier": NR}, study="clinical")
    assert u["grade"] == "gold" and u["missing"] == [] and u["not_applicable"] == ["supplier"]
    assert _u({"supplier": NR}, study="in_vivo")["grade"] == "near_gold"


def test_cannabinoid_vocabulary_names_synonyms_and_no_drug_names():
    voc = RULES["vocabularies"]["cannabinoid"]
    by = {x["value"]: x for x in voc["values"]}
    assert {"thc", "cbd", "cbg", "thca"} <= set(by)
    assert by["cbg"]["full_name"] == "cannabigerol" and by["cbd"]["full_name"] == "cannabidiol"
    assert {"Δ9-THCA", "THCA-A", "tetrahydrocannabinolic acid"} <= set(by["thca"]["synonyms"])
    assert {"Δ9-THC", "delta-9-THC"} <= set(by["thc"]["synonyms"])
    drugs = {x["value"] for x in RULES["vocabularies"]["product_substance"]["values"]}
    syns = {s.lower() for x in voc["values"] for s in x["synonyms"] + [x["full_name"]]}
    assert not (drugs & syns) and "dronabinol" not in syns
    assert all("tier" not in x and x["external_id"] is None for x in voc["values"])
    assert {"delta8_thc", "cbga", "cbl", "11_oh_thc"} <= set(by)
    assert voc["values_status"] == "locked" and voc["candidates_status"] == "pending_phd_not_scored"
    cand = {x["value"] for x in voc["candidates_pending_phd"]}
    assert cand == {"thc_cooh", "cbca", "thcva", "cbdva", "cbe", "cbt"} and not (cand & set(by))
    assert all(x["full_name"] and x["synonyms"] for x in voc["values"] + voc["candidates_pending_phd"])


def test_route_not_reported_is_missing_and_adds_no_route_requirements():
    rec = copy.deepcopy(FULL_V07); rec["reporting"]["route"] = NR
    res = mc.compute(rec, "in_vivo", TIERS)
    assert res["universal"]["grade"] == "near_gold" and "route" in res["universal"]["missing"]
    assert res["route_requirements"]["route_reported"] is False and res["route_requirements"]["expected_fields"] == []
    assert res["universal"]["missing"] == ["route"]
    assert TIERS["route_requirements"]["status"] == "provisional"


def test_route_requirements_follow_flowchart_and_fields_exist():
    fields = SCHEMA["properties"]["fields"]["properties"]
    for groups in TIERS["route_requirements"]["route_groups"].values():
        for g in groups.values():
            assert g["flowchart"] and all(f in fields for f in g["fields"])
            assert set(g["routes"]) <= {v["value"] for v in RULES["vocabularies"]["route"]["values"]}
    rr = mc.compute(FULL_V07, "in_vivo", TIERS)["route_requirements"]
    assert rr["route_reported"] and "dose_thc_mg_kg" in rr["expected_fields"] and "puff_count" not in rr["expected_fields"]
    rec = copy.deepcopy(FULL_V07); rec["fields"]["route"]["value"] = ["inhaled"]
    assert "puff_count" in mc.compute(rec, "clinical", TIERS)["route_requirements"]["expected_fields"]
    assert TIERS["route_requirements"]["open"]


IVR = {"cell_line_or_tissue_source": rep(), "concentration": rep(), "exposure_time": rep(),
       "species_of_origin": rep(), "vehicle_solvent": rep(), "replicates": rep()}
CNC = {"status": "could_not_check", "check_failure_reason": "source_not_fetched"}


def _iv(changes, cutoff=None):
    rec = copy.deepcopy(FULL_V07); rec["reporting"].update(IVR); rec["reporting"].update(changes)
    assert whole(rec)
    return mc.compute(rec, "in_vitro", TIERS, secondary_cutoff=cutoff)["study_type"]


def test_in_vitro_gold():
    s = _iv({})
    assert s["grade"] == "gold" and s["secondary_share_worst"] == 1.0 and s["secondary_cutoff"] == 0.80


def test_in_vitro_required_not_reported_is_near_gold_even_if_another_unchecked():
    s = _iv({"concentration": NR, "exposure_time": CNC})
    assert s["grade"] == "near_gold" and s["missing"] == ["concentration"]
    assert {"key": "exposure_time", "check_failure_reason": "source_not_fetched"} in s["not_checked"]


def test_in_vitro_required_unchecked_is_unverified():
    s = _iv({"exposure_time": CNC})
    assert s["grade"] == "unverified" and s["grade_reason"] == "required_not_checked" and s["missing"] == []


def test_in_vitro_secondary_share_too_low_is_near_gold():
    s = _iv({"species_of_origin": NR, "replicates": CNC})
    assert s["secondary_share_best"] < 0.8 and s["grade"] == "near_gold" and s["grade_reason"] == "secondary_share_too_low"


def test_in_vitro_secondary_depends_on_unchecked_is_unverified():
    s = _iv({"replicates": CNC})
    assert s["secondary_share_worst"] == pytest.approx(2 / 3) and s["secondary_share_best"] == 1.0
    assert s["grade"] == "unverified" and s["grade_reason"] == "secondary_share_depends_on_unchecked"


def test_in_vitro_cutoff_boundary_and_override():
    s = _iv({"replicates": NR})  # 2/3 secondary reported
    assert s["grade"] == "near_gold"
    assert _iv({"replicates": NR}, cutoff=2 / 3)["grade"] == "gold"  # worst == cutoff -> gold
    assert _iv({"replicates": NR}, cutoff=0.67)["grade"] == "near_gold"
    s = _iv({"replicates": {"status": "not_applicable"}, "species_of_origin": NR}, cutoff=0.5)
    assert s["secondary_share_worst"] == 0.5 and s["grade"] == "gold"  # not_applicable leaves the denominator
