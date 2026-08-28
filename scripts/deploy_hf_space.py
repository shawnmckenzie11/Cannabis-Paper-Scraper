#!/usr/bin/env python3
"""Create or update the Hugging Face Docker Space from this GitHub repository."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hf_space_config import HF_SPACE_ID

logger = logging.getLogger("deploy_hf_space")

SPACE_IGNORE_PATTERNS = [
    ".git*",
    ".github/**",
    "scratch/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "**/*.db",
    "**/*.db-wal",
    "**/*.db-shm",
    ".cursor/**",
    ".venv/**",
    "venv/**",
    "htmlcov/**",
    ".pytest_cache/**",
    "coverage.xml",
    ".coverage",
    "cloud-agent-install.log",
    "artifacts/**",
]

SPACE_PUBLIC_VARS = [
    {"key": "PORT", "value": "7860"},
    {"key": "CHEAP_OPS", "value": "1"},
    {"key": "DATABASE_PATH", "value": "/tmp/cannabis_papers.db"},
    {"key": "CATALOG_DATASET_ID", "value": "mckenziansolutions/cannabis-papers-catalog"},
    {"key": "INPROCESS_DAILY_HARVEST", "value": "0"},
    {"key": "AUTO_HARVEST_CLASSIFY", "value": "false"},
]


def _token() -> str:
    """Return HF_TOKEN or exit."""
    import catalog_store

    token = catalog_store.hf_token()
    if not token:
        logger.error("HF_TOKEN is required to deploy the Space")
        raise SystemExit(2)
    return token


def _pro_required(exc: BaseException) -> bool:
    """Return True when Hugging Face rejected Docker Space creation as a paid feature."""
    text = str(exc).lower()
    needles = (
        "pro subscription",
        "paid plan",
        "requires a pro",
        "upgrade to pro",
        "subscribe at",
        "gradio and docker",
    )
    return any(needle in text for needle in needles)


def _apply_space_runtime(api, token: str) -> None:
    """Set public Space variables and optional secrets after the repo exists."""
    for item in SPACE_PUBLIC_VARS:
        try:
            api.add_space_variable(HF_SPACE_ID, item["key"], item["value"])
        except Exception as exc:
            logger.warning("Could not set Space variable %s: %s", item["key"], exc)
    reload_token = (os.getenv("CATALOG_RELOAD_TOKEN") or "").strip()
    if reload_token:
        try:
            api.add_space_secret(HF_SPACE_ID, "CATALOG_RELOAD_TOKEN", reload_token)
        except Exception as exc:
            logger.warning("Could not set CATALOG_RELOAD_TOKEN secret: %s", exc)
    if token:
        try:
            api.add_space_secret(HF_SPACE_ID, "HF_TOKEN", token)
        except Exception as exc:
            logger.warning("Could not set HF_TOKEN Space secret: %s", exc)


def main() -> int:
    """Upload the app tree to spaces/mckenziansolutions/cannabis-paper-scraper."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from huggingface_hub import HfApi, SpaceHardware

    import catalog_store

    token = _token()
    api = HfApi(token=token)
    dataset_id = catalog_store.catalog_dataset_id()
    logger.info("Creating catalog dataset %s if needed", dataset_id)
    api.create_repo(repo_id=dataset_id, repo_type="dataset", exist_ok=True, private=False)

    logger.info("Creating Space %s if needed", HF_SPACE_ID)
    try:
        api.create_repo(
            repo_id=HF_SPACE_ID,
            repo_type="space",
            space_sdk="docker",
            space_hardware=SpaceHardware.CPU_BASIC,
            space_variables=SPACE_PUBLIC_VARS,
            exist_ok=True,
            private=False,
        )
    except Exception as exc:
        logger.error("Could not create Docker Space %s: %s", HF_SPACE_ID, exc)
        if _pro_required(exc):
            logger.error(
                "Hugging Face requires a PRO plan to create Docker Spaces, even on "
                "free CPU Basic hardware. Upgrade https://huggingface.co/pricing for "
                "mckenziansolutions, create the Space in the website UI, then re-run "
                "this script. The catalog dataset and GitHub Actions harvest do not "
                "need PRO."
            )
            return 3
        logger.error(
            "If this is a PRO-plan error, upgrade the mckenziansolutions account "
            "or create the Space in the website UI, then re-run this script."
        )
        return 1

    logger.info("Uploading repository to Space %s", HF_SPACE_ID)
    api.upload_folder(
        folder_path=str(ROOT),
        repo_id=HF_SPACE_ID,
        repo_type="space",
        ignore_patterns=SPACE_IGNORE_PATTERNS,
        commit_message="Deploy Cannabis Research Intelligence dashboard",
    )
    _apply_space_runtime(api, token)
    logger.info("Space URL: https://huggingface.co/spaces/%s", HF_SPACE_ID)
    logger.info("App host: https://mckenziansolutions-cannabis-paper-scraper.hf.space")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
