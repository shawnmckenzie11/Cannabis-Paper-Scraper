"""Test helpers for creating real DB-backed sessions (admin checks hit the users table)."""
import uuid

from db_manager import DatabaseManager


def ensure_user(email: str, *, verified: int = 1) -> dict:
    """Create (or fetch) a user row for ``email`` and return it."""
    db = DatabaseManager()
    user = db.get_user_by_email(email)
    if not user:
        username = "t_" + uuid.uuid4().hex[:10]
        db.create_user(username, email, password_hash=None, is_verified=verified)
        user = db.get_user_by_email(email)
    return user


def login_session(client, email: str, *, verified: int = 1) -> dict:
    """Put a DB-backed logged-in session for ``email`` on a Flask test client."""
    user = ensure_user(email, verified=verified)
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = user["id"]
        sess["username"] = user["username"]
        sess["email"] = user["email"]
    return user
