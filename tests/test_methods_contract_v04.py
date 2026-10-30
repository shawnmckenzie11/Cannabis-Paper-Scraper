"""Contract tests for methods schema v0.4 (pubmed_metadata_record). Validates the real merged file."""
import copy, json, pathlib
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
DATA = json.loads((ROOT / "data/pubmed_metadata_split_v0.1.json").read_text())
PREPRINTS = {"40502036", "36993751", "40235472"}


def v(rec):
    s = dict(SCHEMA["$defs"]["pubmed_metadata_record"], **{"$defs": SCHEMA["$defs"]})
    jsonschema.Draft202012Validator(s).validate(rec)


def bad(rec):
    with pytest.raises(jsonschema.ValidationError):
        v(rec)


def test_schema_valid_and_version():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["x-contract-version"] == "0.5"
    assert "wp1.4.0-draft" in SCHEMA["properties"]["schema_version"]["enum"]
    assert "wp1.3.0-draft" in SCHEMA["properties"]["schema_version"]["enum"]


def test_real_file_validates_unchanged():
    assert isinstance(DATA, list) and DATA
    for rec in DATA:
        v(rec)


def test_preprints_derivable_from_publication_types():
    derived = {r["pmid"] for r in DATA if "Preprint" in (r["publication_types"] or [])}
    assert derived == PREPRINTS


def rec0():
    return copy.deepcopy(DATA[0])


def test_null_with_reason_ok_and_record_id_optional():
    r = rec0(); r["doi"] = None; r["missing"] = {"doi": "absent in PubMed record"}
    v(r)
    v(dict(rec0(), record_id="pm-1"))
    bad(dict(rec0(), record_id=""))


@pytest.mark.parametrize("f", ["title", "authors", "journal", "year", "doi", "publication_types"])
def test_null_without_reason_rejected(f):
    r = rec0(); r[f] = None; r["missing"] = {}
    bad(r)


def test_missing_pmid_and_bad_shapes():
    r = rec0(); del r["pmid"]; bad(r)
    bad(dict(rec0(), pmid=40818965))
    bad(dict(rec0(), extra=1))
    bad(dict(rec0(), missing={"pmid": "x"}))
    for ts in ("2026-10-30T03:00:43", "2026-10-30 03:00:43Z", "2026-10-30T03:00:43+00:00", "2026-10-30"):
        r = rec0(); r["provenance"]["retrieved_at"] = ts; bad(r)
    r = rec0(); del r["provenance"]["endpoint"]; bad(r)


def test_provenance_params_reject_email_and_api_key():
    import copy
    import json
    import jsonschema
    from pathlib import Path

    schema = json.loads(Path("schemas/methods.schema.json").read_text())
    sub = {"$defs": schema["$defs"], "$ref": "#/$defs/pubmed_metadata_record"}
    params = sub["$defs"]["pubmed_metadata_record"]["properties"]["provenance"]["properties"]["params"]
    v = jsonschema.Draft202012Validator(params)
    assert v.is_valid({"db": "pubmed", "retmode": "xml"})
    for k in ("email", "api_key"):
        assert not v.is_valid({"db": "pubmed", k: "x"})



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
