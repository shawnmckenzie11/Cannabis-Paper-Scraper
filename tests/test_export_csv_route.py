import csv, io, sys, pathlib
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
app_module = pytest.importorskip("app")


class FakeDB:
    def get_user_by_id(self, uid):
        return {"id": 1, "email": "t@example.com"}

    def get_analysis(self, aid):
        return {"id": aid, "user_id": 1, "name": "t", "filter_settings": "{}", "chart_data": "{}"}

    def search_papers(self, filters):
        return [{"id": 7, "title": "=cmd|' /C calc'!A0", "year": 2020, "dose_mg": "-0.5"}]


def test_export_csv_route_guards_formulas(monkeypatch):
    monkeypatch.setattr(app_module, "DatabaseManager", FakeDB)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = 1
        s["logged_in"] = True
    resp = client.get("/api/analyses/5/export-csv")
    assert resp.status_code == 200, resp.data
    row = next(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))
    assert row["title"] == "'=cmd|' /C calc'!A0"
    assert row["dose_mg"] == "-0.5"
