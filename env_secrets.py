"""Load local secrets for dev scripts without committing credentials."""

from __future__ import annotations

import os
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_MIGRATIONS_ENV = _ROOT / "migrations" / "env.py"


def load_anthropic_api_key() -> str | None:
    """Return ANTHROPIC_API_KEY from env, migrations/env.py, or .env via dotenv."""
    existing = os.getenv("ANTHROPIC_API_KEY")
    if existing:
        return existing

    if _MIGRATIONS_ENV.is_file():
        text = _MIGRATIONS_ENV.read_text(encoding="utf-8")
        patterns = [
            r"ANTHROPIC_API_KEY\s*=\s*['\"]([^'\"]+)['\"]",
            r"os\.environ\[['\"]ANTHROPIC_API_KEY['\"]\]\s*=\s*['\"]([^'\"]+)['\"]",
            r"os\.putenv\(['\"]ANTHROPIC_API_KEY['\"],\s*['\"]([^'\"]+)['\"]",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                os.environ["ANTHROPIC_API_KEY"] = match.group(1)
                return match.group(1)

    try:
        from dotenv import load_dotenv

        load_dotenv(_ROOT / ".env")
    except ImportError:
        pass

    return os.getenv("ANTHROPIC_API_KEY")
