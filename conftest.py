"""Pytest bootstrap: run the Flask app in explicit test mode.

app.py refuses to boot without FLASK_SECRET_KEY unless APP_ENV/FLASK_ENV names a
dev/test environment, so the suite opts in here.

Legacy tests POST without CSRF tokens over plain http, so they run with CSRF off
and non-Secure cookies. This only works in APP_ENV=test; test_p0_3_* re-enables both.
"""
import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("WTF_CSRF_ENABLED", "0")
os.environ.setdefault("SESSION_COOKIE_SECURE", "0")
