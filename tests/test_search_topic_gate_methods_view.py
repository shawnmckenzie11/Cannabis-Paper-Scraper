"""Topic gate in search/count/CSV, and read-only methods-contract presentation (fake data only)."""
import csv
import hashlib
import io
import json
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import methods_completeness as mc  # noqa: E402
import methods_view as mv  # noqa: E402
from repository.dialect import DialectSQL  # noqa: E402
from repository.filters import TOPIC_GATE_SQL, build_filter_clauses  # noqa: E402

FAKE = [("Gate true cannabis paper", 2), ("Gate null unassessed paper", None), ("Gate false off-topic paper", 0)]


@pytest.fixture
def gated_db(monkeypatch):
    from db_manager import DatabaseManager
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", tmp.name)
    DatabaseManager._initialized = False
    DatabaseManager._tab_flags_ready_cache = None
    db = DatabaseManager(db_path=tmp.name)
    ids = {}
    for i, (title, _) in enumerate(FAKE):
        ids[title] = db.insert_paper({"title": title, "abstract": "fake dev abstract", "pmid": str(990000 + i),
                                      "publication_type": "original research", "ingestion_status": "relevant",
                                      "date_harvested": "2026-01-01T00:00:00"})
    conn = db.get_connection()
    conn.execute("ALTER TABLE papers ADD COLUMN topic_match INTEGER")
    for title, tm in FAKE:
        conn.execute("UPDATE papers SET topic_match = ? WHERE id = ?", (tm, ids[title]))
    conn.commit()
    conn.close()
    db = DatabaseManager(db_path=tmp.name)
    yield db, ids
    DatabaseManager._initialized = False
    os.unlink(tmp.name)


def _titles(rows):
    return {r["title"] for r in rows}


def test_contract_tri_state_matches_sql():
    assert mc.topic_gate(None)["filtered_out"] is False
    assert mc.topic_gate(0)["filtered_out"] is True
    assert mc.topic_gate(2)["filtered_out"] is False
    assert "IS NULL OR papers.topic_match > 0" in TOPIC_GATE_SQL


def test_no_topic_match_column_hides_nothing():
    clauses, _ = build_filter_clauses({}, dialect=DialectSQL("sqlite"), tab_columns_exist=True)
    assert TOPIC_GATE_SQL not in clauses
    for name in ("sqlite", "postgresql"):
        clauses, _ = build_filter_clauses({}, dialect=DialectSQL(name), tab_columns_exist=True,
                                          topic_match_column_exists=True)
        assert clauses[0] == TOPIC_GATE_SQL


def test_search_keeps_true_and_null_hides_false(gated_db):
    db, _ = gated_db
    rows, total = db.search_papers({"limit": 50, "offset": 0}, include_total=True)
    assert _titles(rows) == {"Gate true cannabis paper", "Gate null unassessed paper"}
    assert total == 2 == db.count_papers({})


def test_search_with_query_still_gated(gated_db):
    db, _ = gated_db
    rows, total = db.search_papers({"query": "paper", "limit": 50, "offset": 0}, include_total=True)
    assert "Gate false off-topic paper" not in _titles(rows)
    assert total == len(rows) == 2


def _csv(monkeypatch, db, chart):
    import app as app_module
    from db_manager import DatabaseManager
    monkeypatch.setattr(DatabaseManager, "get_user_by_id", lambda self, uid: {"id": 1, "email": "t@example.com"}, raising=False)
    monkeypatch.setattr(DatabaseManager, "get_analysis", lambda self, aid: {
        "id": aid, "user_id": 1, "name": "t", "filter_settings": "{}", "chart_data": json.dumps(chart)})
    monkeypatch.setattr(app_module, "DatabaseManager", lambda *a, **k: db)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["logged_in"] = True
    resp = client.get("/api/analyses/5/export-csv")
    assert resp.status_code == 200, resp.data
    return list(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))


def test_csv_export_uses_same_gate_filter_path(monkeypatch, gated_db):
    db, _ = gated_db
    rows = _csv(monkeypatch, db, {})
    assert _titles(rows) == {"Gate true cannabis paper", "Gate null unassessed paper"}
    assert set(mv.CSV_COLUMNS) <= set(rows[0])


def test_csv_export_uses_same_gate_paper_ids_path(monkeypatch, gated_db):
    db, ids = gated_db
    rows = _csv(monkeypatch, db, {"paper_ids": list(ids.values())})
    assert _titles(rows) == {"Gate true cannabis paper", "Gate null unassessed paper"}


# --- badge ----------------------------------------------------------------------------------

def _computed(grade, uses_ref=False):
    return {"rule_id": "r", "rule_version": "9.9-test", "secondary_cutoff": 0.8,
            "universal": {"grade": grade}, "tier_uses_reference_values": uses_ref}


@pytest.mark.parametrize("tier", ["gold", "near_gold", "unverified", "not_scored", "out_of_scope", "not_yet_assessed"])
def test_badge_reads_computed_values(tier):
    b = mv.tier_badge(_computed(tier))
    assert b["tier"] == tier and b["known_tier"] and b["rule_version"] == "9.9-test" and b["cutoff"] == 0.8


def test_badge_reference_values_wording():
    assert mv.tier_badge(_computed("gold", True))["label"] == "gold, includes signed-off product values"


def test_badge_tolerates_unknown_tier_and_missing():
    b = mv.tier_badge(_computed("some_future_tier"))
    assert b["tier"] == "some_future_tier" and not b["known_tier"] and b["label"]
    assert mv.tier_badge(None) is None


def test_badge_from_real_compute_not_scored():
    out = mc.compute({}, "observational")
    assert mv.tier_badge(out)["tier"] == "not_scored"


# --- detail / csv ---------------------------------------------------------------------------

RECORD = {
    "fields": {"comparison_group": {"value": "<b>placebo</b>"}},
    "reporting": {
        "dose": {"status": "reported", "value_source": "paper"},
        "route": {"status": "reported", "value_source": "legacy_llm_fullpdf"},
        "duration": {"status": "reported", "duration_category": "chronic"},
        "comparison_group": {"status": "reported", "extraction_confidence": "medium", "confidence": 0.6},
        "experimental_model_verbatim": "<script>x</script> rat CCI model",
        "conditions": [{"condition_key": "neoplasms", "mesh_descriptor_id": "D009369", "label": "Neoplasms"}],
        "outcome_domains": [{"domain": "pain", "is_primary": True}, {"domain": "sleep", "is_primary": False}],
    },
}


def test_detail_view_labels_and_comparator():
    d = mv.detail_view(RECORD)
    src = {f["key"]: f for f in d["fields"]}
    assert src["route"]["value_source_label"] == "Earlier AI classification, not reviewed"
    assert src["route"]["counts_toward_badge"] is False and src["dose"]["counts_toward_badge"] is True
    assert src["duration"]["value_source"] == "paper"
    assert d["comparator"]["label"] == "Extracted, not yet validated"
    assert d["experimental_model_verbatim"] == "<script>x</script> rat CCI model"
    assert mv.value_source_label("future_source")


def test_csv_cells():
    cells = mv.csv_cells(RECORD)
    assert json.loads(cells["value_sources"])["route"] == "legacy_llm_fullpdf"
    assert cells["comparator_extraction_confidence"] == "medium" and cells["comparator_confidence"] == 0.6
    assert mv.csv_cells(None) == {c: "" for c in mv.CSV_COLUMNS}


# --- conditions -------------------------------------------------------------------------------

METHODS_DIR = pathlib.Path("/workspace/crn-methods")


def test_condition_json_records_approved_hashes():
    meta = json.loads(mv.CONDITION_LIST_PATH.read_text())
    assert meta["list_version"] == "0.1.0" and meta["mesh_release"] == "MeSH 2026"
    assert meta["approval"]["approved_csv_sha256"] == "40a9be45cab10ddd1dcdae7b479033cba632686468f834ba43eb846df05cadcc"
    assert meta["approval"]["rollup_csv_sha256"] == "16e12f4629e55fd45ea48fc327051958f89f23b2a0305a83994135d48eedeb48"


@pytest.mark.skipif(not METHODS_DIR.exists(), reason="approved CSVs only on the dev box")
def test_condition_csvs_verify():
    assert mv.verify_condition_sources(METHODS_DIR / "condition-list-v0.1-approved.csv",
                                       METHODS_DIR / "condition-rollup-v0.1.csv") == {"list": True, "rollup": True}


def test_parent_includes_rollup_children():
    ids = mv.expand_condition("neoplasms")
    assert {"D009369", "D000008"} <= ids  # Abdominal Neoplasms rolls up to Neoplasms


def test_study_design_headings_are_their_own_entries():
    clist, rollup = mv.load_conditions()
    for e in clist["entries"]:
        if e["mesh_descriptor_id"] in mv.STANDALONE_CONDITION_IDS:
            continue
        assert not (mv.expand_condition(e["key"], clist, rollup) & mv.STANDALONE_CONDITION_IDS), e["key"]
    assert mv.expand_condition("disease_models_animal", clist, rollup) >= {"D004195"}
    assert mv.expand_condition("prenatal_exposure_delayed_effects", clist, rollup) >= {"D011297"}
    assert mv.expand_condition("no_such_key", clist, rollup) == set()


def test_record_filters():
    child = dict(RECORD, reporting=dict(RECORD["reporting"], conditions=[
        {"condition_key": "x", "mesh_descriptor_id": "D000008", "label": "Abdominal Neoplasms"}]))
    assert mv.matches_filters(child, condition_keys=["neoplasms"])
    assert mv.matches_filters(RECORD, duration_categories=["chronic", "acute"])
    assert not mv.matches_filters(RECORD, duration_categories=["acute"])
    assert mv.matches_filters(RECORD, outcome_domains=["sleep"])
    assert not mv.matches_filters(RECORD, outcome_domains=["sleep"], primary_outcome_only=True)
    assert not mv.matches_filters({}, duration_categories=["acute"])


def test_template_escapes_and_never_computes_tier():
    html = (ROOT / "templates" / "index.html").read_text()
    assert 'String(text ?? "")' in html
    start = html.index("// Methods contract (#117)")
    block = html[start:html.index("function escapeHtml", start)]
    assert "compute" not in block.replace("computed", "")
    assert "Earlier AI classification, not reviewed" in block
    assert 'id="card-methods"' in html and 'aria-labelledby="label-methods"' in html
    assert 'tabindex="-1"' not in block
