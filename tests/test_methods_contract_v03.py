"""Contract tests for methods schema v0.3 (eval gaps, fields only). Fake data only."""
import json, pathlib
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
FC = jsonschema.Draft202012Validator.FORMAT_CHECKER
SHA = "0" * 64
TS = "2026-10-29T12:00:00Z"

def v(name, rec):
    s = dict(SCHEMA["$defs"][name], **{"$defs": SCHEMA["$defs"]})
    jsonschema.Draft202012Validator(s, format_checker=FC).validate(rec)

def bad(name, rec):
    with pytest.raises(jsonschema.ValidationError):
        v(name, rec)

def drop(d, k):
    return {x: y for x, y in d.items() if x != k}

def test_schema_valid_and_version():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert "wp1.3.0-draft" in SCHEMA["properties"]["schema_version"]["enum"]
    assert "wp1.2.0-draft" in SCHEMA["properties"]["schema_version"]["enum"]
    assert SCHEMA["x-contract-version"] in ("0.3", "0.4", "0.5")  # v0.4 and v0.5 are additive

GB = {"paper_id": 1, "rater": "rater-fake-1", "timestamp": TS, "raw_transcript": "t",
      "submitted_rationale": "r", "split": "dev", "storage": "working_store",
      "decision": "gold", "awarded_by": "rater-fake-1", "work_id": "w-1"}
GBL = dict(GB, split="test", storage="locked_store", rater_verified=True, rationale_format="free_text")

def test_gap1_gold_decision_awarded_by():
    v("gold_badge", GB)
    v("gold_badge", dict(GB, decision="not_gold"))
    bad("gold_badge", dict(GB, decision="silver"))
    bad("gold_badge", dict(GB, awarded_by=""))

REL = {"record_id": "rec-rel-1", "query_id": "q-1", "pmid": 12345, "grade": 2, "rater": "rater-fake-1",
       "query_set_version": "qs-v1", "query_set_sha256": SHA}

def test_gap2_relevance_grade():
    v("relevance_grade", REL)
    assert SCHEMA["$defs"]["relevance_grade"]["x-key"] == ["query_id", "pmid"]
    assert SCHEMA["$defs"]["relevance_grade"]["properties"]["grade"]["x-placeholder"] is True
    for g in (0, 1):
        v("relevance_grade", dict(REL, grade=g))
    bad("relevance_grade", dict(REL, grade=3))
    bad("relevance_grade", dict(REL, grade=1.5))
    bad("relevance_grade", dict(REL, query_set_sha256="abc"))
    for k in REL:
        bad("relevance_grade", drop(REL, k))
    bad("relevance_grade", dict(REL, topic_match=1))  # proposed axes are NOT schema fields yet

COR = {"record_id": "r-2", "supersedes_record_id": "r-1", "reason": "typo", "corrected_by": "rater-fake-1",
       "at": TS, "retracted": False}

def test_gap3_correction():
    v("correction", COR)
    v("correction", dict(COR, retracted=True))
    bad("correction", dict(COR, retracted="no"))
    for k in COR:
        bad("correction", drop(COR, k))

def test_gap4_pmid_mapping():
    v("paper_pmid_mapping", {"record_id": "rec-map-1", "paper_id": 1, "pmid": 9, "pmid_missing": False, "manifest_eligible": True})
    v("paper_pmid_mapping", {"record_id": "rec-map-1", "paper_id": 1, "pmid": None, "pmid_missing": True, "manifest_eligible": False})
    bad("paper_pmid_mapping", {"record_id": "rec-map-1", "paper_id": 1, "pmid": None, "pmid_missing": True, "manifest_eligible": True})
    bad("paper_pmid_mapping", {"record_id": "rec-map-1", "paper_id": 1, "pmid": None, "pmid_missing": False, "manifest_eligible": False})
    bad("paper_pmid_mapping", {"record_id": "rec-map-1", "paper_id": 1, "pmid": 9, "pmid_missing": True, "manifest_eligible": False})

REF = {"paper_id": 1, "field": "thc_pct", "value": 20, "status": "draft", "work_id": "w-1",
       "split": "dev", "storage": "working_store"}

def test_gap5_work_id():
    v("reference_value", REF)
    v("gold_badge", GB)
    bad("reference_value", dict(REF, work_id=""))
    bad("gold_badge", dict(GB, work_id=5))

def test_gap6_locked_store_requires_verified_and_format():
    for name, rec in (("gold_badge", GBL),
                      ("reference_value", dict(REF, split="test", storage="locked_store", rater_verified=True, rationale_format="free_text")),
                      ("relevance_grade", dict(REL, split="test", storage="locked_store", rater_verified=True, rationale_format="free_text"))):
        v(name, rec)
        bad(name, drop(rec, "rater_verified"))
        bad(name, drop(rec, "rationale_format"))
        bad(name, dict(rec, rater_verified=False))
        bad(name, dict(rec, rationale_format="scored_rubric"))
    v("gold_badge", GB)  # working_store: not required

def test_gap7_reference_split_storage():
    v("reference_value", dict(REF, split="test", storage="locked_store", rater_verified=True, rationale_format="free_text"))
    bad("reference_value", dict(REF, split="test", storage="working_store"))
    bad("reference_value", drop(dict(REF, split="test"), "storage"))
    bad("reference_value", dict(REF, split="train"))
    bad("reference_value", dict(REF, storage="s3"))

def test_gap8_gold_split_excludes_train():
    assert "train" not in SCHEMA["$defs"]["gold_badge"]["properties"]["split"]["enum"]
    assert "intentional" in SCHEMA["$defs"]["gold_badge"]["properties"]["split"]["description"]
    bad("gold_badge", dict(GB, split="train"))
    v("gold_badge", GBL)
    bad("gold_badge", dict(GBL, storage="working_store"))

def test_v02_records_still_valid():
    v("reference_value", {"paper_id": 1, "field": "thc_pct", "value": 20, "status": "draft"})

def test_record_id_everywhere():
    D = SCHEMA["$defs"]
    for k in ("gold_badge", "reference_value", "relevance_grade", "correction",
              "paper_pmid_mapping", "expert_review", "work_merge_flag"):
        assert D[k]["properties"]["record_id"] == {"$ref": "#/$defs/record_id"}, k
    for k in ("relevance_grade", "correction", "paper_pmid_mapping"):
        assert "record_id" in D[k]["required"], k
    assert "record_id" not in D["gold_badge"]["required"]
    assert "record_id" not in D["reference_value"]["required"]
    v("gold_badge", dict(GB, record_id="rec-gb-1"))
    v("reference_value", dict(REF, record_id="rec-ref-1"))
    bad("gold_badge", dict(GB, record_id=""))
    bad("paper_pmid_mapping", drop({"record_id": "x", "paper_id": 1, "pmid": 9, "pmid_missing": False, "manifest_eligible": True}, "record_id"))
    assert "supersedes_record_id" not in D["paper_pmid_mapping"]["description"]

def test_rationale_format_free_text_only():
    assert SCHEMA["$defs"]["rationale_format"]["enum"] == ["pending_biomedical", "free_text"]
    bad("gold_badge", dict(GBL, rationale_format="pending_biomedical"))

def test_v02_working_store_gold_pending_biomedical_valid():
    v("gold_badge", {"paper_id": 1, "rater": "rater-fake-1", "timestamp": TS, "raw_transcript": "t",
                     "submitted_rationale": "r", "split": "dev", "storage": "working_store",
                     "rationale_format": "pending_biomedical"})
    assert "Biomedical lead" in SCHEMA["$defs"]["rationale_format"]["description"]

def test_locked_store_is_test_only():
    lk = dict(storage="locked_store", rater_verified=True, rationale_format="free_text")
    bad("gold_badge", dict(GBL, split="dev"))
    bad("reference_value", dict(REF, split="dev", **lk))
    bad("relevance_grade", dict(REL, split="dev", **lk))
    bad("relevance_grade", dict(REL, **lk))  # split missing
    v("relevance_grade", dict(REL, split="test", **lk))
    v("relevance_grade", dict(REL, split="dev", storage="working_store"))

def test_retraction_by_record_id():
    assert "record_id" in SCHEMA["$defs"]["correction"]["description"]
    v("correction", dict(COR, retracted=True, supersedes_record_id="rec-gb-1"))
