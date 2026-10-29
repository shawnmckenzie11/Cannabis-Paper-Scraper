import ast, pathlib

def _load():
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app.py").read_text()
    tree = ast.parse(src)
    nodes = [n for n in tree.body if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_CSV_FORMULA_PREFIXES" for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name == "_csv_safe_cell")]
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), ns)
    return ns["_csv_safe_cell"]

def test_formula_prefixes_neutralized():
    f = _load()
    for v in ["=HYPERLINK(\"x\")", "+1", "-2+3", "@SUM(A1)", "\tx", "\rx"]:
        assert f(v) == "'" + v

def test_plain_values_untouched():
    f = _load()
    for v in ["Cannabis and sleep", "", None, 12, 0.5, "a=b"]:
        assert f(v) == v
