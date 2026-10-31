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


def test_pmids_allowlist_only(tmp_path, monkeypatch):
    for bad in ["tests/golden_dataset.json", "/workspace/crn-eval/query-set-v0.qrels-draft.csv",
                "/workspace/crn-eval/split-manifest-v0.1.frozen.csv", "/workspace/crn-eval/manifest_pmids.txt",
                "/workspace/crn-reliability/legacy_llm_fullpdf.csv", str(tmp_path / "dev_pmids.txt"),
                "/workspace/crn-eval/../crn-eval/other.txt"]:
        with pytest.raises(benchmark.Refused):
            benchmark.read_pmids(bad)
    assert {str(p) for p in benchmark.ALLOWED_PMID_FILES} == {
        "/workspace/crn-eval/dev_pmids.txt", "/workspace/crn-eval/legacy32_pmids.txt"}
    dev, leg = tmp_path / "dev_pmids.txt", tmp_path / "legacy32_pmids.txt"
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (dev, leg))
    assert benchmark.read_pmids(dev) is None
    dev.write_text("900000001\n# c\n900000002\n")
    leg.write_text("900000003\n")
    assert benchmark.read_pmids(dev) == ["900000001", "900000002"]
    assert benchmark.read_pmids(leg) == ["900000003"]
    leg.write_text("900000003,some_value\n")
    with pytest.raises(benchmark.Refused):
        benchmark.read_pmids(leg)


def test_pmids_allowlist_symlinks_refused_dotdot_ok(tmp_path, monkeypatch):
    real = tmp_path / "eval" / "dev_pmids.txt"
    real.parent.mkdir()
    real.write_text("900000001\n")
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (real,))
    assert benchmark.read_pmids(tmp_path / "eval" / ".." / "eval" / "dev_pmids.txt") == ["900000001"]
    alias = tmp_path / "alias.txt"  # symlink to an allowlisted file: not on the allowlist itself
    alias.symlink_to(real)
    with pytest.raises(benchmark.Refused):
        benchmark.read_pmids(alias)


def test_allowlisted_entry_that_is_a_symlink_is_refused(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("900000009\n")
    dev = tmp_path / "dev_pmids.txt"
    dev.symlink_to(elsewhere)
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (dev,))
    with pytest.raises(benchmark.Refused):
        benchmark.read_pmids(dev)
    with pytest.raises(benchmark.Refused):  # O_NOFOLLOW backstop even if the check is bypassed
        benchmark._read_nofollow(dev, dev.parent)
    d = tmp_path / "dir_pmids.txt"
    d.mkdir()
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (d,))
    with pytest.raises(benchmark.Refused):
        benchmark.read_pmids(d)


def test_symlinked_middle_folder_below_root_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "evalroot"
    root.mkdir()
    other = tmp_path / "other_dir"
    other.mkdir()
    (other / "dev_pmids.txt").write_text("900000009\n")
    (root / "mid").symlink_to(other, target_is_directory=True)
    dev = root / "mid" / "dev_pmids.txt"
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (dev,))
    monkeypatch.setattr(benchmark, "ALLOWLIST_ROOT", root)
    with pytest.raises(benchmark.Refused):
        benchmark.read_pmids(dev)
    monkeypatch.setattr(benchmark, "_refuse_symlink_components", lambda p, r: True)  # fd walk alone
    with pytest.raises(benchmark.Refused):
        benchmark._read_nofollow(dev, root)


def test_eval_subdir_symlink_to_secret_outside_root_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "crn-eval"
    root.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "dev_pmids.txt").write_text("900000007\n")
    (root / "eval").symlink_to(secret, target_is_directory=True)
    dev = root / "eval" / "dev_pmids.txt"
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (dev,))
    monkeypatch.setattr(benchmark, "ALLOWLIST_ROOT", root)
    with pytest.raises(benchmark.Refused):
        benchmark._refuse_symlink_components(dev, root)
    with pytest.raises(benchmark.Refused):
        benchmark._read_nofollow(dev, root)
    with pytest.raises(benchmark.Refused):
        benchmark.read_pmids(dev)


def test_symlinked_ancestor_above_root_is_accepted(tmp_path, monkeypatch):
    real = tmp_path / "real_ws" / "crn-eval"
    real.mkdir(parents=True)
    (real / "dev_pmids.txt").write_text("900000001\n")
    (tmp_path / "workspace").symlink_to(tmp_path / "real_ws", target_is_directory=True)
    dev = tmp_path / "workspace" / "crn-eval" / "dev_pmids.txt"
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (dev,))
    assert benchmark.read_pmids(dev) == ["900000001"]  # outside default root: falls back to parent
    monkeypatch.setattr(benchmark, "ALLOWLIST_ROOT", tmp_path / "workspace" / "crn-eval")
    assert benchmark.read_pmids(dev) == ["900000001"]


def test_nested_real_dirs_accepted(tmp_path, monkeypatch):
    dev = tmp_path / "a" / "b" / "c" / "dev_pmids.txt"
    dev.parent.mkdir(parents=True)
    dev.write_text("900000001\n900000002\n")
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (dev,))
    assert benchmark.read_pmids(dev) == ["900000001", "900000002"]
    missing = tmp_path / "nope" / "dev_pmids.txt"
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (missing,))
    assert benchmark.read_pmids(missing) is None


def test_pending_when_dev_file_missing(tmp_path, monkeypatch, capsys):
    dev = tmp_path / "dev_pmids.txt"
    monkeypatch.setattr(benchmark, "ALLOWED_PMID_FILES", (dev,))
    assert benchmark.main(["--pmids", str(dev)]) == 2
    assert '"pending"' in capsys.readouterr().out


def test_out_path_inside_repo_refused(tmp_path):
    for bad in [ROOT / "out.json", ROOT / "tests" / "x.json", "out.json"]:
        with pytest.raises(benchmark.Refused):
            benchmark.main(["--fixtures", str(FIX), "--out", str(bad)])
    good = tmp_path / "o.json"
    assert benchmark.main(["--fixtures", str(FIX), "--out", str(good)]) == 0 and good.exists()


def test_fixtures_dir_must_be_repo_fixtures(tmp_path):
    with pytest.raises(benchmark.Refused):
        benchmark.load_fixtures(tmp_path)


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


# ---- fetch hardening (network blocked: every test uses a fake opener) ----
import io
import logging
import urllib.error


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("network access in tests")
    monkeypatch.setattr(sources.urllib.request, "urlopen", blocked)
    monkeypatch.setattr(sources, "_sleep", lambda s: None)


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def link(pmid, pmc):
    return f"<eLinkResult><LinkSet><IdList><Id>{pmid}</Id></IdList><LinkSetDb><Link><Id>{pmc}</Id></Link></LinkSetDb></LinkSet></eLinkResult>".encode()


ARTICLE = b"<article><body><sec><title>Methods</title><p>Rats got 5 mg/kg THC.</p></sec></body></article>"


def test_fetch_failures_map_to_contract_reasons():
    def boom(*a, **k):
        raise OSError("offline")
    out = sources.fetch_main_text(["900000001"], opener=boom)
    assert out["900000001"]["status"] == "fetch_failed"
    assert sources.MAX_IDS <= 200 and sources.MIN_INTERVAL >= 1 / 3 and sources.TOOL == "crn_pipeline"


def test_defusedxml_rejects_entity_bombs():
    bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "xxxxxxxx"><!ENTITY y "&x;&x;&x;">]><article><body><p>&y;</p></body></article>'
    with pytest.raises(ValueError):
        sources.parse_pmc_xml(bomb)
    ext = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY e SYSTEM "file:///etc/passwd">]><article>&e;</article>'
    with pytest.raises(ValueError):
        sources.parse_pmc_xml(ext)
    assert sources.ET.__name__.startswith("defusedxml")


def test_response_size_cap(monkeypatch):
    monkeypatch.setattr(sources, "MAX_BYTES", 50)
    big = b"<a>" + b"x" * 100 + b"</a>"
    with pytest.raises(sources.FetchError):
        sources._get("efetch.fcgi", {}, opener=lambda *a, **k: Resp(big))
    with pytest.raises(ValueError):
        sources.parse_pmc_xml(big)

    def op(url, timeout):
        return Resp(link("900000001", "1") if "elink" in url else big)
    monkeypatch.setattr(sources, "MAX_BYTES", 300)
    big = b"<article>" + b"x" * 400 + b"</article>"
    assert sources.fetch_main_text(["900000001"], opener=op)["900000001"]["status"] == "fetch_failed"


def test_retry_429_5xx_with_backoff(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(sources, "_sleep", sleeps.append)

    def op(url, timeout):
        calls.append(url)
        if len(calls) <= 3:
            raise urllib.error.HTTPError(url, [429, 503, 500][len(calls) - 1], "x", {}, None)
        return Resp(b"<ok/>")
    assert sources._get("efetch.fcgi", {}, opener=op) == b"<ok/>"
    backoffs = [s for s in sleeps if s >= sources.BACKOFF]
    assert backoffs == [sources.BACKOFF, sources.BACKOFF * 2, sources.BACKOFF * 4]
    calls.clear()

    def notfound(url, timeout):
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "x", {}, None)
    with pytest.raises(sources.FetchError):
        sources._get("efetch.fcgi", {}, opener=notfound)
    assert len(calls) == 1  # 4xx other than 429 is not retried

    def always503(url, timeout):
        raise urllib.error.HTTPError(url, 503, "x", {}, None)
    with pytest.raises(sources.FetchError):
        sources._get("efetch.fcgi", {}, opener=always503)


def test_throttle_keeps_under_3_per_second(monkeypatch):
    t = [100.0]
    slept = []
    monkeypatch.setattr(sources.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(sources, "_sleep", lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    sources._last[0] = 0.0
    for _ in range(4):
        sources._throttle()
    assert all(abs(s - sources.MIN_INTERVAL) < 1e-9 for s in slept) and len(slept) == 3


def test_one_failed_lookup_marks_only_that_paper(caplog):
    def op(url, timeout):
        if "elink" in url:
            ids = [v for k, v in (kv.split("=") for kv in url.split("?")[1].split("&")) if k == "id"]
            if "900000002" in ids:
                raise urllib.error.HTTPError(url, 400, "bad", {}, None)
            return Resp(link(ids[0], "1" + ids[0][-1]))
        return Resp(ARTICLE)
    with caplog.at_level(logging.WARNING):
        out = sources.fetch_main_text(["900000001", "900000002", "900000003"], opener=op)
    assert out["900000002"]["status"] == "fetch_failed"
    assert out["900000001"]["status"] == "ok" and out["900000003"]["status"] == "ok"
    assert "FetchError" in caplog.text  # exception type is logged


def test_email_from_env_never_in_provenance(monkeypatch):
    monkeypatch.delenv("CRN_NCBI_EMAIL", raising=False)
    assert sources.ncbi_email() == sources.PLACEHOLDER_EMAIL
    monkeypatch.setenv("CRN_NCBI_EMAIL", "someone@example.org")
    seen = []

    def op(url, timeout):
        seen.append(url)
        return Resp(link("900000001", "1") if "elink" in url else ARTICLE)
    got = sources.fetch_main_text(["900000001"], opener=op)
    assert all("email=someone%40example.org" in u and "tool=crn_pipeline" in u for u in seen)
    rec = extractor.extract(dict(paper_id=900000001, pmid="900000001", **got["900000001"]))["record"]
    assert "example.org" not in json.dumps(rec) and "example.org" not in json.dumps(got)
