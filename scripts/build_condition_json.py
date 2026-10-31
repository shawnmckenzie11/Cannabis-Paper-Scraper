"""Rebuild schemas/condition_list.json and schemas/condition_rollup.json from the pinned CSVs.

Entries and rollup rows are derived from the CSVs; the header metadata (approval, comments,
entry_shape, ...) is taken from the committed JSON. Output is byte-identical to the committed
files (json.dumps indent=2, ASCII, trailing newline). Used by tests/test_methods_contract_v08.py.

    python scripts/build_condition_json.py [--csv-dir DIR] [--write]
"""
import argparse, csv, hashlib, io, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
CSV_DIR = SCHEMAS / "condition_sources"
LIST_CSV = "condition-list-v0.1-approved.csv"
ROLLUP_CSV = "condition-rollup-v0.1.csv"


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def _key(term):
    return re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")


def _split(s):
    return [x.strip() for x in s.split(";") if x.strip()]


def _dump(obj):
    return (json.dumps(obj, indent=2) + "\n").encode()


def build(csv_dir=CSV_DIR, schemas=SCHEMAS):
    """Return (list_bytes, rollup_bytes) rebuilt from the CSVs in csv_dir."""
    csv_dir = pathlib.Path(csv_dir)
    lraw, rraw = (csv_dir / LIST_CSV).read_bytes(), (csv_dir / ROLLUP_CSV).read_bytes()
    clist = json.loads((pathlib.Path(schemas) / "condition_list.json").read_text())
    croll = json.loads((pathlib.Path(schemas) / "condition_rollup.json").read_text())
    if sha256(lraw) != clist["approval"]["approved_csv_sha256"] or sha256(rraw) != croll["source_csv_sha256"]:
        raise ValueError("CSV sha256 does not match the pin in the committed JSON")
    entries = [{"key": _key(r["preferred_term"]), "mesh_descriptor_id": r["descriptor_ui"],
                "preferred_term": r["preferred_term"],
                "tree_numbers": _split(r["c_tree_numbers"]) + _split(r["f03_tree_numbers"]),
                "entry_terms": _split(r["entry_terms"])}
               for r in csv.DictReader(io.StringIO(lraw.decode()))]
    rows = [{"child_mesh_descriptor_id": r["child_descriptor_ui"], "child_preferred_term": r["child_preferred_term"],
             "child_tree_number": r["child_tree_number"], "parent_mesh_descriptor_id": r["parent_descriptor_ui"],
             "parent_preferred_term": r["parent_preferred_term"], "parent_tree_number": r["parent_tree_number"]}
            for r in csv.DictReader(io.StringIO(rraw.decode()))]
    out_list = {k: (entries if k == "entries" else v) for k, v in clist.items()}
    out_roll = {k: (rows if k == "rows" else v) for k, v in croll.items()}
    return _dump(out_list), _dump(out_roll)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", default=str(CSV_DIR))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    lb, rb = build(a.csv_dir)
    for name, b in (("condition_list.json", lb), ("condition_rollup.json", rb)):
        same = (SCHEMAS / name).read_bytes() == b
        print(name, sha256(b), "matches" if same else "DIFFERS")
        if a.write and not same:
            (SCHEMAS / name).write_bytes(b)
    sys.exit(0)
