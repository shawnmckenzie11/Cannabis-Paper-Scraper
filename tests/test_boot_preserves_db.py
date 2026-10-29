"""Regression: boot (entrypoint.sh) must never wipe or silently re-seed the prod DB.

Standalone pytest. Runs the repo's entrypoint.sh with the server stubbed out:
entrypoint ends with `"$@" &` / `wait`, so we pass `true` as the server command.
A shim dir is prepended to PATH so `python3 -m alembic ...` and any other python3
call is a no-op (only pre-start logic is exercised). CHEAP_OPS=1 skips the
background jobs.

Env:
  CRN_REPO_ROOT  repo checkout containing entrypoint.sh (default: this file's ../..,
                 i.e. works when dropped into <repo>/tests/)
"""
import hashlib
import os
import pathlib
import sqlite3
import subprocess

import pytest

HERE = pathlib.Path(__file__).resolve().parent
REPO = pathlib.Path(os.environ.get("CRN_REPO_ROOT", HERE.parent)).resolve()
ENTRYPOINT = REPO / "entrypoint.sh"
MARKER = "boot-regression-marker-7f3a"


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _make_db(path, title=MARKER):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT, classifier_version TEXT)")
    con.execute("INSERT INTO papers (title, classifier_version) VALUES (?, 'x')", (title,))
    con.commit()
    con.close()


def _run_boot(tmp_path, db_path, seed_in_cwd, extra_env=None):
    shim = tmp_path / "shim"
    shim.mkdir()
    py = shim / "python3"
    py.write_text("#!/bin/sh\necho \"[shim] python3 $*\"\nexit 0\n")
    py.chmod(0o755)
    cwd = tmp_path / "app"
    cwd.mkdir()
    if seed_in_cwd:  # the image ships cannabis_papers.db in the app dir
        _make_db(cwd / "cannabis_papers.db", title="seed-snapshot-row")
    env = {
        "PATH": f"{shim}:{os.environ['PATH']}",
        "DATABASE_PATH": str(db_path),
        "CHEAP_OPS": "1",
        "HOME": str(tmp_path),
    }
    env.update(extra_env or {})
    if not ENTRYPOINT.exists():
        pytest.skip(f"entrypoint.sh not found at {ENTRYPOINT}")
    # entrypoint hardcodes CALIBRATION_ARTIFACT_DIR=/data/calibration_runs; run a
    # copy with only that path redirected into tmp (all DB logic is untouched).
    ep = tmp_path / "entrypoint.sh"
    ep.write_text(ENTRYPOINT.read_text().replace("/data/calibration_runs", str(tmp_path / "calibration_runs")))
    r = subprocess.run(["sh", str(ep), "true"], cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=60)
    print(r.stdout, r.stderr)
    return r


@pytest.mark.parametrize("seed_in_cwd", [False, True], ids=["no-seed-file", "seed-file-present"])
def test_boot_preserves_small_existing_db(tmp_path, seed_in_cwd):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "cannabis_papers.db"
    _make_db(db)
    assert db.stat().st_size < 50_000_000
    before = _sha(db)

    r = _run_boot(tmp_path, db, seed_in_cwd)
    assert r.returncode == 0, r.stderr

    assert db.exists(), "entrypoint deleted the existing DB (<50 MB wipe)"
    rows = sqlite3.connect(db).execute("SELECT title FROM papers").fetchall()
    assert (MARKER,) in rows, "marker row lost: DB was replaced/re-seeded"
    assert _sha(db) == before, "DB content changed during pre-start"


def test_no_db_and_no_seed_flag_does_not_create_db(tmp_path):
    db = tmp_path / "data" / "cannabis_papers.db"
    r = _run_boot(tmp_path, db, seed_in_cwd=True,
                  extra_env={"FLASK_ENV": "production"})
    assert r.returncode == 0, r.stderr
    assert not db.exists(), "prod boot created/seeded a DB without an explicit seed flag"


@pytest.mark.skipif(not os.environ.get("CRN_TEST_DATABASE_URL"), reason="set CRN_TEST_DATABASE_URL to also test Postgres mode")
def test_database_url_mode_does_not_touch_sqlite(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "cannabis_papers.db"
    _make_db(db)
    before = _sha(db)
    r = _run_boot(tmp_path, db, seed_in_cwd=True,
                  extra_env={"DATABASE_URL": os.environ["CRN_TEST_DATABASE_URL"]})
    assert r.returncode == 0, r.stderr
    assert db.exists() and _sha(db) == before
