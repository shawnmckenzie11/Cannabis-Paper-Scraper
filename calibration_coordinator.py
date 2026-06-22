# calibration_coordinator.py
"""Production calibration lock coordination via system_metadata."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

DEFAULT_SUBNODE_QUEUE = ["node2a", "node2b", "node2c"]
DEFAULT_REEVALUATE_LATER = ["node1b", "node1c", "node3a", "node3b", "node3c", "node2d"]

LOCK_STATE_KEY = "calibration_lock_state"
LOCK_OWNER_KEY = "calibration_lock_owner"
LOCK_SINCE_KEY = "calibration_lock_since"
ACTIVE_SUBNODE_KEY = "calibration_active_subnode"
SUBNODE_QUEUE_KEY = "calibration_subnode_queue"

VALID_LOCK_STATES = ("idle", "running_batch", "applying_feedback", "deploying")
BLOCKING_LOCK_STATES = ("running_batch", "applying_feedback", "deploying")


class CalibrationLockError(RuntimeError):
    """Raised when a calibration operation cannot acquire the production lock."""


def _get_db(db=None):
    """Returns DatabaseManager when available."""
    if db is not None:
        return db
    from db_manager import DatabaseManager

    return DatabaseManager()


def default_subnode_queue(rules_config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Returns the active RL sub-node queue from rules_config or defaults."""
    if rules_config:
        rl_cfg = (rules_config.get("agent_automation") or {}).get("calibration_rl") or {}
        queue = rl_cfg.get("subnode_queue")
        if isinstance(queue, list) and queue:
            return [str(item) for item in queue]
    return list(DEFAULT_SUBNODE_QUEUE)


def get_lock_status(db=None, rules_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Returns the current calibration lock snapshot for APIs and batch pre-flight."""
    db = _get_db(db)
    state = db.get_metadata(LOCK_STATE_KEY, "idle") or "idle"
    if state not in VALID_LOCK_STATES:
        state = "idle"
    queue_raw = db.get_metadata(SUBNODE_QUEUE_KEY)
    queue: List[str] = default_subnode_queue(rules_config)
    if queue_raw:
        try:
            parsed = json.loads(queue_raw)
            if isinstance(parsed, list):
                queue = [str(item) for item in parsed]
        except (TypeError, json.JSONDecodeError):
            pass
    return {
        "state": state,
        "owner": db.get_metadata(LOCK_OWNER_KEY),
        "since": db.get_metadata(LOCK_SINCE_KEY),
        "active_subnode": db.get_metadata(ACTIVE_SUBNODE_KEY),
        "subnode_queue": queue,
        "is_blocked": state in BLOCKING_LOCK_STATES,
    }


def set_subnode_queue(queue: List[str], db=None) -> None:
    """Persists the pending/completed sub-node queue to system_metadata."""
    db = _get_db(db)
    db.set_metadata(SUBNODE_QUEUE_KEY, json.dumps(queue))


def check_lock_available(db=None, *, operation: str = "calibration batch") -> None:
    """Raises CalibrationLockError when the lock is not idle."""
    status = get_lock_status(db)
    if status["state"] != "idle":
        owner = status.get("owner") or "unknown"
        since = status.get("since") or "unknown"
        raise CalibrationLockError(
            f"Cannot start {operation}: calibration lock is '{status['state']}' "
            f"(owner={owner}, since={since}). Wait for the active operation to finish."
        )


def acquire_lock(
    state: str,
    owner: str,
    *,
    subnode: Optional[str] = None,
    db=None,
) -> Dict[str, Any]:
    """Acquires the calibration lock when idle; raises if already held."""
    if state not in VALID_LOCK_STATES:
        raise ValueError(f"Invalid lock state: {state}")
    if state == "idle":
        raise ValueError("Use release_lock() to return to idle.")
    db = _get_db(db)
    check_lock_available(db, operation=f"acquire lock ({state})")
    now_str = datetime.now().isoformat()
    db.set_metadata(LOCK_STATE_KEY, state)
    db.set_metadata(LOCK_OWNER_KEY, owner)
    db.set_metadata(LOCK_SINCE_KEY, now_str)
    if subnode:
        db.set_metadata(ACTIVE_SUBNODE_KEY, subnode)
    return get_lock_status(db)


def release_lock(db=None) -> Dict[str, Any]:
    """Releases the calibration lock back to idle."""
    db = _get_db(db)
    db.set_metadata(LOCK_STATE_KEY, "idle")
    db.set_metadata(LOCK_OWNER_KEY, "")
    db.set_metadata(LOCK_SINCE_KEY, "")
    return get_lock_status(db)


def run_with_lock(
    state: str,
    owner: str,
    fn,
    *,
    subnode: Optional[str] = None,
    db=None,
):
    """Runs ``fn`` while holding the calibration lock; always releases on exit."""
    acquire_lock(state, owner, subnode=subnode, db=db)
    try:
        return fn()
    finally:
        release_lock(db=db)
