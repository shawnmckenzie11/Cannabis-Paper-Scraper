"""Checkpointing and stall detection for resilient Postgres delta push."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_CHECKPOINT_PATH = Path("scratch/local_push_checkpoint.json")
DEFAULT_STALL_SECONDS = float(os.getenv("PUSH_STALL_SECONDS", "90"))
DEFAULT_COMMIT_TIMEOUT_SECONDS = float(os.getenv("PUSH_COMMIT_TIMEOUT_SECONDS", "60"))
DEFAULT_STATEMENT_TIMEOUT_MS = int(os.getenv("PUSH_STATEMENT_TIMEOUT_MS", "30000"))
DEFAULT_HEARTBEAT_SECONDS = float(os.getenv("PUSH_HEARTBEAT_SECONDS", "30"))

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_RESUMABLE = 2


class PushStalledError(RuntimeError):
    """Raised when push progress stops for longer than the stall threshold."""


class PushProgressTracker:
    """Background watchdog that flags stalls when progress stops advancing."""

    def __init__(
        self,
        *,
        stall_seconds: float = DEFAULT_STALL_SECONDS,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        """Initialize tracker state and timing thresholds."""
        self.stall_seconds = stall_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._last_progress = time.time()
        self._last_heartbeat = 0.0
        self._detail = "starting"
        self.stalled = False
        self.stall_reason = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="push-stall-watchdog", daemon=True)

    def start(self) -> None:
        """Start the watchdog thread."""
        self._thread.start()

    def stop(self) -> None:
        """Stop the watchdog thread."""
        self._stop.set()
        self._thread.join(timeout=2.0)

    def touch(self, detail: str) -> None:
        """Record forward progress and reset the stall timer."""
        self._last_progress = time.time()
        self._detail = detail

    def maybe_log_heartbeat(self, *, pushed: int, delta_total: int) -> None:
        """Emit a heartbeat line when no batch commit occurred recently."""
        now = time.time()
        if now - self._last_heartbeat < self.heartbeat_seconds:
            return
        self._last_heartbeat = now
        idle = now - self._last_progress
        print(
            f"  heartbeat: pushed={pushed}/{delta_total} idle={idle:.0f}s detail={self._detail}",
            flush=True,
        )

    def check_stalled(self) -> None:
        """Raise when the watchdog observed a stall."""
        if self.stalled:
            raise PushStalledError(self.stall_reason or "push stalled")

    def _run(self) -> None:
        """Poll progress timestamps and mark stalled runs."""
        poll_seconds = min(5.0, max(0.1, self.stall_seconds / 4))
        while not self._stop.wait(poll_seconds):
            idle = time.time() - self._last_progress
            if idle >= self.stall_seconds:
                self.stalled = True
                self.stall_reason = (
                    f"No push progress for {idle:.0f}s (threshold={self.stall_seconds:.0f}s; "
                    f"last={self._detail})"
                )
                return


def checkpoint_path(path: Optional[Path] = None) -> Path:
    """Return the checkpoint file path."""
    if path is None:
        return DEFAULT_CHECKPOINT_PATH
    return Path(path)


def load_push_checkpoint(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Load the last push checkpoint when present."""
    target = checkpoint_path(path)
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_push_checkpoint(payload: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Persist push checkpoint metadata atomically."""
    target = checkpoint_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(target)


def configure_postgres_push_session(
    conn,
    *,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> None:
    """Apply session limits that prevent indefinitely blocked UPDATE/COMMIT calls."""
    if statement_timeout_ms > 0:
        conn.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
    conn.execute("SET lock_timeout = '10s'")
    conn.execute("SET idle_in_transaction_session_timeout = '60s'")


def commit_with_timeout(conn, *, timeout_seconds: float = DEFAULT_COMMIT_TIMEOUT_SECONDS) -> None:
    """Commit with a hard timeout so dead connections cannot hang the push forever."""
    result: Dict[str, Any] = {"error": None}

    def _commit() -> None:
        try:
            conn.commit()
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=_commit, name="push-commit", daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        raise PushStalledError(f"Postgres commit stalled after {timeout_seconds:.0f}s")
    if result["error"] is not None:
        raise result["error"]


MAX_POSTGRES_RECOVERY_WAIT_SECONDS = float(
    os.getenv("PUSH_POSTGRES_RECOVERY_WAIT_SECONDS", "600")
)


def wait_for_postgres_recovery(
    max_wait_seconds: float = MAX_POSTGRES_RECOVERY_WAIT_SECONDS,
) -> None:
    """Block until Postgres accepts connections or raise after max_wait_seconds."""
    from db_health import postgres_is_healthy

    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        healthy, detail = postgres_is_healthy()
        if healthy:
            return
        print(f"  waiting for Postgres ({detail})...", flush=True)
        time.sleep(5.0)
    healthy, detail = postgres_is_healthy()
    if not healthy:
        raise RuntimeError(
            f"Postgres still unhealthy after {max_wait_seconds:.0f}s: {detail}"
        )


def reconnect_postgres(db, *, statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS):
    """Return a fresh Postgres connection after waiting out recovery windows."""
    wait_for_postgres_recovery()
    conn = db.get_connection(retries=10)
    configure_postgres_push_session(conn, statement_timeout_ms=statement_timeout_ms)
    return conn
