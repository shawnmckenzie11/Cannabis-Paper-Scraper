"""Shared helpers for the existing ``background_tasks`` + thread-pool job pattern.

Heavy Flask routes (PDF parse, analyze) enqueue work here so the single sync
Gunicorn worker can keep serving catalog search. Results that do not belong in
the slim ``background_tasks`` schema live in process memory, matching the
existing pending-PDF and ``harvest_state`` pattern.

No runtime schema patches: the table is created only by Alembic.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from db_manager import DatabaseManager

logger = logging.getLogger(__name__)

# Same pool size as the previous in-app executor (backpopulate + new jobs).
task_executor = ThreadPoolExecutor(max_workers=2)

ANALYZE_PAPER_CAP = 2000
ANALYZE_DRILLDOWN_CAP = 200
SECTION_STATS_SAMPLE_LIMIT = 400
HARVEST_PROMPT_THRESHOLD = 500

_task_results: Dict[str, Any] = {}
_task_results_lock = threading.Lock()


def _is_postgres() -> bool:
    """True when this process is pointed at production Postgres."""
    return bool(os.environ.get("DATABASE_URL"))


def _param() -> str:
    """Return the SQL placeholder for the active backend."""
    return "%s" if _is_postgres() else "?"


def create_task(task_type: str) -> str:
    """Insert a pending ``background_tasks`` row and return its task_id."""
    task_id = str(uuid.uuid4())
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    param = _param()
    try:
        cursor.execute(
            f"INSERT INTO background_tasks (task_id, sa_task_type, status, total_papers, processed_papers) "
            f"VALUES ({param}, {param}, {param}, {param}, {param})",
            (task_id, task_type, "pending", 0, 0),
        )
        conn.commit()
    finally:
        conn.close()
    return task_id


def update_task(
    task_id: str,
    *,
    status: Optional[str] = None,
    total_papers: Optional[int] = None,
    processed_papers: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """Patch progress fields on a ``background_tasks`` row."""
    assignments = ["updated_at = CURRENT_TIMESTAMP"]
    values: list[Any] = []
    param = _param()
    if status is not None:
        assignments.append(f"status = {param}")
        values.append(status)
    if total_papers is not None:
        assignments.append(f"total_papers = {param}")
        values.append(total_papers)
    if processed_papers is not None:
        assignments.append(f"processed_papers = {param}")
        values.append(processed_papers)
    if error_message is not None:
        assignments.append(f"error_message = {param}")
        values.append(error_message)
    values.append(task_id)

    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"UPDATE background_tasks SET {', '.join(assignments)} WHERE task_id = {param}",
            tuple(values),
        )
        conn.commit()
    finally:
        conn.close()


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Return a task row plus any in-memory result payload."""
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    param = _param()
    try:
        cursor.execute(
            f"SELECT sa_task_type, status, total_papers, processed_papers, error_message "
            f"FROM background_tasks WHERE task_id = {param}",
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        payload = {
            "task_id": task_id,
            "task_type": row[0] if isinstance(row, tuple) else row["sa_task_type"],
            "status": row[1] if isinstance(row, tuple) else row["status"],
            "total_papers": row[2] if isinstance(row, tuple) else row["total_papers"],
            "processed_papers": row[3] if isinstance(row, tuple) else row["processed_papers"],
            "error_message": row[4] if isinstance(row, tuple) else row["error_message"],
        }
    finally:
        conn.close()

    result = get_task_result(task_id)
    if result is not None:
        payload["result"] = result
    return payload


def set_task_result(task_id: str, result: Any) -> None:
    """Store a JSON-serializable result for later polling."""
    with _task_results_lock:
        _task_results[task_id] = result


def get_task_result(task_id: str) -> Any:
    """Return the in-memory result for ``task_id``, if any."""
    with _task_results_lock:
        return _task_results.get(task_id)


def clear_task_result(task_id: str) -> None:
    """Drop a stored result (used by tests)."""
    with _task_results_lock:
        _task_results.pop(task_id, None)


def submit_background(fn: Callable, *args: Any) -> None:
    """Run ``fn`` on the shared pool so the Flask request thread can return."""
    task_executor.submit(fn, *args)


def mark_task_failed(task_id: str, exc: BaseException) -> None:
    """Record a worker exception on the task row."""
    logger.exception("Background task %s failed", task_id)
    try:
        update_task(task_id, status="failed", error_message=str(exc))
    except Exception:
        logger.exception("Failed to persist error for task %s", task_id)


def dumps_json(value: Any) -> str:
    """Serialize analysis payloads the same way ``api_analyze`` did."""
    return json.dumps(value, default=str)
