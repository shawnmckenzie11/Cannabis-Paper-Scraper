import csv, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from csv_safety import csv_safe_cell as f


def test_formula_prefixes_neutralized():
    for v in ["=HYPERLINK(\"x\")", "+1+1", "-2+3", "@SUM(A1)", "\tx", "\rx"]:
        assert f(v) == "'" + v


def test_leading_space_formula_neutralized():
    for v in ["  =HYPERLINK(\"x\")", " @SUM(A1)", "\t=1+1"]:
        assert f(v) == "'" + v


def test_numeric_strings_untouched():
    for v in ["-0.5", "+3", " -12", "1e5"]:
        assert f(v) == v


def test_plain_values_untouched():
    for v in ["Cannabis and sleep", "", None, 12, 0.5, "a=b"]:
        assert f(v) == v


def test_search_db_export_csv_guarded(tmp_path):
    import types
    stubs = {"db_manager": types.SimpleNamespace(DatabaseManager=object),
             "extractor": types.SimpleNamespace(format_study_duration=str)}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    sys.modules.pop("search_db", None)
    try:
        import search_db
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    out = tmp_path / "o.csv"
    search_db.export_csv([{"title": "  =HYPERLINK(\"x\")", "score": "-0.5"}], str(out))
    row = next(csv.DictReader(open(out)))
    assert row["title"].startswith("'")
    assert row["score"] == "-0.5"
