"""Root conftest strips SENTRY_*DSN so test runs never send real Sentry events."""
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))


class TestSentryDsnStripped(unittest.TestCase):
    def test_no_dsn_in_env_or_copied_subprocess_env(self):
        self.assertFalse([k for k in os.environ if k.startswith("SENTRY_") and "DSN" in k])
        env = dict(os.environ)
        r = subprocess.run([sys.executable, "-c", "import os; print(os.getenv('SENTRY_DSN') is None)"],
                           cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "True", r.stderr)

    def test_sentry_client_has_no_dsn_after_app_import(self):
        import app  # noqa: F401
        import sentry_sdk
        self.assertFalse(sentry_sdk.get_client().dsn)

    def test_outer_dsn_is_stripped_by_conftest(self):
        env = {k: v for k, v in os.environ.items()}
        env.update({"SENTRY_DSN": "https://fakekey@o0.ingest.example.invalid/1",
                    "SENTRY_DSN_LIVE": "https://fakekey@o0.ingest.example.invalid/2"})
        code = "import os, conftest; print([k for k in os.environ if 'SENTRY' in k and 'DSN' in k])"
        r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "[]", r.stderr)
