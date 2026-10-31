"""Contract tests for methods schema v0.7 (wp1.7.0-draft): per-field reporting block and computed completeness tiers."""
import copy, json, pathlib, re
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
RULES = json.loads((ROOT / "schemas/normalization_rules.json").read_text())
TIERS = json.loads((ROOT / "schemas/completeness_rules.json").read_text())
import sys; sys.path.insert(0, str(ROOT))
import methods_completeness as mc

STATUSES = ["reported", "not_reported", "could_not_check", "not_applicable", "not_yet_assessed"]
LOCATIONS = ["methods", "supplementary", "abstract", "discussion", "unknown", "table", "figure"]
REASONS = ["fetch_failed", "parse_failed", "source_not_available", "budget_cap", "source_not_fetched", "figure_not_read", "low_confidence_read"]


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
        "storage_temperature": {"value": "-20C", "missingness": "reported"},
        "randomized": {"value": "yes", "missingness": "reported"},
        "arms": {"value": "controlled", "missingness": "reported"},
        "blinding": {"value": "double_blind", "missingness": "reported"},
    },
    "reporting": {
        "research_type": rep(), "strain_or_product_name": rep(), "supplier": rep(), "form": rep(),
        "major_cannabinoids": rep("supplementary"), "route": rep(),
        "dose": {"status": "not_reported", "location": None, "evidence_quote": None},
        "frequency": {"status": "could_not_check", "check_failure_reason": "parse_failed"},
        "vehicle_solvent": {"status": "not_applicable"},
        "storage_temperature": rep(),
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
    for k in ("supplier", "form", "cannabinoids", "vehicle_solvent", "storage", "storage_temperature", "randomized", "arms", "blinding", "research_type"):
        del old["fields"][k]
    old["fields"]["route"]["value"] = ["inhaled"]
    assert whole(old)
    res = mc.compute(old, "in_vivo", TIERS)
    assert res["universal"]["counts"]["not_yet_assessed"] == 5 and res["universal"]["counts"]["not_reported"] == 0
    assert res["universal"]["grade"] == "not_yet_assessed" and res["universal"]["missing"] == [] and res["study_type"]["fraction"] is None


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
    iv = TIERS["tiers"]["in_vivo"]
    assert [i["key"] for i in iv["required_always"]] == ["route", "dose", "duration", "number_of_animals"]
    assert [i["key"] for i in iv["secondary"]] == ["species", "animal_strain", "sex", "age_or_weight", "frequency", "vehicle_solvent"]
    iv = TIERS["tiers"]["in_vitro"]
    assert [i["key"] for i in iv["required_always"]] == ["cell_line_or_tissue_source", "species_of_origin", "concentration", "exposure_time"]
    assert [i["key"] for i in iv["secondary"]] == ["vehicle_solvent", "replicates"]
    assert TIERS["secondary_cutoff"]["default"] == 0.80 and TIERS["secondary_cutoff"]["overridable"] is True
    assert [i["key"] for i in TIERS["tiers"]["clinical"]["required_always"]] == ["number_of_participants", "route", "dose", "frequency", "duration", "participant_age_or_sex", "diagnosis_or_condition", "comparison_group"]
    assert TIERS["tiers"]["clinical"]["secondary"] == [] and TIERS["tiers"]["clinical"]["applies_to"] == "clinical_rct"
    assert {i["key"] for i in TIERS["tiers"]["good_practice"]["items"]} == {"storage_temperature", "milled_ground", "protected_from_light"}


def test_completeness_is_computed_per_tier_without_combined_score():
    res = mc.compute(FULL_V07, "in_vivo", TIERS)
    assert set(res) == {"rule_id", "rule_version", "universal", "study_type", "route_requirements", "good_practice", "reference_fields", "missing_reference_available", "secondary_cutoff", "signoff_set_hash", "tier_uses_reference_values", "condition_list_version", "defaults_applied"}
    assert res["universal"]["grade"] == "gold"  # location (supplementary) is never a pass condition
    assert res["study_type"]["fraction"] == 0.5 and res["study_type"]["counts"]["could_not_check"] == 1
    assert res["good_practice"]["recorded_only"] is True and res["good_practice"]["reported"] == ["storage_temperature"]
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
    assert set(TIERS["not_scored_study_types"]) == {"observational", "self_reported", "review", "case_report"}
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
    assert TIERS["route_requirements"]["status"] == "approved"


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
    assert TIERS["route_requirements"]["open"] == []


def test_nine_approved_defaults_behaviour():
    rr = TIERS["route_requirements"]
    assert [d["id"] for d in rr["approved_defaults"]] == list(range(1, 10))
    assert all(d["approved"] == "Biomedical lead, 2026-10-31" for d in rr["approved_defaults"])
    assert rr["satisfied_by"] == "any_one_field" and rr["cannabinoids"] == "any_measured_counts_every_route"
    assert TIERS["tracks"]["per_paper"] == 1 and mc.tracks_per_paper(TIERS) == 1
    voc = json.loads((ROOT / "schemas/normalization_rules.json").read_text())["vocabularies"]["route"]
    vals = {x["value"] for x in voc["values"]}
    assert {"sublingual", "intranasal", "intratracheal"} <= vals
    assert {"smoke", "vapor"} <= vals and rr["no_requirement_routes"]["in_vitro"] == ["smoke", "vapor"]
    assert "no_requirement_pending_vocabulary" not in rr and "vapour" not in vals
    for st, route in (("in_vivo", "sublingual"), ("in_vivo", "intranasal"), ("in_vivo", "intratracheal"),
                      ("clinical", "sublingual"), ("clinical", "topical"), ("in_vitro", "smoke"), ("in_vitro", "vapor")):
        rec = copy.deepcopy(FULL_V07)
        rec["fields"]["route"] = {"value": [route], "missingness": "reported"}
        if st == "clinical":
            rec["fields"]["randomized"] = {"value": "yes", "missingness": "reported"}
        out = mc.route_requirements(rec, st, TIERS)
        assert out["expected_fields"] == [] and out["unmapped_routes"] == [] and out["no_requirement_routes"] == [route]
        assert out["tracks_per_paper"] == 1
    # prospective / retrospective clinical (non-randomized) is not scored for gold
    rec = copy.deepcopy(FULL_V07); rec["fields"]["randomized"] = {"value": "no", "missingness": "reported"}
    assert mc.compute(rec, "clinical", TIERS)["universal"]["grade"] == "not_scored"
    mc_item = [i for i in TIERS["tiers"]["universal"]["items"] if i["key"] == "major_cannabinoids"][0]
    assert mc_item["satisfied_by"] == "any_measured_cannabinoid"


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
    s = _iv({"vehicle_solvent": NR, "replicates": CNC})
    assert s["secondary_share_best"] < 0.8 and s["grade"] == "near_gold" and s["grade_reason"] == "secondary_share_too_low"


def test_in_vitro_secondary_depends_on_unchecked_is_unverified():
    s = _iv({"replicates": CNC})
    assert s["secondary_share_worst"] == 0.5 and s["secondary_share_best"] == 1.0
    assert s["grade"] == "unverified" and s["grade_reason"] == "secondary_share_depends_on_unchecked"


def test_in_vitro_cutoff_boundary_and_override():
    s = _iv({"replicates": NR})  # 1/2 secondary reported
    assert s["grade"] == "near_gold"
    assert _iv({"replicates": NR}, cutoff=0.5)["grade"] == "gold"  # worst == cutoff -> gold
    assert _iv({"replicates": NR}, cutoff=0.51)["grade"] == "near_gold"
    s = _iv({"replicates": {"status": "not_applicable"}})
    assert s["secondary_share_worst"] == 1.0 and s["grade"] == "gold"  # not_applicable leaves the denominator


def test_a10_in_vitro_source_species_required_and_system_type_recorded_only():
    s = _iv({"species_of_origin": NR})
    assert s["grade"] == "near_gold" and s["missing"] == ["species_of_origin"]
    assert _iv({"species_of_origin": CNC})["grade"] == "unverified"
    f = SCHEMA["properties"]["fields"]["properties"]["system_type"]
    assert f["properties"]["value"]["enum"] == ["primary", "cell_line", "co_culture", "organoid", "pcls", "other", None]
    assert "A10" in TIERS["answered"] and "animal models only" in TIERS["answered"]["A10"]
    assert all(i["key"] != "system_type" for t in TIERS["tiers"].values() for i in t.get("items", []))


CLR = {k: rep() for k in ("number_of_participants", "route", "dose", "frequency", "duration", "participant_age_or_sex", "diagnosis_or_condition", "comparison_group")}


def _cl(changes):
    rec = copy.deepcopy(FULL_V07); rec["reporting"].update(CLR); rec["reporting"].update(changes)
    assert whole(rec)
    return mc.compute(rec, "clinical", TIERS)["study_type"]


def test_clinical_gold_all_required_no_share():
    s = _cl({})
    assert s["grade"] == "gold" and s["secondary_share_worst"] == 1.0


def test_clinical_any_required_not_reported_is_near_gold():
    s = _cl({"comparison_group": NR})
    assert s["grade"] == "near_gold" and s["missing"] == ["comparison_group"]


def test_clinical_required_unchecked_is_unverified():
    s = _cl({"dose": {"status": "could_not_check", "check_failure_reason": "parse_failed"}})
    assert s["grade"] == "unverified" and s["not_checked"] == [{"key": "dose", "check_failure_reason": "parse_failed"}]


def test_clinical_age_or_sex_single_item_and_open_questions():
    item = [i for i in TIERS["tiers"]["clinical"]["items"] if i["key"] == "participant_age_or_sex"][0]
    assert item["satisfied_by"] == "either" and set(item["fields"]) == {"sgba_population_age", "sgba_sex_reported"}
    assert set(TIERS["resolved"]) >= {"Q1", "Q2"} and "either" in TIERS["resolved"]["Q1"] and "not_scored" in TIERS["resolved"]["Q2"]


def test_comparison_group_confidence_and_needs_review():
    r = dict(rep(), confidence=0.4, needs_review=True)
    assert ok("field_reporting", r)
    assert not ok("field_reporting", dict(r, confidence=2))
    assert _cl({"comparison_group": r})["grade"] == "gold"
    assert "comparison_group" in SCHEMA["properties"]["fields"]["properties"]


def test_clinical_supplier_na_and_observational_still_not_scored():
    assert _u({"supplier": NR}, study="clinical")["missing"] == []
    assert mc.compute(FULL_V07, "observational", TIERS)["study_type"]["not_scored"] is True


GRADES = ["gold", "near_gold", "unverified", "not_scored", "out_of_scope", "not_yet_assessed"]


def test_grade_enum_explicit_never_blank():
    assert SCHEMA["$defs"]["tier_grade"]["enum"] == GRADES == TIERS["grade_enum"]
    assert SCHEMA["$defs"]["tier_grade"]["default"] == "not_yet_assessed"
    for st in ("observational", "self_reported", "review", "case_report"):
        assert mc.compute(FULL_V07, st, TIERS)["universal"]["grade"] == "not_scored"
    old = {"reporting": {}, "fields": {}}
    assert mc.compute(old, "in_vivo", TIERS)["universal"]["grade"] == "not_yet_assessed"
    for st in ("in_vivo", "in_vitro", "clinical"):
        res = mc.compute(FULL_V07, st, TIERS)
        assert res["universal"]["grade"] in GRADES


def _rand(v):
    rec = copy.deepcopy(FULL_V07); rec["reporting"].update(CLR)
    if v is None:
        del rec["fields"]["randomized"]
    else:
        rec["fields"]["randomized"] = {"value": v, "missingness": "reported" if v != "unknown" else "uncertain"}
    assert whole(rec)
    return mc.compute(rec, "clinical", TIERS)


def test_randomized_scope_gate():
    assert _rand("yes")["study_type"]["grade"] == "gold"
    no = _rand("no")
    assert all(no[t]["grade"] == "not_scored" for t in ("universal", "study_type", "good_practice"))
    assert TIERS["tiers"]["clinical"]["scope_gate"]["no"] == "not_scored"
    assert "comparison_group" not in json.dumps(no)
    assert _rand("unknown")["study_type"]["grade"] == "unverified"
    assert _rand("unknown")["study_type"]["reason"] == "randomized_unknown"
    assert _rand(None)["study_type"]["grade"] == "not_yet_assessed"


def test_arms_and_blinding_recorded_only():
    f = SCHEMA["properties"]["fields"]["properties"]
    assert "trial_design" not in f
    rec = copy.deepcopy(FULL_V07); rec["reporting"].update(CLR)
    rec["fields"]["arms"] = {"value": "single_arm", "missingness": "reported"}
    rec["fields"]["blinding"] = {"value": "open_label", "missingness": "reported"}
    assert whole(rec) and mc.compute(rec, "clinical", TIERS)["study_type"]["grade"] == "gold"
    rec["fields"]["blinding"]["value"] = "triple_blind"
    assert not whole(rec)


def test_storage_good_practice_never_affects_grade():
    rec = copy.deepcopy(FULL_V07)
    for k in ("storage_temperature", "milled_ground", "protected_from_light"):
        rec["reporting"][k] = NR
    a, b = mc.compute(rec, "in_vivo", TIERS), mc.compute(FULL_V07, "in_vivo", TIERS)
    assert a["universal"] == b["universal"] and a["study_type"] == b["study_type"]
    assert a["good_practice"]["recorded_only"] is True and "grade" not in a["good_practice"]


IVV_REQ = ("route", "dose", "duration", "number_of_animals")
IVV_SEC = ("species", "animal_strain", "sex", "age_or_weight", "frequency", "vehicle_solvent")


def _ivv(over=None, cutoff=None):
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"].update({k: rep() for k in IVV_REQ + IVV_SEC})
    rec["reporting"].update(over or {})
    assert whole(rec)
    return mc.compute(rec, "in_vivo", TIERS, secondary_cutoff=cutoff)["study_type"]


CNC = {"status": "could_not_check", "location": None, "check_failure_reason": "source_not_fetched"}


def test_in_vivo_v1_gold_and_outputs_both_shares():
    s = _ivv()
    assert s["grade"] == "gold" and s["secondary_share_worst"] == s["secondary_share_best"] == 1.0
    assert s["missing"] == [] and s["not_checked"] == [] and s["secondary_cutoff"] == 0.8


def test_in_vivo_required_not_reported_beats_unchecked():
    s = _ivv({"number_of_animals": NR, "dose": CNC})
    assert s["grade"] == "near_gold" and s["missing"] == ["number_of_animals"]
    assert s["not_checked"] == [{"key": "dose", "check_failure_reason": "source_not_fetched"}]


def test_in_vivo_required_unchecked_is_unverified():
    assert _ivv({"duration": CNC})["grade"] == "unverified"


def test_in_vivo_secondary_branches_and_cutoff_override():
    # 6 secondary: 1 missing -> worst 5/6 = 0.833 >= 0.80 gold
    assert _ivv({"sex": NR})["grade"] == "gold"
    # 2 missing -> 4/6 best < 0.80 near_gold
    assert _ivv({"sex": NR, "species": NR})["grade"] == "near_gold"
    # 1 missing + 1 unchecked -> worst 4/6, best 5/6 -> unverified
    s = _ivv({"sex": NR, "species": CNC})
    assert s["grade"] == "unverified" and s["secondary_not_reported"] == ["sex"]
    # override
    assert _ivv({"sex": NR, "species": NR}, cutoff=0.6)["grade"] == "gold"


def test_in_vivo_not_applicable_excluded_from_denominator():
    na = {"status": "not_applicable", "location": None}
    s = _ivv({"animal_strain": na, "age_or_weight": na, "sex": NR})
    assert s["secondary_share_worst"] == 0.75 and s["grade"] == "near_gold"


def test_comparison_group_extraction_confidence_and_review_reason():
    rec = copy.deepcopy(FULL_V07); rec["reporting"].update(CLR)
    rec["reporting"]["comparison_group"] = dict(rep(), extraction_confidence="low", needs_review=True,
                                                review_reason="arm_only_in_table_or_figure")
    assert whole(rec)
    for bad in ({"extraction_confidence": "very_low"}, {"review_reason": "unclear"},
                {"needs_review": False, "review_reason": "other"}):
        r2 = copy.deepcopy(rec); r2["reporting"]["comparison_group"].update(bad)
        assert not whole(r2)
    for rr in ("no_named_control_arm", "arm_only_in_table_or_figure", "other"):
        rec["reporting"]["comparison_group"]["review_reason"] = rr
        assert whole(rec)


def test_k1_location_enum_exact():
    assert SCHEMA["$defs"]["reporting_location"]["enum"] == ["methods", "supplementary", "abstract", "discussion", "unknown", "table", "figure"]


def test_locked_cannabinoids_exact():
    v = json.loads((ROOT / "schemas/normalization_rules.json").read_text())["vocabularies"]["cannabinoid"]
    assert {x["value"] for x in v["values"]} == {"thc", "cbd", "cbg", "cbn", "cbc", "thcv", "cbdv", "thca", "cbda",
                                                 "delta8_thc", "cbga", "cbl", "11_oh_thc"}
    assert {x["value"] for x in v["candidates_pending_phd"]} == {"thc_cooh", "cbca", "thcva", "cbdva", "cbe", "cbt"}
    assert all(x["full_name"] and x["synonyms"] for x in v["values"] + v["candidates_pending_phd"])


@pytest.mark.parametrize("why", ["figure_not_read", "low_confidence_read"])
def test_figure_reasons_are_could_not_check_never_a_miss(why):
    cnc = {"status": "could_not_check", "location": None, "check_failure_reason": why}
    s = _ivv({"dose": cnc})
    assert s["grade"] == "unverified" and s["missing"] == []
    assert s["not_checked"] == [{"key": "dose", "check_failure_reason": why}]
    u = _u({"form": cnc})
    assert u["grade"] == "unverified" and u["missing"] == []


def test_c1_dose_range_and_per_protocol_recorded():
    c1 = TIERS["answered"]["C1"]
    assert "range" in c1 and "per protocol" in c1.lower() and "not_reported" in c1


def test_c2_duration_category_label_never_affects_tier():
    vocab = json.loads((ROOT / "schemas/normalization_rules.json").read_text())["vocabularies"]["duration_category"]
    assert [v["value"] for v in vocab["values"]] == ["acute", "subacute", "subchronic", "chronic", "long_term", "other"]
    assert "single dose" in vocab["values"][0]["synonyms"] and "prolonged" in vocab["values"][4]["synonyms"]
    vague = {"status": "not_reported", "location": None, "duration_category": "chronic", "duration_category_verbatim": "chronic"}
    s = _ivv({"duration": vague})
    assert s["grade"] == "near_gold" and s["missing"] == ["duration"]
    timed = dict(rep(), duration_category="long_term", duration_category_verbatim="long-term")
    assert _ivv({"duration": timed}) == _ivv()
    rec = copy.deepcopy(FULL_V07); rec["reporting"]["duration"] = dict(vague, duration_category="very_long")
    assert not whole(rec)
    assert "C2" in TIERS["answered"]


def test_g1_good_practice_set_and_storage_duration_gone():
    f = SCHEMA["properties"]["fields"]["properties"]
    assert "storage_duration" not in f and "storage_light" not in f
    assert f["milled_ground"]["properties"]["value"]["enum"] == ["yes", "no", "not_reported", None]
    rec = copy.deepcopy(FULL_V07)
    rec["fields"]["milled_ground"] = {"value": "yes", "missingness": "reported"}
    rec["fields"]["protected_from_light"] = {"value": "no", "missingness": "reported"}
    assert whole(rec)


LINK = {"paper_id": "p1", "track_id": None, "field": "major_cannabinoids", "reference_value_id": "r1",
        "value": 22.5, "linked_at": "2026-10-31", "linked_by": "biomedical_lead:1", "revoked_at": None}


def test_reference_fields_numeric_signed_off_satisfies_item():
    rec = copy.deepcopy(FULL_V07); rec["reporting"]["major_cannabinoids"] = NR
    rec["fields"]["cannabinoids"] = {"value": None, "missingness": "not_reported"}  # nothing measured (default 8 off)
    plain = mc.compute(rec, "in_vivo", TIERS)
    assert plain["universal"]["grade"] == "near_gold" and plain["reference_fields"] == []
    res = mc.compute(rec, "in_vivo", TIERS, reference_links=[LINK])
    assert res["reference_fields"] == ["major_cannabinoids"] and res["universal"]["grade"] == "gold"
    assert rec["reporting"]["major_cannabinoids"] == NR  # paper status never overwritten
    assert res["signoff_set_hash"] != plain["signoff_set_hash"]


def test_reference_non_numeric_is_option_a_and_revoked_reverts():
    rec = copy.deepcopy(FULL_V07); rec["reporting"]["major_cannabinoids"] = NR
    rec["fields"]["cannabinoids"] = {"value": None, "missingness": "not_reported"}  # nothing measured (default 8 off)
    res = mc.compute(rec, "in_vivo", TIERS, reference_links=[dict(LINK, value="high THC")])
    assert res["universal"]["grade"] == "near_gold" and res["missing_reference_available"] == ["major_cannabinoids"]
    rev = mc.compute(rec, "in_vivo", TIERS, reference_links=[dict(LINK, revoked_at="2026-11-01")])
    assert rev["universal"]["grade"] == "near_gold" and rev["reference_fields"] == []
    assert rev["signoff_set_hash"] == mc.compute(rec, "in_vivo", TIERS)["signoff_set_hash"]


def test_signoff_hash_order_independent_and_cutoff_in_output():
    l2 = dict(LINK, reference_value_id="r2", field="form")
    assert mc.signoff_set_hash([LINK, l2]) == mc.signoff_set_hash([l2, LINK])
    assert mc.compute(FULL_V07, "in_vivo", TIERS, secondary_cutoff=0.6)["secondary_cutoff"] == 0.6


def test_top1_final_and_not_reported_reason():
    assert "final" in TIERS["answered"]["TOP1"] and not any("TOP1" in q for q in TIERS["open_questions"])
    reasons = ["cited_elsewhere", "vague_term_only", "protocol_without_number", "government_program_unnamed",
               "product_named_content_missing", "missing_units", "absent"]
    for why in reasons:
        rec = copy.deepcopy(FULL_V07)
        rec["reporting"]["dose"] = dict(NR, not_reported_reason=why)
        assert whole(rec)
    rec["reporting"]["dose"] = dict(NR, not_reported_reason="unknown_reason")
    assert not whole(rec)
    rec["reporting"]["dose"] = dict(rep(), not_reported_reason="absent")
    assert not whole(rec)
    a = _ivv({"dose": dict(NR, not_reported_reason="cited_elsewhere")})
    assert a == _ivv({"dose": NR})


def test_top3_number_without_units_recorded_as_absent():
    assert "not_reported" in TIERS["answered"]["TOP3"] and "missing_units" in TIERS["answered"]["TOP3"]
    assert "missing_units" in SCHEMA["$defs"]["field_reporting"]["properties"]["not_reported_reason"]["anyOf"][0]["enum"]


@pytest.mark.parametrize("key", ["strain_or_product_name", "supplier", "form", "major_cannabinoids"])
@pytest.mark.parametrize("study", ["in_vivo", "in_vitro"])
def test_top4_any_missing_universal_item_always_blocks_gold(key, study):
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"][key] = NR
    if key == "major_cannabinoids":
        rec["fields"]["cannabinoids"] = {"value": None, "missingness": "not_reported"}  # nothing measured (default 8 off)
    u = mc.compute(rec, study, TIERS)["universal"]
    assert u["grade"] == "near_gold" and key in u["missing"]


def test_t2_table_figure_locations_and_reads():
    for loc in ("table", "figure"):
        rec = copy.deepcopy(FULL_V07); rec["reporting"]["dose"] = rep(loc=loc)
        assert whole(rec)
    assert _ivv({"dose": rep(loc="figure")}) == _ivv()
    assert "T2" in TIERS["answered"]


def test_p2_supplier_program_verbatim_not_scored():
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"]["supplier"] = dict(NR, not_reported_reason="government_program_unnamed")
    rec["fields"]["supplier_program_verbatim"] = {"value": "NIDA Drug Supply Program", "missingness": "reported"}
    assert whole(rec)
    assert mc.compute(rec, "in_vivo", TIERS)["universal"]["missing"] == ["supplier"]


def test_p1_resolved_numeric_counts_flag():
    rec = copy.deepcopy(FULL_V07); rec["reporting"]["major_cannabinoids"] = NR
    assert mc.compute(rec, "in_vivo", TIERS, reference_links=[LINK])["tier_uses_reference_values"] is True
    assert mc.compute(rec, "in_vivo", TIERS)["tier_uses_reference_values"] is False
    assert "option B" in TIERS["answered"]["P1"]


def test_a11_conditions_list_approved_and_mesh_cross_check():
    cl = json.loads((ROOT / "schemas/condition_list.json").read_text())
    assert cl["status"] == "approved" and cl["list_version"] == "0.1.0" and cl["mesh_release_year"] == 2026
    assert cl["approval"]["approved_on"] == "2026-10-31" and len(cl["approval"]["approved_csv_sha256"]) == 64
    ents = cl["entries"]
    assert ents and len({e["key"] for e in ents}) == len(ents)
    assert all(re.fullmatch(r"D\d{6,9}", e["mesh_descriptor_id"]) and e["tree_numbers"] for e in ents)
    ids = {e["mesh_descriptor_id"] for e in ents}
    assert {"D004195", "D011297", "D001523"} <= ids
    owner = {t: e["mesh_descriptor_id"] for e in ents for t in e["tree_numbers"]}

    def covered(u, t):
        p = t.split(".")
        return any(owner.get(".".join(p[:i]), u) != u for i in range(1, len(p)))

    for e in ents:  # every kept non-exempt descriptor has at least one uncovered tree number
        if e["mesh_descriptor_id"] not in {"D004195", "D011297"}:
            assert not all(covered(e["mesh_descriptor_id"], t) for t in e["tree_numbers"]), e["key"]
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"]["conditions"] = [{"condition_key": "x", "mesh_descriptor_id": "D000001", "label": "x"}]
    rec["reporting"]["experimental_model_verbatim"] = "CFA-induced inflammatory pain"
    rec["mesh_from_pubmed"] = ["D000001"]
    assert whole(rec)
    rec["reporting"]["conditions"][0]["mesh_descriptor_id"] = "pain"
    assert not whole(rec)
    assert mc.compute(FULL_V07, "in_vivo", TIERS)["condition_list_version"] == cl["list_version"]


def test_a11_condition_rollup_maps_every_dropped_descriptor_to_approved_parent():
    cl = json.loads((ROOT / "schemas/condition_list.json").read_text())
    ro = json.loads((ROOT / "schemas/condition_rollup.json").read_text())
    approved = {e["mesh_descriptor_id"]: e for e in cl["entries"]}
    assert ro["list_version"] == cl["list_version"] and ro["mesh_release_year"] == 2026
    assert ro["source_csv_sha256"] == cl["approval"]["rollup_csv_sha256"]
    children = {}
    for r in ro["rows"]:
        assert r["child_mesh_descriptor_id"] not in approved
        par = approved[r["parent_mesh_descriptor_id"]]
        assert r["parent_tree_number"] in par["tree_numbers"]
        assert r["child_tree_number"].startswith(r["parent_tree_number"] + ".")
        children.setdefault(r["child_mesh_descriptor_id"], []).append(r)
    assert children and len(children) == cl["approval"]["counts"]["dropped"]
    assert all(len(v) >= 1 for v in children.values())


def test_a12_outcome_domains_exactly_one_primary():
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"]["outcome_domains"] = [{"domain": "pain", "is_primary": True}, {"domain": "sleep", "is_primary": False}]
    assert whole(rec)
    a = mc.compute(rec, "in_vivo", TIERS)
    for bad in ([{"domain": "pain", "is_primary": False}],
                [{"domain": "pain", "is_primary": True}, {"domain": "sleep", "is_primary": True}],
                [{"domain": "mood", "is_primary": True}]):
        rec["reporting"]["outcome_domains"] = bad
        assert not whole(rec)
    assert a["universal"] == mc.compute(FULL_V07, "in_vivo", TIERS)["universal"]


def test_a13_comparator_validated_default_false_no_tier_effect():
    f = SCHEMA["$defs"]["field_reporting"]["properties"]["comparator_validated"]
    assert f["default"] is False
    rec = copy.deepcopy(FULL_V07); rec["reporting"].update(CLR)
    rec["reporting"]["comparison_group"] = dict(rep(), comparator_validated=True)
    assert whole(rec) and mc.compute(rec, "clinical", TIERS)["study_type"]["grade"] == "gold"


def test_a15_group_sample_sizes_optional_no_tier_effect():
    base = _ivv()
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"]["group_sample_sizes"] = [{"group_label": "THC 10 mg/kg", "n": 8, "unit": "animals"}]
    assert whole(rec)
    rec["reporting"]["group_sample_sizes"][0]["n"] = "eight"
    assert not whole(rec)
    assert _ivv({"number_of_animals": NR})["grade"] == "near_gold" and base["grade"] == "gold"


def test_b3_relevance_topic_gate_on():
    g = SCHEMA["$defs"]["relevance_grade"]["x-topic-gate"]
    assert g["enabled"] is True and "score 0" in g["rule"] and "filtered out" in g["rule"]


def test_b3_topic_gate_pass_computed():
    assert mc.topic_gate(0) == {"topic_gate_pass": False, "score_override": 0, "filtered_out": True}
    assert mc.topic_gate(2) == {"topic_gate_pass": True, "score_override": None, "filtered_out": False}
    assert mc.topic_gate(None) == {"topic_gate_pass": None, "score_override": None, "filtered_out": False}
    assert "topic_gate_pass" in SCHEMA["$defs"]["relevance_grade"]["x-computed"]


def test_legacy_llm_fullpdf_side_by_side_never_scored_and_not_exported():
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"]["dose"] = dict(NR, legacy_llm_fullpdf={"value": "10 mg/kg", "status": "reported",
                                                            "value_source": "legacy_llm_fullpdf"})
    rec["mesh_from_pubmed"] = ["D010146"]
    assert whole(rec)
    assert _ivv({"dose": rec["reporting"]["dose"]})["grade"] == "near_gold"  # legacy never fills tier
    out = mc.labelling_export(rec)
    assert "mesh_from_pubmed" not in out and "legacy_llm_fullpdf" not in json.dumps(out)
    assert "legacy_llm_fullpdf" in json.dumps(rec)  # source record untouched
    bad = copy.deepcopy(FULL_V07); bad["reporting"]["dose"] = dict(rep(), value_source="legacy_llm_fullpdf")
    assert not whole(bad)
    assert SCHEMA["$defs"]["value_source"]["enum"] == ["paper", "signed_off", "legacy_llm_fullpdf", "expert"]


def test_labelling_export_strips_legacy_recursively_including_lists():
    rec = copy.deepcopy(FULL_V07)
    rec["reporting"]["conditions"] = [
        {"condition_key": "pain", "mesh_descriptor_id": "D010146", "label": "Pain"},
        {"condition_key": "x", "mesh_descriptor_id": "D000001", "label": "x", "value_source": "legacy_llm_fullpdf"},
        {"condition_key": "y", "mesh_descriptor_id": "D000002", "label": "y",
         "nested": [{"legacy_llm_fullpdf": {"value": 1}}, {"value_source": "legacy_llm_fullpdf"}]},
    ]
    out = mc.labelling_export(rec)
    assert "legacy_llm_fullpdf" not in json.dumps(out)
    assert [c["condition_key"] for c in out["reporting"]["conditions"]] == ["pain", "y"]
    assert out["reporting"]["conditions"][1]["nested"] == [{}]
    assert len(rec["reporting"]["conditions"]) == 3


def test_expert_locked_fields_invalid_json_warns(caplog):
    row = {"classifier_version": "llm-pdf-reclassify-2026-07", "species": "rat", "expert_locked_fields": "species, study_type"}
    with caplog.at_level("WARNING"):
        a = mc.legacy_backfill_attribution(row, TIERS)
    assert a["species"]["value_source"] == "expert"
    assert any("comma-split" in r.message for r in caplog.records)


def test_legacy_backfill_attribution_rule():
    row = {"classifier_version": "llm-pdf-reclassify-2026-07", "classification_timestamp": "2026-07-10T15:52:42Z",
           "study_type": "in_vivo", "species": "rat", "thc_mg_kg": 5.0, "cbd_pct": None, "thc_um": 3.0,
           "expert_locked_fields": '["species"]'}
    a = mc.legacy_backfill_attribution(row, TIERS)
    assert set(a) == {"study_type", "species", "thc_mg_kg", "thc_um"}  # null cbd_pct skipped
    assert a["study_type"]["value_source"] == "legacy_llm_fullpdf" and a["species"]["value_source"] == "expert"
    assert all(v["source_attribution"] == "inferred_from_row_version" for v in a.values())
    assert a["thc_mg_kg"]["classifier_version"] == row["classifier_version"]
    assert a["thc_mg_kg"]["classification_timestamp"] == row["classification_timestamp"]
    assert mc.legacy_backfill_attribution(dict(row, classifier_version="maude-2.6.0"), TIERS) == {}
    assert "expert" in SCHEMA["$defs"]["value_source"]["enum"]


# ---- v0.7.1: approved defaults 3, 6, 8 applied at read time ----

def _route_rec(route="oral_gavage", fields=None, reporting=None, cannabinoids=None):
    rec = copy.deepcopy(FULL_V07)
    rec["fields"]["route"] = {"value": [route], "missingness": "reported"}
    rec["reporting"]["route"] = rep()
    if cannabinoids is not None:
        rec["fields"]["cannabinoids"] = cannabinoids
    rec["fields"].update(fields or {})
    rec["reporting"].update(reporting or {})
    return rec


NOCANN = {"value": None, "missingness": "not_reported"}


def test_tier_rule_version_bumped_and_carried():
    assert TIERS["version"] == "0.7.1"
    assert mc.compute(FULL_V07, "in_vivo", TIERS)["rule_version"] == "0.7.1"
    assert mc.compute(FULL_V07, "in_vivo")["rule_version"] == "0.7.1"


def test_default3_any_one_flowchart_field_counts():
    exp = mc.route_requirements(_route_rec(), "in_vivo", TIERS)["expected_fields"]
    rec = _route_rec(reporting={f: NR for f in exp} | {"dose_thc_mg_kg": rep()}, cannabinoids=NOCANN)
    raw = copy.deepcopy(rec)
    out = mc.compute(rec, "in_vivo", TIERS)
    assert out["route_requirements"]["fields_status"] == {"status": "reported", "satisfied_by": "dose_thc_mg_kg", "default": 3}
    assert "route_fields" not in out["universal"]["missing"]
    assert rec == raw  # raw fields and statuses unchanged
    # Pipeline per-field missingness also counts when there is no reporting item
    rec2 = _route_rec(fields={"duration_days": {"value": 14, "missingness": "reported"}}, cannabinoids=NOCANN)
    assert mc.route_fields_status(rec2, "in_vivo", TIERS)["satisfied_by"] == "duration_days"
    # fail: none of the fields reported
    bad = _route_rec(reporting={f: NR for f in exp}, cannabinoids=NOCANN)
    o = mc.compute(bad, "in_vivo", TIERS)
    assert o["route_requirements"]["fields_status"]["status"] == "not_reported"
    assert "route_fields" in o["universal"]["missing"] and o["universal"]["grade"] == "near_gold"
    # unchecked (Pipeline reason code) -> not a miss, unverified
    unk = _route_rec(reporting={f: dict(status="could_not_check", location=None, check_failure_reason="figure_not_read") for f in exp}, cannabinoids=NOCANN)
    assert mc.route_fields_status(unk, "in_vivo", TIERS)["status"] == "could_not_check"
    ou = mc.compute(unk, "in_vivo", TIERS)
    assert ou["universal"]["grade"] == "unverified"
    # Pipeline's check_failure_reason propagates through route_fields (not dropped)
    assert mc.route_fields_status(unk, "in_vivo", TIERS)["check_failure_reason"] == "figure_not_read"
    assert {"key": "route_fields", "check_failure_reason": "figure_not_read"} in ou["universal"]["not_checked"]


def test_default6_any_measured_cannabinoid_every_route():
    meas = {"value": [{"name": "CBG", "amount": 2.0, "unit": "mg/kg", "source_span": "2 mg/kg CBG"}], "missingness": "reported", "source_span": "2 mg/kg CBG"}
    for st, route in (("in_vivo", "inhaled"), ("in_vivo", "oral_gavage"), ("in_vitro", "in_vitro_media"), ("clinical", "inhaled")):
        exp = mc.route_requirements(_route_rec(route), st, TIERS)["expected_fields"]
        rec = _route_rec(route, reporting={f: NR for f in exp}, cannabinoids=meas)
        assert mc.route_fields_status(rec, st, TIERS) == {"status": "reported", "satisfied_by": "fields.cannabinoids", "default": 6}, (st, route)
        # fail: amount without unit is not measured (TOP3)
        nounit = copy.deepcopy(meas); nounit["value"][0]["unit"] = None
        rec = _route_rec(route, reporting={f: NR for f in exp}, cannabinoids=nounit)
        assert mc.route_fields_status(rec, st, TIERS)["status"] == "not_reported", (st, route)


def test_default8_any_measured_cannabinoid_is_major():
    meas = {"value": [{"name": "THCV", "amount": 0.4, "unit": "%", "source_span": "0.4% THCV"}], "missingness": "reported", "source_span": "0.4% THCV"}
    rec = _route_rec(reporting={"major_cannabinoids": NR}, cannabinoids=meas)
    raw = copy.deepcopy(rec)
    out = mc.compute(rec, "in_vivo", TIERS)
    assert "major_cannabinoids" not in out["universal"]["missing"]
    assert {"default": 8, "item": "major_cannabinoids", "measured": ["THCV"]} in out["defaults_applied"]
    assert rec == raw and rec["reporting"]["major_cannabinoids"]["status"] == "not_reported"
    # fail: nothing measured
    none = {"value": [{"name": "THC", "amount": None, "unit": None}], "missingness": "reported", "source_span": "THC"}
    out = mc.compute(_route_rec(reporting={"major_cannabinoids": NR}, cannabinoids=none), "in_vivo", TIERS)
    assert "major_cannabinoids" in out["universal"]["missing"]
    # fail: Pipeline's explicit reason code is respected
    cited = dict(NR, not_reported_reason="cited_elsewhere")
    out = mc.compute(_route_rec(reporting={"major_cannabinoids": cited}, cannabinoids=meas), "in_vivo", TIERS)
    assert "major_cannabinoids" in out["universal"]["missing"]


@pytest.mark.parametrize("why", ["missing_units", "cited_elsewhere"])
def test_default6_does_not_override_explicit_reason(why):
    meas = {"value": [{"name": "CBG", "amount": 2.0, "unit": "mg/kg", "source_span": "2 mg/kg CBG"}], "missingness": "reported", "source_span": "2 mg/kg CBG"}
    exp = mc.route_requirements(_route_rec(), "in_vivo", TIERS)["expected_fields"]
    rec = _route_rec(reporting={f: dict(NR, not_reported_reason=why) for f in exp}, cannabinoids=meas)
    assert mc.route_fields_status(rec, "in_vivo", TIERS)["status"] == "not_reported"
    out = mc.compute(rec, "in_vivo", TIERS)
    assert out["universal"]["grade"] != "gold"
    assert "route_fields" in out["universal"]["missing"]
    assert not any(d.get("default") == 6 for d in out["defaults_applied"])
