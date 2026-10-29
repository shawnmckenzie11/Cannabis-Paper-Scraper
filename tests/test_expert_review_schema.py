"""Validate the expert_review definition in schemas/methods.schema.json."""
import copy, json, pathlib
import pytest
jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
VALID = json.loads((ROOT / "tests/fixtures/expert_review_valid.json").read_text())

def validate(rec):
    s = dict(SCHEMA["$defs"]["expert_review"], **{"$defs": SCHEMA["$defs"]})
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    jsonschema.Draft202012Validator(s).validate(rec)

def test_valid_fixture():
    validate(VALID)

@pytest.mark.parametrize("patch", [
    {"verdict": ""}, {"verdict": "unclear"}, {"verdict": None},
    {"label_status": "gold"}, {"label_status": "training"},
    {"input_mode": "typed", "raw_transcript": "x"}, {"audio": "b64"},
])
def test_invalid(patch):
    rec = copy.deepcopy(VALID); rec.update(patch)
    with pytest.raises(jsonschema.ValidationError):
        validate(rec)

def test_missing_verdict_invalid():
    rec = copy.deepcopy(VALID); del rec["verdict"]
    with pytest.raises(jsonschema.ValidationError):
        validate(rec)

def validate_fmt(rec):
    s = dict(SCHEMA["$defs"]["expert_review"], **{"$defs": SCHEMA["$defs"]})
    jsonschema.Draft202012Validator(
        s, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    ).validate(rec)

@pytest.mark.parametrize("patch", [
    {"reviewer_confidence": 0}, {"reviewer_confidence": 6},
    {"split_assignment_ref": ""},
])
def test_invalid_bounds(patch):
    rec = copy.deepcopy(VALID); rec.update(patch)
    with pytest.raises(jsonschema.ValidationError):
        validate(rec)

def test_study_group_id_required_nullable():
    validate(dict(VALID, study_group_id=None))
    validate(dict(VALID, study_group_id="sg-1"))
    rec = copy.deepcopy(VALID); del rec["study_group_id"]
    with pytest.raises(jsonschema.ValidationError):
        validate(rec)

def test_valid_voice_record():
    validate_fmt(dict(VALID, input_mode="voice", raw_transcript="the passage does not say",
                      stt_model_id="whisper-large-v3"))

def test_reviewed_at_format_enforced():
    pytest.importorskip("rfc3339_validator")
    validate_fmt(VALID)
    with pytest.raises(jsonschema.ValidationError):
        validate_fmt(dict(VALID, reviewed_at="29/10/2026 4pm"))

def test_top_level_schema_version_accepts_draft_versions():
    sv = SCHEMA["properties"]["schema_version"]
    for v in ("wp1.0.0-draft", "wp1.1.0-draft"):
        jsonschema.Draft202012Validator(sv).validate(v)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(sv).validate("wp0.9")
