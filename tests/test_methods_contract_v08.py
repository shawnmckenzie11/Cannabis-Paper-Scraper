"""Contract tests for methods schema v0.8 (wp1.8.0-draft): technical follow-ups to v0.7 (all additive)."""
import copy, hashlib, json, pathlib
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
TIERS = json.loads((ROOT / "schemas/completeness_rules.json").read_text())
import sys; sys.path.insert(0, str(ROOT))
import methods_completeness as mc
import methods_view as mv
sys.path.insert(0, str(ROOT / "scripts"))
import build_condition_json as bcj
from test_methods_contract_v07 import FULL_V07

# Pins: source CSVs (as recorded in condition_list.json["approval"]) and the JSON built from them.
LIST_CSV_SHA256 = "40a9be45cab10ddd1dcdae7b479033cba632686468f834ba43eb846df05cadcc"
ROLLUP_CSV_SHA256 = "16e12f4629e55fd45ea48fc327051958f89f23b2a0305a83994135d48eedeb48"
LIST_JSON_SHA256 = "4d62e3d632d7ba37ccb3a9fc7dd2d629fc880163aa2ef9437cc1ddcf2182a16c"
ROLLUP_JSON_SHA256 = "fe996d39dcd587300e460f330bc4139da96575cb238121853b170db2168512f9"


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def whole(rec):
    return jsonschema.Draft202012Validator(SCHEMA).is_valid(rec)


def ok(name, obj):
    return jsonschema.Draft202012Validator(dict(SCHEMA["$defs"][name], **{"$defs": SCHEMA["$defs"]})).is_valid(obj)


V08 = copy.deepcopy(FULL_V07)
V08["schema_version"] = "wp1.8.0-draft"
V08["record_id"] = "rec-fake-8"


def test_version_bump_additive():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["x-contract-version"] == "0.8"
    assert SCHEMA["properties"]["schema_version"]["enum"][-2:] == ["wp1.7.0-draft", "wp1.8.0-draft"]
    assert SCHEMA["$defs"]["extraction_run_manifest"]["properties"]["contract_version"]["enum"] == ["0.5", "0.6", "0.7", "0.8"]
    assert TIERS["version"] == "0.7.2"  # tier logic unchanged; only note text changed
    assert whole(V08) and whole(FULL_V07)
    no_id = copy.deepcopy(V08); del no_id["record_id"]
    assert not whole(no_id)  # record_id required, same gate as wp1.7.0-draft


# --- 1. condition JSON pins + rebuild ---------------------------------------------------------

def test_condition_pins_csv_and_json():
    clist = json.loads((ROOT / "schemas/condition_list.json").read_text())
    roll = json.loads((ROOT / "schemas/condition_rollup.json").read_text())
    assert clist["approval"]["approved_csv_sha256"] == LIST_CSV_SHA256
    assert clist["approval"]["rollup_csv_sha256"] == roll["source_csv_sha256"] == ROLLUP_CSV_SHA256
    assert sha(ROOT / "schemas/condition_sources/condition-list-v0.1-approved.csv") == LIST_CSV_SHA256
    assert sha(ROOT / "schemas/condition_sources/condition-rollup-v0.1.csv") == ROLLUP_CSV_SHA256
    assert sha(ROOT / "schemas/condition_list.json") == LIST_JSON_SHA256 == mv.CONDITION_LIST_SHA256
    assert sha(ROOT / "schemas/condition_rollup.json") == ROLLUP_JSON_SHA256 == mv.CONDITION_ROLLUP_SHA256


def test_condition_json_rebuilds_byte_identical_from_pinned_csvs():
    lb, rb = bcj.build()
    assert lb == (ROOT / "schemas/condition_list.json").read_bytes()
    assert rb == (ROOT / "schemas/condition_rollup.json").read_bytes()
    assert hashlib.sha256(lb).hexdigest() == LIST_JSON_SHA256
    assert hashlib.sha256(rb).hexdigest() == ROLLUP_JSON_SHA256


def test_rebuild_refuses_csv_not_matching_pin(tmp_path):
    for n in (bcj.LIST_CSV, bcj.ROLLUP_CSV):
        (tmp_path / n).write_bytes((ROOT / "schemas/condition_sources" / n).read_bytes())
    with open(tmp_path / bcj.LIST_CSV, "a") as f:
        f.write("D999999,Fake,Y,N,C99,,\n")
    with pytest.raises(ValueError):
        bcj.build(tmp_path)


# --- 2. value_source on conditions + blinding ---------------------------------------------------

def test_condition_entry_value_source_reuses_vocabulary():
    prop = SCHEMA["$defs"]["condition_entry"]["properties"]["value_source"]
    assert prop["$ref"] == "#/$defs/value_source"
    base = {"condition_key": "neoplasms", "mesh_descriptor_id": "D009369", "label": "Neoplasms"}
    assert ok("condition_entry", base)  # optional: pre-v0.8 entries stay valid
    for v in SCHEMA["$defs"]["value_source"]["enum"]:
        assert ok("condition_entry", dict(base, value_source=v))
    assert not ok("condition_entry", dict(base, value_source="mesh"))


def test_legacy_condition_stripped_from_labelling_export():
    rec = copy.deepcopy(V08)
    rec["reporting"]["conditions"] = [
        {"condition_key": "neoplasms", "mesh_descriptor_id": "D009369", "label": "Neoplasms", "value_source": "paper"},
        {"condition_key": "pain", "mesh_descriptor_id": "D010146", "label": "Pain", "value_source": "legacy_llm_fullpdf"},
    ]
    rec["mesh_from_pubmed"] = ["D010146"]
    assert whole(rec)
    out = mc.labelling_export(rec)
    assert [c["condition_key"] for c in out["reporting"]["conditions"]] == ["neoplasms"]
    assert "mesh_from_pubmed" not in out
    assert "legacy_llm_fullpdf" not in json.dumps(out) and "D010146" not in json.dumps(out)
    assert len(rec["reporting"]["conditions"]) == 2  # source record untouched


# --- 3. honesty_label rule_based ------------------------------------------------------------------

def test_honesty_label_rule_based():
    assert SCHEMA["properties"]["honesty_label"]["enum"] == ["ai_assisted", "human_reviewed", "hand_tagged", "mixed", "rule_based"]
    assert whole(dict(V08, honesty_label="rule_based"))


# --- 4. Pipeline #119 gaps 1, 2, 4, 6 -------------------------------------------------------------

def test_gap1_product_name_verbatim():
    f = SCHEMA["properties"]["fields"]["properties"]["product_name_verbatim"]
    assert f["$ref"] == "#/$defs/string_field"
    assert "product_name_verbatim" in SCHEMA["$defs"]["run_event"]["properties"]["field"]["enum"]
    rec = copy.deepcopy(V08)
    rec["fields"]["product_name_verbatim"] = {"value": "Sativex", "missingness": "reported", "source_span": "Sativex (GW Pharma)"}
    assert whole(rec)
    assert mc.compute(rec, "in_vivo") == mc.compute(V08, "in_vivo")  # never affects any tier


def _manifest(reason):
    return {"run_id": "r1", "mode": "scored", "split": "dev", "contract_version": "0.8", "git_ref": "abc",
            "config_hash": "h", "prompt_hashes": {}, "model_ids": {}, "records": [],
            "excluded": [{"paper_pmid": "123", "reason": reason}]}


def test_gap2_not_original_research_exclusion():
    reasons = SCHEMA["$defs"]["extraction_run_manifest"]["properties"]["excluded"]["items"]["properties"]["reason"]["enum"]
    assert reasons == ["budget_cap", "pmid_missing", "source_unavailable", "consecutive_failures", "not_original_research"]
    assert ok("extraction_run_manifest", _manifest("not_original_research"))
    assert not ok("extraction_run_manifest", _manifest("survey"))


@pytest.mark.parametrize("name,lo,hi", [("number_field", 5.0, 10.5), ("integer_field", 8, 12)])
def test_gap4_numeric_ranges(name, lo, hi):
    assert ok(name, {"value": None, "missingness": "reported", "value_min": lo, "value_max": hi, "source_span": "x"})
    assert ok(name, {"value": lo, "missingness": "reported"})  # existing shape unchanged
    assert not ok(name, {"value": None, "missingness": "reported", "value_min": lo})  # both bounds
    assert not ok(name, {"value": None, "missingness": "not_in_available_source", "value_min": lo, "value_max": hi})
    assert ok(name, {"value": None, "missingness": "not_in_available_source", "value_min": None, "value_max": None})
    rec = copy.deepcopy(V08)
    rec["fields"]["dose_mg" if name == "number_field" else "sample_size"] = {"value": None, "missingness": "reported", "value_min": lo, "value_max": hi}
    assert whole(rec)


def test_gap4_integer_range_rejects_fraction():
    assert not ok("integer_field", {"value": None, "missingness": "reported", "value_min": 1.5, "value_max": 3})


def test_gap6_could_not_check_pointer():
    cnc = {"status": "could_not_check", "check_failure_reason": "figure_not_read",
           "could_not_check_pointer": {"location": "figure", "ref": "Figure 2B"}}
    assert ok("field_reporting", cnc)
    assert ok("field_reporting", dict(cnc, could_not_check_pointer={"location": "supplementary"}))
    assert not ok("field_reporting", dict(cnc, could_not_check_pointer={"ref": "Figure 2B"}))  # location required
    assert not ok("field_reporting", {"status": "reported", "location": "methods",
                                      "could_not_check_pointer": {"location": "figure"}})
    assert ok("field_reporting", {"status": "not_reported", "could_not_check_pointer": None})
    rec = copy.deepcopy(V08); rec["reporting"]["frequency"] = cnc
    assert whole(rec)
    assert mc.compute(rec, "in_vivo")["universal"]["grade"] == mc.compute(V08, "in_vivo")["universal"]["grade"]


# --- 5. reliability nits --------------------------------------------------------------------------

def test_no_note_starts_with_stale_version_prefix():
    def strings(n):
        if isinstance(n, dict):
            for v in n.values(): yield from strings(v)
        elif isinstance(n, list):
            for v in n: yield from strings(v)
        elif isinstance(n, str):
            yield n
    assert not [s for s in strings(TIERS) if s.startswith("v0.7.1")]
    assert "only when there is more than one" in TIERS["route_requirements"]["grading"]


def test_check_failure_reasons_documented_as_optional():
    doc = (ROOT / "docs/methods/field-dictionary.md").read_text()
    assert "`check_failure_reasons`" in doc and "only when" in doc


# --- 6. fields-or-{} guards -----------------------------------------------------------------------

@pytest.mark.parametrize("fields", [None, {}])
def test_route_lookups_tolerate_missing_fields(fields):
    rec = copy.deepcopy(V08); rec["fields"] = fields
    mc.compute(rec, "in_vivo")
    assert mc.route_requirements(rec, "in_vivo")["route_reported"] is False
    assert mc.route_fields_status(rec, "in_vivo", mc.load_rule()) is None
    assert mv.comparator(rec) is None and mv.detail_view(rec)["comparator"] is None
