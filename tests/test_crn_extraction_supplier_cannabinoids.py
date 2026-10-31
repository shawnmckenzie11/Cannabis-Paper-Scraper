"""Supplier-compound linking, cannabinoid vocabulary matching, rule_based honesty label.
Synthetic fixtures only: fake PMIDs (999999xx) and fake company names."""
import json
import pathlib
import sys

import pytest

pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crn_extraction import extractor  # noqa: E402
import methods_completeness  # noqa: E402


def run(text, pmid="99999950"):
    out = extractor.extract({"paper_id": 1, "pmid": pmid, "source_tier": "unknown",
                             "sections": [{"type": "methods", "text": text}]})
    rec = out["record"]
    assert extractor.validate(rec) == []
    return rec


def test_animal_source_never_fills_supplier():
    rec = run("Male Wistar rats were obtained from the Fake Experimental Centre. "
              "CBD was purchased from Fakechem Ltd. Rats received CBD (10 mg/kg) i.p.")
    assert rec["fields"]["supplier"]["value"] == "Fakechem Ltd"
    assert "CBD" in rec["reporting"]["supplier"]["evidence_quote"]


@pytest.mark.parametrize("text", [
    "Rats were obtained from Fake Breeders Inc. Rats received THC i.p.",
    "Breeding pairs were supplied by Fake Vendor Co. Mice received THC i.p.",
    "Mice from our colony were provided by the Fake Animal Facility. Mice received CBD i.p.",
])
def test_animal_and_colony_sentences_leave_supplier_missing(text):
    rec = run(text)
    assert "supplier" not in rec["fields"]
    assert rec["reporting"]["supplier"]["status"] == "not_reported"


def test_each_compound_linked_to_its_supplier():
    rec = run("Mice were purchased from Fake Labs. CBD was obtained from Alpha Fake Inc and "
              "Δ9-THC from Beta Fake Ltd. Mice received 5 mg/kg CBD i.p.")
    assert rec["fields"]["supplier"]["value"] == "CBD: Alpha Fake Inc; Δ9-THC: Beta Fake Ltd"


def test_generic_test_compound_sentence_counts():
    rec = run("Rats were obtained from Fake Farm. All drugs were purchased from Fake Sigma Co. Rats received drug i.p.")
    assert rec["fields"]["supplier"]["value"] == "Fake Sigma Co"


@pytest.mark.parametrize("text,names", [
    ("Rats received CBD (10 mg/kg) i.p.", ["cbd"]),
    ("Rats received Δ9-tetrahydrocannabinol (3 mg/kg) i.p.", ["thc"]),
    ("Rats received Δ9\u2011THC (3 mg/kg) i.p.", ["thc"]),  # non-breaking hyphen from PDF text
    ("Rats received Δ 9-THC (3 mg/kg) i.p.", ["thc"]),
    ("Mice received cannabidiol (CBD, 10 mg/kg) and THC (1 mg/kg) i.p.", ["thc", "cbd"]),
])
def test_cannabinoids_normalized_and_measured(text, names):
    rec = run(text)
    vals = rec["fields"]["cannabinoids"]["value"]
    assert sorted(e["name"] for e in vals) == sorted(names)
    assert all(isinstance(e["amount"], float) and e["unit"] for e in vals)
    assert sorted(methods_completeness._measured_cannabinoids(rec["fields"])) == sorted(names)


def test_names_come_only_from_locked_vocabulary():
    vocab = json.loads((ROOT / "schemas/normalization_rules.json").read_text())["vocabularies"]["cannabinoid"]
    allowed = {v["value"] for v in vocab["values"]}
    rec = run("Rats received THC, CBD, CBG and THC-COOH i.p.")
    assert {e["name"] for e in rec["fields"]["cannabinoids"]["value"]} <= allowed


def test_no_amount_when_no_number_next_to_name():
    rec = run("Rats received THC i.p. The dose was 3 mg/kg.")
    assert "amount" not in rec["fields"]["cannabinoids"]["value"][0]


def test_honesty_label_is_rule_based_and_in_schema_enum():
    enum = json.loads((ROOT / "schemas/methods.schema.json").read_text())["properties"]["honesty_label"]["enum"]
    rec = run("Rats received THC i.p.")
    assert rec["honesty_label"] == "rule_based" and "rule_based" in enum
