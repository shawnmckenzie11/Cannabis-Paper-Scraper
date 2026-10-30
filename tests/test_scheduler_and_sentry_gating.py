"""Importing app must not start the harvest thread or report to Sentry outside a deployed app."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as m  # noqa: E402
import sentry_setup as ss  # noqa: E402


@pytest.mark.parametrize("env,expected", [
    ({}, False),
    ({"APP_ENV": "test"}, False),
    ({"APP_ENV": "development"}, False),
    ({"APP_ENV": "production"}, True),
    ({"APP_ENV": "staging"}, True),
    ({"FLY_APP_NAME": "fake-app"}, True),
    ({"FLY_APP_NAME": "fake-app", "ENABLE_INPROCESS_SCHEDULER": "0"}, False),
    ({"APP_ENV": "test", "ENABLE_INPROCESS_SCHEDULER": "1"}, True),
])
def test_scheduler_should_start(env, expected):
    assert m.scheduler_should_start(env) is expected


def test_sentry_off_outside_deployed_env():
    dsn = "https://fake@example.invalid/1"
    assert ss.sentry_enabled({"SENTRY_DSN": dsn}) is False
    assert ss.sentry_enabled({"SENTRY_DSN": dsn, "APP_ENV": "test"}) is False
    assert ss.sentry_enabled({"SENTRY_DSN": dsn, "APP_ENV": "production"}) is True
    assert ss.sentry_enabled({"SENTRY_DSN": dsn, "FLY_APP_NAME": "fake-app"}) is True
    assert ss.sentry_enabled({"SENTRY_DSN": dsn, "FLY_APP_NAME": "x", "CRN_SENTRY_DISABLED": "1"}) is False


def test_plain_import_starts_no_scheduler_thread():
    env = {k: v for k, v in os.environ.items()
           if k not in ("ENABLE_INPROCESS_SCHEDULER", "FLY_APP_NAME", "CRN_SENTRY_DISABLED")}
    env.update({"APP_ENV": "development", "FLASK_SECRET_KEY": "fake-not-real",
                "SENTRY_DSN": "https://fake@example.invalid/1"})
    code = ("import threading, app, sentry_sdk;"
            "print(app._scheduler_started, sentry_sdk.get_client().is_active())")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False False"
