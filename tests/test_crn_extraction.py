"""CRN Pipeline rule extractor + main-text benchmark harness. Fake fixtures only (fake PMIDs, no PII)."""
import json
import pathlib
import re

import pytest

jsonschema = pytest.importorskip("jsonschema")
ROOT = pathlib.Path(__file__).resolve().parents[1]
import sys; sys.path.insert(0, str(ROOT))
from crn_extraction import benchmark, extractor, sources

FIX = ROOT / "tests/fixtures/crn_extraction"
SCHEMA = json.loads((ROOT / "schemas/methods.schema.json").read_text())
FR = SCHEMA["$defs"]["field_reporting"]["properties"]


def ex(name):
    return extractor.extract(json.loads((FIX / f"{name}.json").read_text()))


def rep(name):
    return ex(name)["record"]["reporting"]


@pytest.mark.parametrize("f", sorted(p.stem for p in FIX.glob("*.json")))
def test_every_record_is_contract_valid(f):
    r = ex(f)
    if r["record"] is not None:
        assert extractor.validate(r["record"]) == []
        assert r["record"]["schema_version"] == "wp1.7.0-draft"


def test_every_contract_reason_code_is_emitted():
    s = benchmark.run(benchmark.load_fixtures(FIX), max_papers=11)
    assert set(s["check_failure_reason"]) == set(SCHEMA["$defs"]["check_failure_reason"]["enum"])
    assert set(s["not_reported_reason"]) == set(FR["not_reported_reason"]["anyOf"][0]["enum"])
    assert s["schema_invalid"] == {}


def test_paper_level_failures():
    for name, reason in [("08_fetch_failed", "fetch_failed"), ("09_parse_failed", "parse_failed"),
                         ("10_source_not_available", "source_not_available")]:
        for v in rep(name).values():
            assert v == {"status": "could_not_check", "check_failure_reason": reason, "location": None}


def test_budget_cap_and_manifest():
    papers = benchmark.load_fixtures(FIX)
    s = benchmark.run(papers, max_papers=0)
    assert set(s["check_failure_reason"]) == {"budget_cap"}
    assert {e["reason"] for e in s["excluded"]} == {"budget_cap"}
    m = benchmark.manifest(s, "t")
    v = jsonschema.Draft202012Validator(dict(SCHEMA["$defs"]["extraction_run_manifest"], **{"$defs": SCHEMA["$defs"]}))
    assert list(v.iter_errors(m)) == []
    assert m["split"] == "dev" and "test" not in json.dumps(m["excluded"])


def test_supplement_is_source_not_fetched_not_fetch_failed():
    r = rep("12_in_vivo_supplement_dose")["dose"]
    assert r["check_failure_reason"] == "source_not_fetched" and r["location"] is None
    assert rep("02_in_vivo_gaps")["vehicle_solvent"]["check_failure_reason"] == "source_not_fetched"


def test_top1_cited_elsewhere_and_c2_vague_duration():
    r = rep("02_in_vivo_gaps")
    assert r["dose"]["status"] == "not_reported" and r["dose"]["not_reported_reason"] == "cited_elsewhere"
    d = r["duration"]
    assert d["not_reported_reason"] == "vague_term_only" and d["duration_category"] == "chronic"
    assert d["duration_category_verbatim"].lower() == "chronic"


def test_p2_government_program_and_product_content_missing():
    rec = ex("02_in_vivo_gaps")["record"]
    assert rec["reporting"]["supplier"]["not_reported_reason"] == "government_program_unnamed"
    assert rec["fields"]["supplier_program_verbatim"]["value"] == "national drug supply program"
    assert "supplier" not in rec["fields"]
    assert rec["reporting"]["major_cannabinoids"]["not_reported_reason"] == "product_named_content_missing"
    assert "cannabinoids" not in rec["fields"]  # never filled from a product label


def test_top3_missing_units_and_c1_protocol_and_range():
    assert rep("03_in_vivo_missing_units")["dose"]["not_reported_reason"] == "missing_units"
    assert rep("04_clinical_rct")["dose"]["not_reported_reason"] == "protocol_without_number"
    rec = ex("01_in_vivo_full")["record"]
    assert rec["reporting"]["dose"]["status"] == "reported"  # C1: 5-20 mg/kg range counts
    assert rec["fields"]["dose_thc_mg_kg"]["value"] is None  # range: no single number invented


def test_figure_and_table_reasons():
    assert rep("02_in_vivo_gaps")["number_of_animals"]["check_failure_reason"] == "figure_not_read"
    assert rep("11_in_vivo_table_low_conf")["dose"]["check_failure_reason"] == "low_confidence_read"


def test_comparator_and_confidence():
    c = rep("04_clinical_rct")["comparison_group"]
    assert c["extraction_confidence"] == "high" and c["comparator_validated"] is False
    w = rep("05_clinical_weak_comparator")["comparison_group"]
    assert w["needs_review"] and w["review_reason"] == "no_named_control_arm" and w["comparator_validated"] is False
    assert rep("04_clinical_rct")["supplier"] == {"status": "not_applicable"}


def test_group_sizes_mesh_value_source():
    rec = ex("01_in_vivo_full")["record"]
    assert rec["reporting"]["group_sample_sizes"] == [
        {"group_label": "vehicle", "n": 8, "unit": "animals"}, {"group_label": "high-dose", "n": 8, "unit": "animals"}]
    assert rec["mesh_from_pubmed"] == ["D000000001"] and "conditions" not in rec["reporting"]
    for v in rec["reporting"].values():
        if isinstance(v, dict) and v["status"] in ("reported", "not_reported"):
            assert v["value_source"] == "paper" and "source_attribution" not in v
    assert "legacy_llm_fullpdf" not in json.dumps(rec)


def test_q7_excluded_and_no_tiers():
    assert ex("07_survey_excluded") == {"record": None, "excluded": "not_original_research_survey", "study_type": None}
    rec = ex("01_in_vivo_full")["record"]
    assert not re.search(r'"(grade|tier|gold|near_gold)"', json.dumps(rec))


def test_route_values_are_vocab_only():
    vocab = {v["value"] for v in json.loads((ROOT / "schemas/normalization_rules.json").read_text())["vocabularies"]["route"]["values"]}
    cfg = extractor.load_config()
    assert {v for v, _ in cfg["route"]} <= vocab
    assert not {"smoke", "vapor"} & {v for v, _ in cfg["route"]}


def test_provenance_has_no_contact_or_key():
    rec = ex("01_in_vivo_full")["record"]
    blob = json.dumps(rec).lower()
    assert "email" not in blob and "api_key" not in blob and "@" not in blob
    assert rec["provenance"]["model_id"] is None


def test_guard_refuses_test_label_paths(tmp_path):
    for bad in ["tests/golden_dataset.json", "/workspace/crn-eval/query-set-v0.qrels-draft.csv",
                "/workspace/crn-eval/split-manifest-v0.1.frozen.csv", "/workspace/crn-eval/manifest_pmids.txt", "x/grades.csv"]:
        with pytest.raises(benchmark.Refused):
            benchmark.read_pmids(bad)
    assert benchmark.read_pmids(tmp_path / "dev_pmids.txt") is None
    (tmp_path / "dev_pmids.txt").write_text("900000001\n# c\n900000002\n")
    assert benchmark.read_pmids(tmp_path / "dev_pmids.txt") == ["900000001", "900000002"]


def test_pending_when_dev_file_missing(tmp_path, capsys):
    assert benchmark.main(["--pmids", str(tmp_path / "dev_pmids.txt")]) == 2
    assert '"pending"' in capsys.readouterr().out


def test_pmc_parser_drops_supplements_and_pii():
    xml = b"""<article><front><article-meta><contrib-group><contrib><email>a@b.c</email></contrib></contrib-group>
    <abstract><p>Abs.</p></abstract></article-meta></front><body>
    <sec><title>Materials and methods</title><p>Rats got 5 mg/kg THC.</p>
    <fig><caption><p>Fig 1 doses.</p></caption></fig><supplementary-material><p>SECRET supp</p></supplementary-material></sec>
    <sec><title>Results</title><p>R.</p><table-wrap><table><tr><td>THC 5 mg/kg</td></tr></table></table-wrap></sec></body></article>"""
    secs = sources.parse_pmc_xml(xml)
    types = [s["type"] for s in secs]
    assert {"abstract", "methods", "results", "table", "figure_caption"} == set(types)
    assert "SECRET" not in json.dumps(secs) and "a@b.c" not in json.dumps(secs)
    with pytest.raises(ValueError):
        sources.parse_pmc_xml(b"<article><body>")


def test_fetch_failures_map_to_contract_reasons():
    def boom(*a, **k):
        raise OSError("offline")
    out = sources.fetch_main_text(["900000001"], opener=boom)
    assert out["900000001"]["status"] == "fetch_failed"
    assert sources.MAX_IDS <= 200 and sources.MIN_INTERVAL >= 1 / 3 and sources.TOOL == "crn_pipeline"
