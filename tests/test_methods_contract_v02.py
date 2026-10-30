"""Contract tests for methods schema v0.2 additions."""
import json, pathlib
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
GUEST = json.loads((ROOT / "schemas/guest_public_fields.json").read_text())
RULES = json.loads((ROOT / "schemas/normalization_rules.json").read_text())
FC = jsonschema.Draft202012Validator.FORMAT_CHECKER

def v(name, rec):
    s = dict(SCHEMA["$defs"][name], **{"$defs": SCHEMA["$defs"]})
    jsonschema.Draft202012Validator(s, format_checker=FC).validate(rec)

def bad(name, rec):
    with pytest.raises(jsonschema.ValidationError):
        v(name, rec)

def test_schema_valid():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)

def test_guest_allowlist_default_deny():
    assert GUEST["policy"] == "default_deny"
    allowed = set(GUEST["paper_fields"]) | set(GUEST["link_fields"])
    assert not allowed & set(GUEST["denied_examples"])
    assert "strain_normalized" not in allowed and "strain_reported" in allowed
    assert set(GUEST["link_relationships"]) == {"cites", "cited_by"}
    assert set(GUEST["stats_forbidden_breakdowns"]) == {"split", "gold", "verdict", "reviewer"}

def test_study_group_is_paper():
    assert SCHEMA["properties"]["record_unit"]["enum"] == ["paper"]
    assert "study_group_id" in SCHEMA["properties"]

REF = {"paper_id": 1, "field": "thc_pct", "value": 20, "status": "draft"}
def test_reference_value():
    v("reference_value", REF)
    v("reference_value", dict(REF, status="expert_checked", checked_by="x", checked_at="2026-10-29T12:00:00Z"))
    bad("reference_value", dict(REF, status="expert_checked"))
    bad("reference_value", dict(REF, status="gold"))

GB = {"paper_id": 1, "rater": "phd@x", "timestamp": "2026-10-29T12:00:00Z", "raw_transcript": "t",
      "submitted_rationale": "r", "split": "dev", "storage": "working_store"}
def test_gold_badge():
    v("gold_badge", GB)
    v("gold_badge", dict(GB, split="test", storage="locked_store", rater_verified=True, rationale_format="free_text"))  # v0.3 gap 6
    bad("gold_badge", dict(GB, split="test", storage="working_store"))
    bad("gold_badge", dict(GB, audio="b64"))
    bad("gold_badge", {k: x for k, x in GB.items() if k != "submitted_rationale"})

def test_normalized_field():
    v("normalized_field", {"raw_text": "once daily", "normalized_value": "daily", "normalization_status": "pending_biomedical",
                            "rule_id": "frequency_once_daily_vs_daily", "vocabulary_version": "v0"})  # v0.6: rule_id + version required
    bad("normalized_field", {"raw_text": "x", "normalized_value": "x", "normalization_status": "bogus"})  # v0.6 widened enum
    bad("normalized_field", {"normalized_value": "x", "normalization_status": "pending_biomedical"})

def test_normalization_rules():
    r = {x["id"]: x for x in RULES["rules"]}
    assert r["range_format"]["status"] == "allowed" and r["range_format"]["kind"] == "formatting_only"
    assert r["frequency_once_daily_vs_daily"]["status"] == "pending_biomedical"

def test_work_id_and_merge_flag():
    assert "work_id" in SCHEMA["properties"]
    f = {"paper_ids": [1, 2], "match_basis": "doi", "status": "flagged"}
    v("work_merge_flag", f)
    bad("work_merge_flag", dict(f, auto_merged=True))
    bad("work_merge_flag", dict(f, status="confirmed_merge"))
    v("work_merge_flag", dict(f, status="confirmed_merge", new_manifest_version="m2"))
