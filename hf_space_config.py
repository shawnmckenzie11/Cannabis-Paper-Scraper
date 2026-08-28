"""Hugging Face Space identity and in-process harvest gating."""

from __future__ import annotations

import os
from typing import Optional

HF_SPACE_OWNER = "mckenziansolutions"
HF_SPACE_NAME = "cannabis-paper-scraper"
HF_SPACE_ID = f"{HF_SPACE_OWNER}/{HF_SPACE_NAME}"
HF_SPACE_URL = f"https://huggingface.co/spaces/{HF_SPACE_ID}"
HF_SPACE_HOST = "https://mckenziansolutions-cannabis-paper-scraper.hf.space"


def running_on_huggingface(environ: Optional[dict] = None) -> bool:
    """Return True when the process is a Hugging Face Space runtime."""
    env = environ if environ is not None else os.environ
    return bool((env.get("SPACE_ID") or env.get("SPACE_HOST") or "").strip())


def inprocess_daily_harvest_enabled(environ: Optional[dict] = None) -> bool:
    """Return True when gunicorn should start the in-process harvest thread.

    Hugging Face Spaces and GitHub Actions own harvest; the thread stays off
    unless INPROCESS_DAILY_HARVEST is explicitly enabled.
    """
    env = environ if environ is not None else os.environ
    raw = str(env.get("INPROCESS_DAILY_HARVEST", "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}
