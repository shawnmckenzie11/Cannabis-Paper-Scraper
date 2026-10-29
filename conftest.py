"""Pytest bootstrap: run the Flask app in explicit test mode.

app.py refuses to boot without FLASK_SECRET_KEY unless APP_ENV/FLASK_ENV names a
dev/test environment, so the suite opts in here.
"""
import os

os.environ.setdefault("APP_ENV", "test")

# Never let a test run (or a subprocess boot that copies os.environ) reach a real
# Sentry project: drop SENTRY_DSN, SENTRY_DSN_LIVE and any other SENTRY_*DSN var.
for _k in [k for k in os.environ if k.startswith("SENTRY_") and "DSN" in k]:
    del os.environ[_k]
