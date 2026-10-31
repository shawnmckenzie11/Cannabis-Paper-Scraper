"""Contract tests for methods schema v0.5 (wp1.5.0-draft) additions."""
import json, pathlib
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())


def vd(name, rec):
    s = dict(SCHEMA["$defs"][name], **{"$defs": SCHEMA["$defs"]})
    jsonschema.Draft202012Validator(s).validate(rec)


def test_source_tier_includes_pmc_oa_xml():
    assert "pmc_oa_xml" in SCHEMA["$defs"]["source_tier"]["enum"]


LIC = {"license_raw": "CC BY 4.0", "license_source": "pmc_oa_list", "fulltext_storable": False, "pending_biomedical": True}


def test_source_license_valid_and_pinned_false():
    vd("source_license", LIC)
    for k in ("fulltext_storable", "pending_biomedical"):
        r = dict(LIC); r.pop(k)
        with pytest.raises(jsonschema.ValidationError):
            vd("source_license", r)
    with pytest.raises(jsonschema.ValidationError):
        vd("source_license", dict(LIC, fulltext_storable=True))
    assert "source_license" not in SCHEMA["$defs"]["provenance_envelope"]["required"]


def test_split_has_validation():
    vd("split", "validation")
    with pytest.raises(jsonschema.ValidationError):
        vd("split", "train")


def test_test_split_only_in_locked_store_both_ways():
    vd("locked_store_test_only", {"split": "test", "storage": "locked_store"})
    vd("locked_store_test_only", {"split": "dev", "storage": "working_store"})
    for rec in ({"split": "test", "storage": "working_store"}, {"split": "test"},
                {"split": "dev", "storage": "locked_store"}):
        with pytest.raises(jsonschema.ValidationError):
            vd("locked_store_test_only", rec)


MAN = {"run_id": "r1", "mode": "scored", "split": "dev", "contract_version": "0.5", "git_ref": "abc123",
       "config_hash": "h", "prompt_hashes": {"x23-A-v1": "p"}, "model_ids": {"A": "m"},
       "records": [{"record_id": "rec-1", "paper_pmid": "123"}], "excluded": [{"paper_pmid": "456", "reason": "budget_cap"}],
       "events": [{"record_id": "rec-1", "field": "route", "reason": "quote_not_found"}]}


def test_run_manifest_scored_and_fences():
    vd("extraction_run_manifest", MAN)
    for bad_rec in (dict(MAN, split="test"), dict(MAN, model_ids=None),
                    dict(MAN, mode="dry_run"), dict(MAN, events=[{"record_id": "r", "field": "f", "reason": "x", "quote": "q"}])):
        with pytest.raises(jsonschema.ValidationError):
            vd("extraction_run_manifest", bad_rec)
    vd("extraction_run_manifest", dict(MAN, mode="dry_run", model_ids=None, records=[]))


def test_record_id_required_only_on_v05():
    gate = [c for c in SCHEMA["allOf"] if c.get("if", {}).get("properties", {}).get("schema_version", {}).get("const") == "wp1.5.0-draft"]
    assert len(gate) == 1
    g = jsonschema.Draft202012Validator(gate[0])
    assert not g.is_valid({"schema_version": "wp1.5.0-draft"})
    assert g.is_valid({"schema_version": "wp1.5.0-draft", "record_id": "rec-1"})
    assert g.is_valid({"schema_version": "wp1.4.0-draft"})


def test_run_event_field_is_a_contract_field():
    vd("run_event", {"record_id": "r", "field": "route", "reason": "quote_not_found"})
    with pytest.raises(jsonschema.ValidationError):
        vd("run_event", {"record_id": "r", "field": "patient said she felt dizzy", "reason": "quote_not_found"})
    with pytest.raises(jsonschema.ValidationError):
        vd("extraction_run_manifest", dict(MAN, contract_version="anything"))


def test_run_event_field_enum_matches_fields_keys():
    enum = set(SCHEMA["$defs"]["run_event"]["properties"]["field"]["enum"])
    assert enum == set(SCHEMA["properties"]["fields"]["properties"])


FULL_V05 = {
    "schema_version": "wp1.5.0-draft",
    "paper_id": 1,
    "record_id": "rec-fake-1",
    "provenance": {
        "schema_version": "0.5",
        "extractor_id": "fake-extractor",
        "model_id": None,
        "prompt_id": None,
        "confidence": None,
        "source_tier": "pmc_oa_xml",
        "source_span": None,
        "human_override": False,
        "reviewed_at": None,
        "source_license": {
            "license_raw": None,
            "license_source": "unknown",
            "fulltext_storable": False,
            "pending_biomedical": True,
        },
    },
    "fields": {},
}


def test_full_v05_record_validates_against_whole_schema():
    jsonschema.Draft202012Validator(SCHEMA).validate(FULL_V05)
    missing = {k: v for k, v in FULL_V05.items() if k != "record_id"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(SCHEMA).validate(missing)


def test_excluded_reason_has_consecutive_failures():
    vd("extraction_run_manifest", dict(MAN, excluded=[{"paper_pmid": "123", "reason": "consecutive_failures"}]))


def test_excluded_reason_set_is_exact():
    reason = SCHEMA["$defs"]["extraction_run_manifest"]["properties"]["excluded"]["items"]["properties"]["reason"]
    assert reason["enum"] == ["budget_cap", "pmid_missing", "source_unavailable", "consecutive_failures"]
    for bad in ("consecutive_failure_stop", "other", ""):
        with pytest.raises(jsonschema.ValidationError):
            vd("extraction_run_manifest", dict(MAN, excluded=[{"paper_pmid": "123", "reason": bad}]))
