"""PR #89 Reliability review: env precedence, startup-only analyses migration,
gated destructive reclassify. Fake data only."""
import ast
import pathlib
import sqlite3
import subprocess
import os

import pytest

import boot_safety
import reclassify_metadata
from db_manager import DatabaseManager

REPO = pathlib.Path(__file__).resolve().parent.parent


# (a) APP_ENV decides alone when set
def _prod_env(monkeypatch):
    for v in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "FLASK_SECRET_KEY",
              "RATELIMIT_STORAGE_URI", "RATE_LIMIT_SINGLE_INSTANCE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FLASK_ENV", "development")


def test_app_env_production_overrides_flask_env_dev_secret_check(monkeypatch):
    _prod_env(monkeypatch)
    assert boot_safety.is_dev_env() is False
    with pytest.raises(boot_safety.StartupConfigError, match="FLASK_SECRET_KEY"):
        boot_safety.validate_startup_env()


def test_app_env_production_overrides_flask_env_dev_limiter_check(monkeypatch):
    _prod_env(monkeypatch)
    with pytest.raises(boot_safety.StartupConfigError, match="RATELIMIT_STORAGE_URI"):
        boot_safety.resolve_rate_limit_storage_uri()


def test_app_py_env_check_same_precedence():
    tree = ast.parse((REPO / "app.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_is_dev_or_test_env")
    ns = {"os": os, "_DEV_ENV_NAMES": {"dev", "development", "local", "test", "testing"}}
    exec(compile(ast.Module([fn], []), "app_fn", "exec"), ns)
    env = dict(os.environ)
    try:
        os.environ["APP_ENV"] = "production"; os.environ["FLASK_ENV"] = "development"
        assert ns["_is_dev_or_test_env"]() is False
        os.environ.pop("APP_ENV"); assert ns["_is_dev_or_test_env"]() is True
    finally:
        os.environ.clear(); os.environ.update(env)


def test_flask_env_used_only_when_app_env_unset(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("FLASK_ENV", "development")
    assert boot_safety.is_dev_env() is True


# (b) analyses migration is startup-only
def test_request_paths_do_not_run_analyses_migration():
    tree = ast.parse((REPO / "app.py").read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "init_analyses_table"]
    assert calls == [], "app.py request handlers must not call init_analyses_table"


def test_analyses_migration_runs_once_at_startup(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    calls = []
    orig = DatabaseManager.init_analyses_table
    monkeypatch.setattr(DatabaseManager, "init_analyses_table",
                        lambda self: (calls.append(1), orig(self))[1])
    monkeypatch.setattr(DatabaseManager, "_initialized", False)
    db = str(tmp_path / "fake.db")
    DatabaseManager(db)
    first = len(calls)
    assert first >= 1
    DatabaseManager(db); DatabaseManager(db)  # per-request constructions
    assert len(calls) == first


# (c) destructive reclassify is gated
@pytest.mark.parametrize("env,allowed", [
    ({}, False),
    ({"ALLOW_RECLASSIFY_PURGE": "1"}, False),
    ({"ALLOW_RECLASSIFY_PURGE": "1", "APP_ENV": "production", "FLASK_ENV": "development"}, False),
    ({"APP_ENV": "development"}, False),
    ({"ALLOW_RECLASSIFY_PURGE": "1", "APP_ENV": "development"}, True),
])
def test_reclassify_gate(monkeypatch, env, allowed):
    for v in ("ALLOW_RECLASSIFY_PURGE", "APP_ENV", "FLASK_ENV"):
        monkeypatch.delenv(v, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert reclassify_metadata.reclassify_allowed() is allowed


def test_reclassify_script_refuses_and_keeps_rows(tmp_path):
    db = tmp_path / "fake.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT)")
    con.execute("INSERT INTO papers (title) VALUES ('fake-paper-1')")
    con.commit(); con.close()
    env = {k: v for k, v in os.environ.items()
           if k not in ("ALLOW_RECLASSIFY_PURGE", "APP_ENV", "FLASK_ENV", "DATABASE_URL")}
    env.update(DATABASE_PATH=str(db), APP_ENV="production", PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([os.sys.executable, str(REPO / "reclassify_metadata.py")],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "refused" in (r.stdout + r.stderr)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1


def test_entrypoint_skips_reclassify_without_opt_in(tmp_path):
    shim = tmp_path / "shim"; shim.mkdir()
    py = shim / "python3"
    py.write_text("#!/bin/sh\necho \"[shim] python3 $*\"\necho 0\nexit 0\n"); py.chmod(0o755)
    data = tmp_path / "data"; data.mkdir()
    db = data / "fake.db"
    sqlite3.connect(db).execute("CREATE TABLE papers (id INTEGER PRIMARY KEY)").connection.commit()
    ep = tmp_path / "entrypoint.sh"
    ep.write_text((REPO / "entrypoint.sh").read_text().replace("/data/calibration_runs", str(tmp_path / "cal")))
    env = {"PATH": f"{shim}:{os.environ['PATH']}", "DATABASE_PATH": str(db), "CHEAP_OPS": "0",
           "HOME": str(tmp_path), "APP_ENV": "production"}
    r = subprocess.run(["sh", str(ep), "true"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "Heuristic reclassify skipped" in r.stdout
    assert "reclassify_metadata.py" not in r.stdout
