"""Contract tests for methods schema v0.6 (wp1.6.0-draft): MCK-425 product/strain vocabulary (phd_to_confirm)."""
import copy, json, pathlib
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
RULES = json.loads((ROOT / "schemas/normalization_rules.json").read_text())
STATUSES = ["pending_biomedical", "approved", "unmapped", "needs_review"]


def ok(name, rec):
    s = dict(SCHEMA["$defs"][name], **{"$defs": SCHEMA["$defs"]})
    return jsonschema.Draft202012Validator(s).is_valid(rec)


def nf(raw, val, status="pending_biomedical", rule="mck425_product_origin", ver="0.1.0"):
    return {"raw_text": raw, "normalized_value": val, "normalization_status": status, "rule_id": rule, "vocabulary_version": ver}


ENTRY = {"origin": nf("plant-derived", "plant_derived"),
         "preparation": nf("whole flower", "whole_flower", rule="mck425_product_preparation"),
         "substance": dict(nf("Epidiolex", "epidiolex", rule="mck425_product_substance"), external_id=None)}
CHEMO = {"value": "I", "missingness": "reported", "cannabinoid_content_reported": True, "source_span": "THC 20%, CBD <1%"}

FULL_V06 = {
    "schema_version": "wp1.6.0-draft", "paper_id": 1, "record_id": "rec-fake-6",
    "provenance": {"schema_version": "0.6", "extractor_id": "fake-extractor", "model_id": None, "prompt_id": None,
                   "confidence": None, "source_tier": "pmc_oa_xml", "source_span": None, "human_override": False,
                   "reviewed_at": None},
    "fields": {
        "product": {"value": [ENTRY], "missingness": "reported"},
        "route": {"value": ["inhaled"], "missingness": "reported"},
        "strain_reported": {"value": "O.G. Kush", "missingness": "reported"},
        "strain_normalized": {"value": nf("O.G. Kush", "og kush", rule="mck425_strain_name_cleaning"), "missingness": "reported"},
        "chemotype_reported": CHEMO,
    },
}


def whole(rec):
    return jsonschema.Draft202012Validator(SCHEMA).is_valid(rec)


def test_version_bump():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["x-contract-version"] in ("0.6", "0.7")  # v0.7 is additive
    assert "wp1.6.0-draft" in SCHEMA["properties"]["schema_version"]["enum"]
    assert "wp1.5.0-draft" in SCHEMA["properties"]["schema_version"]["enum"]
    assert SCHEMA["$defs"]["extraction_run_manifest"]["properties"]["contract_version"]["enum"][:2] == ["0.5", "0.6"]


def test_full_v06_record_validates_against_whole_schema():
    assert whole(FULL_V06)
    assert not whole({k: x for k, x in FULL_V06.items() if k != "record_id"})


def test_v06_requires_new_product_and_strain_shape_but_older_records_keep_legacy():
    legacy = copy.deepcopy(FULL_V06)
    legacy["fields"]["product"] = {"value": ["CBD oil"], "missingness": "reported"}
    assert not whole(legacy)
    legacy["schema_version"] = "wp1.5.0-draft"
    legacy["fields"]["strain_normalized"] = {"value": "og kush", "missingness": "reported"}
    assert whole(legacy)


def test_raw_text_required_and_non_empty():
    rec = nf("x", "plant_derived"); del rec["raw_text"]
    assert not ok("normalized_field", rec)
    assert not ok("normalized_field", nf("", "plant_derived"))


def test_no_normalized_value_without_rule_id_and_version():
    for k in ("rule_id", "vocabulary_version"):
        rec = nf("plant-derived", "plant_derived"); del rec[k]
        assert not ok("normalized_field", rec)
        assert not ok("normalized_field", dict(nf("plant-derived", "plant_derived"), **{k: None}))


def test_unmapped_stays_raw_never_guessed():
    assert ok("normalized_field", {"raw_text": "mystery tincture", "normalized_value": None, "normalization_status": "unmapped"})
    assert not ok("normalized_field", nf("mystery tincture", "isolate", status="unmapped"))
    assert not ok("normalized_field", {"raw_text": "x", "normalized_value": None, "normalization_status": "approved"})


def test_normalization_status_enum():
    enum = SCHEMA["$defs"]["normalized_field"]["properties"]["normalization_status"]["enum"]
    assert enum == STATUSES == RULES["review_policy"]["normalization_status_enum"]
    for st in STATUSES:
        assert ok("normalized_field", nf("plant-derived", "plant_derived", status=st)) == (st != "unmapped")
    assert not ok("normalized_field", nf("plant-derived", "plant_derived", status="auto"))


def test_product_axis_enums_match_vocabulary():
    for name, voc in (("product_origin_field", "product_origin"), ("product_preparation_field", "product_preparation")):
        values = [x["value"] for x in RULES["vocabularies"][voc]["values"]]
        assert SCHEMA["$defs"][name]["allOf"][1]["properties"]["normalized_value"]["enum"] == values + [None]
        for x in values:
            assert ok(name, nf("raw", x))
        assert not ok(name, nf("raw", "extract"))
    assert [x["value"] for x in RULES["vocabularies"]["product_origin"]["values"]] == ["plant_derived", "synthetic", "biosynthetic"]
    assert [x["value"] for x in RULES["vocabularies"]["product_preparation"]["values"]] == [
        "whole_flower", "full_spectrum_extract", "broad_spectrum_extract", "isolate", "formulated_drug"]


def test_named_drugs_are_own_values_and_route_separate():
    subs = [x["value"] for x in RULES["vocabularies"]["product_substance"]["values"]]
    assert {"epidiolex", "nabilone", "dronabinol"} <= set(subs)
    assert not ok("product_entry", dict(ENTRY, route=nf("inhaled", "inhaled")))
    assert "route" in SCHEMA["properties"]["fields"]["properties"]


def test_external_id_slot_optional_and_empty():
    voc = RULES["vocabularies"]
    for name in ("product_origin", "product_preparation", "product_substance", "chemotype"):
        assert all("external_id" in x and x["external_id"] is None for x in voc[name]["values"])
    assert ok("normalized_field", dict(nf("a", "plant_derived"), external_id=None))
    assert "external_id" not in SCHEMA["$defs"]["normalized_field"]["required"]


def test_chemotype_only_with_reported_cannabinoid_content():
    assert ok("chemotype_reported_field", CHEMO)
    assert not ok("chemotype_reported_field", dict(CHEMO, cannabinoid_content_reported=False))
    assert not ok("chemotype_reported_field", {k: x for k, x in CHEMO.items() if k != "source_span"})
    assert not ok("chemotype_reported_field", dict(CHEMO, value="IV"))
    absent = {"value": None, "missingness": "not_in_available_source", "cannabinoid_content_reported": False}
    assert ok("chemotype_reported_field", absent)
    assert not ok("chemotype_reported_field", dict(absent, missingness="reported"))


def test_strain_normalized_carries_no_chemistry():
    props = SCHEMA["$defs"]["strain_normalized_field"]["properties"]
    assert "chemotype" not in json.dumps(props) and "cannabinoid" not in json.dumps(props)
    assert not ok("strain_normalized_field", {"value": nf("OG Kush", "og kush"), "missingness": "reported", "chemotype": "I"})
    assert ok("strain_normalized_field", {"value": None, "missingness": "not_in_available_source"})


def test_review_policy_n_placeholder_flagged():
    rp = RULES["review_policy"]
    assert rp["rare_raw_string_threshold_n"] == 3 and rp["n_is_placeholder"] is True
    assert "PLACEHOLDER" in rp["n_note"] and rp["status"] == "phd_to_confirm"


def test_everything_marked_phd_to_confirm_and_rules_not_approved():
    for name, voc in RULES["vocabularies"].items():
        if not name.startswith("_"):
            assert voc["status"] == "phd_to_confirm" and voc["version"]
    mck = [r for r in RULES["rules"] if r["id"].startswith("mck425_")]
    assert len(mck) == 5 and all(r["status"] == "pending_biomedical" and r["confirm"] == "phd_to_confirm" for r in mck)
    assert {r["vocabulary"] for r in mck} <= set(RULES["vocabularies"])


def test_run_event_field_enum_matches_fields_keys_v06():
    enum = set(SCHEMA["$defs"]["run_event"]["properties"]["field"]["enum"])
    assert "chemotype_reported" in enum
    assert enum == set(SCHEMA["properties"]["fields"]["properties"])
