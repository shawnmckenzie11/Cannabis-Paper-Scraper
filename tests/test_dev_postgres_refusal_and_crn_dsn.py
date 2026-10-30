"""Dev/test runs refuse a Postgres DATABASE_URL; CRN Sentry reads only CRN_SENTRY_DSN."""
import pytest

import boot_safety
import sentry_setup

FAKE_PG = "postgresql://fake-user:fake-pw@db.invalid:5432/fake"


def test_dev_refuses_postgres(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", FAKE_PG)
    monkeypatch.delenv("CRN_ALLOW_LOCAL_POSTGRES", raising=False)
    with pytest.raises(boot_safety.StartupConfigError) as e:
        boot_safety.validate_startup_env()
    assert "fake-pw" not in str(e.value) and "db.invalid" not in str(e.value)


def test_dev_allows_postgres_when_opted_in(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", FAKE_PG)
    monkeypatch.setenv("CRN_ALLOW_LOCAL_POSTGRES", "1")
    boot_safety.validate_startup_env()


def test_dev_allows_sqlite(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///fake.db")
    boot_safety.validate_startup_env()


def test_shared_sentry_dsn_is_ignored():
    env = {"SENTRY_DSN": "https://fake@o0.ingest.invalid/1", "FLY_APP_NAME": "fake-app"}
    assert not sentry_setup.sentry_enabled(env)


def test_crn_sentry_dsn_enables_on_deployed_app():
    env = {"CRN_SENTRY_DSN": "https://fake@o0.ingest.invalid/1", "FLY_APP_NAME": "fake-app"}
    assert sentry_setup.sentry_enabled(env)


def test_dev_allows_localhost_postgres(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("CRN_ALLOW_LOCAL_POSTGRES", raising=False)
    for h in ("localhost", "127.0.0.1"):
        monkeypatch.setenv("DATABASE_URL", f"postgresql://fake:fake@{h}:5432/fake")
        boot_safety.validate_startup_env()
