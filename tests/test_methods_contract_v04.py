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
    assert SCHEMA["x-contract-version"] == "0.4"
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


def test_source_tier_includes_pmc_oa_xml():
    import json
    s = json.load(open("schemas/methods.schema.json"))
    assert "pmc_oa_xml" in s["$defs"]["source_tier"]["enum"]


def test_source_license_defaults_not_storable():
    import json
    s = json.load(open("schemas/methods.schema.json"))
    d = s["$defs"]["source_license"]
    assert d["properties"]["fulltext_storable"]["default"] is False
    assert "source_license" in s["$defs"]["provenance_envelope"]["properties"]
    assert "source_license" not in s["$defs"]["provenance_envelope"]["required"]
