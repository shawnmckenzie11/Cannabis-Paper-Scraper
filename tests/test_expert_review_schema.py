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
