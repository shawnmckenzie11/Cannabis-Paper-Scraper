"""P0-5 startup safety: fail fast outside dev/test when required config is missing.

Error messages name the missing variable but never echo any value.
"""
import logging
import os

DEV_ENVS = {"dev", "development", "local", "test", "testing"}
REQUIRED_PROD_VARS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "FLASK_SECRET_KEY")
_TRUTHY = {"1", "true", "yes", "on"}

logger = logging.getLogger(__name__)


class StartupConfigError(RuntimeError):
    pass


def is_dev_env() -> bool:
    """APP_ENV decides alone when set; FLASK_ENV is consulted only when APP_ENV
    is unset/empty (matches entrypoint.sh ${APP_ENV:-${FLASK_ENV:-}})."""
    value = (os.getenv("APP_ENV") or "").strip() or (os.getenv("FLASK_ENV") or "").strip()
    return value.lower() in DEV_ENVS


def resolve_rate_limit_storage_uri() -> str:
    """Shared limiter store URI (e.g. redis://...). memory:// only in dev/test or
    with an explicit RATE_LIMIT_SINGLE_INSTANCE=1 opt-in."""
    uri = (os.getenv("RATELIMIT_STORAGE_URI") or "").strip()
    if is_dev_env():
        return uri or "memory://"
    if uri and not uri.lower().startswith("memory://"):
        return uri
    if (os.getenv("RATE_LIMIT_SINGLE_INSTANCE") or "").strip().lower() in _TRUTHY:
        logger.warning(
            "RATE_LIMIT_SINGLE_INSTANCE=1: using in-process memory:// rate-limit storage. "
            "Limits are NOT shared across machines/workers; only safe with a single instance."
        )
        return "memory://"
    raise StartupConfigError(
        "RATELIMIT_STORAGE_URI is not set or is memory://. Refusing to start: set it to a "
        "shared store (e.g. redis://...), or set RATE_LIMIT_SINGLE_INSTANCE=1 to accept "
        "per-process limits, or set APP_ENV to a dev value."
    )


def validate_startup_env() -> None:
    """Raise StartupConfigError outside dev/test if required config is missing/empty."""
    if is_dev_env():
        _refuse_postgres_in_dev()
        return
    missing = [n for n in REQUIRED_PROD_VARS if not (os.getenv(n) or "").strip()]
    if missing:
        raise StartupConfigError(
            "; ".join(f"{n} is not set" for n in missing)
            + ". Refusing to start: set the missing variable(s), or set APP_ENV to a dev "
            "value (dev, development, local, test, testing) for local use."
        )
    resolve_rate_limit_storage_uri()


_LOCAL_DB_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def _postgres_hosts(url: str):
    """Every host psycopg2 would try, including ?host= / ?hostaddr= overrides."""
    hosts = []
    try:
        from psycopg2.extensions import parse_dsn
        dsn_url = "postgresql" + url[url.index("://"):] if "://" in url else url
        parsed = parse_dsn(dsn_url)
        for key in ("host", "hostaddr"):
            hosts += [h.strip().lower() for h in (parsed.get(key) or "").split(",")]
    except Exception:
        return None  # unparseable: treat as non-local
    from urllib.parse import urlsplit, parse_qs
    try:
        parts = urlsplit(url)
        hosts.append((parts.hostname or "").lower())
        q = parse_qs(parts.query)
        for key in ("host", "hostaddr"):
            for v in q.get(key, []):
                hosts += [h.strip().lower() for h in v.split(",")]
    except ValueError:
        return None
    return hosts


def _refuse_postgres_in_dev() -> None:
    """A dev/test run may only use a localhost Postgres (local or CI test DB); never a remote/prod one.

    No override. Error names variables, never values.
    """
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url.lower().startswith("postgres"):
        return
    hosts = _postgres_hosts(url)
    if hosts is not None and all(h in _LOCAL_DB_HOSTS for h in hosts):
        return
    raise StartupConfigError(
        "DATABASE_URL points at a non-local Postgres while APP_ENV is a dev/test value. "
        "Refusing to start: unset DATABASE_URL or use a localhost database."
    )
