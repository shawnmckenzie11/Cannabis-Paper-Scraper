"""Shared knobs for the daily PubMed harvest (GitHub Actions and local/Fly)."""

DAILY_HARVEST_QUERY = "cannabis OR cannabinoid OR marijuana"

# ISO date used when SQLite has never recorded a successful daily run.
DEFAULT_CATCHUP_DAYS = 3


def inprocess_daily_harvest_enabled(environ=None) -> bool:
    """Return True when gunicorn should start the in-process harvest thread.

    Production (Actions + VPS) leaves this off so harvest is not glued to HTTP.
    Set INPROCESS_DAILY_HARVEST=1 only for experiments that still want the
    daemon thread inside gunicorn.
    """
    env = environ if environ is not None else __import__("os").environ
    return str(env.get("INPROCESS_DAILY_HARVEST", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def resolve_harvest_mindate(last_run_date: str | None, *, today=None, catchup_days: int = DEFAULT_CATCHUP_DAYS) -> str:
    """Pick the PubMed Entrez-date start for an incremental daily harvest."""
    from datetime import date, timedelta

    if last_run_date and last_run_date not in {"Never", "never"}:
        return str(last_run_date)[:10]
    day = today or date.today()
    return (day - timedelta(days=catchup_days)).isoformat()
