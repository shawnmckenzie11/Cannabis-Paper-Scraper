"""P0-5: entrypoint never deletes/overwrites a DB; prod fails fast on missing config.

Fake data only.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
STRIP = ("FLASK_SECRET_KEY", "APP_ENV", "FLASK_ENV", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
         "RATELIMIT_STORAGE_URI", "RATE_LIMIT_SINGLE_INSTANCE", "DATABASE_URL", "SEED_DEV_DB",
         "CALIBRATION_ARTIFACT_DIR")
PROD_OK = {"FLASK_SECRET_KEY": "fake-" + "k" * 40, "GOOGLE_CLIENT_ID": "fake-client-id",
           "GOOGLE_CLIENT_SECRET": "fake-client-secret", "RATELIMIT_STORAGE_URI": "redis://fake:6379/0"}


def _clean_env(overrides):
    env = {k: v for k, v in os.environ.items() if k not in STRIP}
    env.update(overrides)
    return env


# Limiter storage stub for prod boots: records the URI Flask-Limiter is built
# with and serves memory storage, so redis:// needs no redis package or server.
_STUB_LIMITER_STORAGE = (
    "import flask_limiter._extension as _fe, limits.storage as _ls; _seen = []\n"
    "def _stub(uri, **kw):\n"
    "    _seen.append(uri); return _ls.MemoryStorage()\n"
    "_fe.storage_from_string = _stub\n"
)


def _boot(overrides, code="import app; print('BOOTED')"):
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=_clean_env(overrides),
                          capture_output=True, text=True, timeout=180)


class TestEntrypointNeverWipes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.work = os.path.join(self.tmp, "app")
        os.makedirs(self.work)
        shutil.copy(os.path.join(REPO, "entrypoint.sh"), self.work)
        # Fake seed source so we can detect an overwrite or a seed.
        with open(os.path.join(self.work, "cannabis_papers.db"), "wb") as f:
            f.write(b"SEED" * 256)
        # Stub python3 so background jobs/alembic never touch anything real.
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        stub = os.path.join(self.bin, "python3")
        with open(stub, "w") as f:
            f.write("#!/bin/sh\necho 1\n")
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
        self.db = os.path.join(self.tmp, "data", "cannabis_papers.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, extra):
        env = _clean_env({"PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
                          "DATABASE_PATH": self.db, "CHEAP_OPS": "1", **extra})
        env["CALIBRATION_ARTIFACT_DIR"] = os.path.join(self.tmp, "cal")
        return subprocess.run(["sh", "entrypoint.sh", "true"], cwd=self.work, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_existing_small_db_not_deleted(self):
        os.makedirs(os.path.dirname(self.db))
        payload = b"x" * 1024
        with open(self.db, "wb") as f:
            f.write(payload)
        for extra in ({}, {"APP_ENV": "dev", "SEED_DEV_DB": "1"}):
            r = self._run(extra)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(self.db, "rb") as f:
                self.assertEqual(f.read(), payload)

    def test_seed_skipped_without_flag(self):
        for extra in ({}, {"APP_ENV": "dev"}, {"SEED_DEV_DB": "1"}, {"APP_ENV": "production", "SEED_DEV_DB": "1"}):
            r = self._run(extra)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(self.db), extra)
            self.assertIn("seeding skipped", r.stdout)

    def test_seed_with_dev_flag_when_absent(self):
        r = self._run({"APP_ENV": "test", "SEED_DEV_DB": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(self.db))

    def test_postgres_mode_never_seeds(self):
        r = self._run({"APP_ENV": "dev", "SEED_DEV_DB": "1", "DATABASE_URL": "postgresql://fake/db"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.db))

    def test_no_rm_of_db_in_entrypoint(self):
        with open(os.path.join(REPO, "entrypoint.sh")) as f:
            self.assertNotIn('rm -f "$DB_PATH"', f.read())


class TestProdFailFast(unittest.TestCase):
    def test_prod_full_config_boots(self):
        r = _boot(dict(PROD_OK), _STUB_LIMITER_STORAGE + "import app; print('BOOTED')")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("BOOTED", r.stdout)

    def test_each_missing_var_refuses(self):
        for var in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "FLASK_SECRET_KEY"):
            for bad in (None, "  "):
                env = dict(PROD_OK)
                if bad is None:
                    env.pop(var)
                else:
                    env[var] = bad
                r = _boot(env)
                self.assertNotEqual(r.returncode, 0, var)
                self.assertIn(var, r.stderr)
                for v in PROD_OK.values():
                    self.assertNotIn(v, r.stderr)

    def test_dev_starts_without_secrets(self):
        for name in ("dev", "development", "local", "test", "testing"):
            r = _boot({"APP_ENV": name})
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def test_limiter_memory_refused_in_prod(self):
        for uri in (None, "memory://"):
            env = dict(PROD_OK)
            env.pop("RATELIMIT_STORAGE_URI")
            if uri:
                env["RATELIMIT_STORAGE_URI"] = uri
            r = _boot(env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("RATELIMIT_STORAGE_URI", r.stderr)

    def test_limiter_single_instance_flag(self):
        env = dict(PROD_OK)
        env.pop("RATELIMIT_STORAGE_URI")
        env["RATE_LIMIT_SINGLE_INSTANCE"] = "1"
        r = _boot(env, "import app; print(app.app.config['RATELIMIT_STORAGE_URI'])")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("memory://", r.stdout)
        self.assertIn("RATE_LIMIT_SINGLE_INSTANCE", r.stderr)

    def test_limiter_dev_defaults_memory(self):
        r = _boot({"APP_ENV": "dev"}, "import app; print(app.app.config['RATELIMIT_STORAGE_URI'])")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("memory://", r.stdout)

    def test_limiter_redis_used_in_prod(self):
        r = _boot(dict(PROD_OK), _STUB_LIMITER_STORAGE + "import app\n"
                  "print(app.app.config['RATELIMIT_STORAGE_URI'])\nprint('LIMITER', _seen)")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("redis://fake:6379/0", r.stdout)
        self.assertIn("LIMITER ['redis://fake:6379/0']", r.stdout)
