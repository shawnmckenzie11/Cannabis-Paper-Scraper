"""Topic gate in search/count/CSV, and read-only methods-contract presentation (fake data only)."""
import csv
import hashlib
import io
import json
import os
import pathlib
import re
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


def _client(monkeypatch, db, chart):
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
    return client


def _csv(monkeypatch, db, chart):
    resp = _client(monkeypatch, db, chart).get("/api/analyses/5/export-csv")
    assert resp.status_code == 200, resp.data
    return list(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))


def _analysis_view(monkeypatch, db, chart):
    resp = _client(monkeypatch, db, chart).get("/api/analyses/5/papers")
    assert resp.status_code == 200, resp.data
    return resp.get_json()


def test_csv_export_uses_same_gate_filter_path(monkeypatch, gated_db):
    db, _ = gated_db
    rows = _csv(monkeypatch, db, {})
    assert _titles(rows) == {"Gate true cannabis paper", "Gate null unassessed paper"}
    assert set(mv.CSV_COLUMNS) <= set(rows[0])


def test_csv_export_uses_same_gate_paper_ids_path(monkeypatch, gated_db):
    db, ids = gated_db
    rows = _csv(monkeypatch, db, {"paper_ids": list(ids.values())})
    assert _titles(rows) == {"Gate true cannabis paper", "Gate null unassessed paper"}


def test_analysis_view_hides_false_keeps_null_and_true(monkeypatch, gated_db):
    db, ids = gated_db
    body = _analysis_view(monkeypatch, db, {"paper_ids": list(ids.values())})
    assert _titles(body["papers"]) == {"Gate true cannabis paper", "Gate null unassessed paper"}
    assert body["total_count"] == 2 and body["truncated"] is False
    assert _titles(db.search_papers_by_ids(list(ids.values()))) == _titles(body["papers"])


class _PgStyleInspector:
    """Column listing as Postgres reports it; any PRAGMA reaching the engine fails the test."""

    def __init__(self, engine):
        self.engine = engine

    def get_columns(self, table):
        import sqlite3
        conn = sqlite3.connect(self.engine.url.database)
        try:
            return [{"name": r[1]} for r in conn.execute(f"PRAGMA table_info({table})")]
        finally:
            conn.close()


def test_pg_style_connection_without_pragma_still_filters(monkeypatch, gated_db):
    from sqlalchemy import event
    import repository.papers as rp
    db, ids = gated_db
    repo = db._papers_repo()
    repo._topic_match_cache = None
    monkeypatch.setattr(rp, "inspect", _PgStyleInspector)

    def no_pragma(conn, cursor, statement, *a):
        assert "PRAGMA" not in statement.upper(), statement
    event.listen(repo.engine, "before_cursor_execute", no_pragma)
    try:
        all_ids = list(ids.values())
        assert repo.topic_match_column_exists() is True
        assert _titles(_csv(monkeypatch, db, {"paper_ids": all_ids})) == {"Gate true cannabis paper", "Gate null unassessed paper"}
        assert _titles(_analysis_view(monkeypatch, db, {"paper_ids": all_ids})["papers"]) == {
            "Gate true cannabis paper", "Gate null unassessed paper"}
    finally:
        event.remove(repo.engine, "before_cursor_execute", no_pragma)


def test_inspection_failure_is_not_swallowed(monkeypatch, gated_db):
    import repository.papers as rp
    db, _ = gated_db
    repo = db._papers_repo()
    repo._topic_match_cache = None

    def boom(_engine):
        raise RuntimeError("inspect failed")
    monkeypatch.setattr(rp, "inspect", boom)
    with pytest.raises(RuntimeError):
        repo.visible_paper_ids([1, 2, 3])


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

def test_condition_json_runtime_pins_match_committed_files():
    assert hashlib.sha256(mv.CONDITION_LIST_PATH.read_bytes()).hexdigest() == mv.CONDITION_LIST_SHA256 == \
        "4d62e3d632d7ba37ccb3a9fc7dd2d629fc880163aa2ef9437cc1ddcf2182a16c"
    assert hashlib.sha256(mv.CONDITION_ROLLUP_PATH.read_bytes()).hexdigest() == mv.CONDITION_ROLLUP_SHA256 == \
        "fe996d39dcd587300e460f330bc4139da96575cb238121853b170db2168512f9"
    assert mv.load_conditions() is not None and mv.condition_filter_enabled()


def test_condition_json_loaded_once(monkeypatch):
    mv._load_conditions_cached.cache_clear()
    calls = []
    real = mv._read_pinned
    monkeypatch.setattr(mv, "_read_pinned", lambda *a: calls.append(a) or real(*a))
    for _ in range(3):
        mv.expand_condition("neoplasms")
    assert len(calls) == 2
    mv._load_conditions_cached.cache_clear()


@pytest.mark.parametrize("case", ["mismatch", "missing"])
def test_condition_json_bad_file_disables_filter(tmp_path, caplog, case):
    lst = tmp_path / "condition_list.json"
    if case == "mismatch":
        lst.write_bytes(mv.CONDITION_LIST_PATH.read_bytes() + b" ")
    with caplog.at_level("ERROR", logger="methods_view"):
        assert mv.load_conditions(lst, mv.CONDITION_ROLLUP_PATH) is None
    assert "condition filter disabled" in caplog.text
    mv._load_conditions_cached.cache_clear()


def test_condition_filter_disabled_is_ignored_not_500(monkeypatch, caplog):
    monkeypatch.setattr(mv, "CONDITION_LIST_PATH", pathlib.Path("/nonexistent/condition_list.json"))
    monkeypatch.setattr(mv.load_conditions, "__defaults__", (mv.CONDITION_LIST_PATH, mv.CONDITION_ROLLUP_PATH))
    mv._load_conditions_cached.cache_clear()
    assert mv.expand_condition("neoplasms") == set()
    assert mv.matches_filters(RECORD, condition_keys=["neoplasms"]) is True
    mv._load_conditions_cached.cache_clear()


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


def test_escapehtml_legacy_callers_unchanged():
    html = (ROOT / "templates" / "index.html").read_text()
    start = html.index("// Methods contract (#117)")
    end = html.index("function escapeHtml(text)", start)
    outside = html[:start] + html[end + len("function escapeHtml(text)"):]
    assert not re.search(r"(?<![\w.])escapeHtml\(", outside.replace("escapeHtml(text || \"\")", ""))
    assert "function escapeHtmlLegacy(text)" in html


def test_template_escapes_and_never_computes_tier():
    html = (ROOT / "templates" / "index.html").read_text()
    assert 'String(text ?? "")' in html
    start = html.index("// Methods contract (#117)")
    block = html[start:html.index("function escapeHtml", start)]
    assert "compute(" not in block and "tier-badge" not in block
    assert "Earlier AI classification, not reviewed" in block
    assert 'id="card-methods"' in html and 'aria-labelledby="label-methods"' in html
    assert 'tabindex="-1"' not in block
