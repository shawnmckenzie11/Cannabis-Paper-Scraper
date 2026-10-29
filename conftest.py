"""Pytest bootstrap: run the Flask app in explicit test mode.

app.py refuses to boot without FLASK_SECRET_KEY unless APP_ENV/FLASK_ENV names a
dev/test environment, so the suite opts in here.
"""
import os

os.environ.setdefault("APP_ENV", "test")
