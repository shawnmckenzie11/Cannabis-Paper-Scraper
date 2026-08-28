"""Persistent catalog storage for the Hugging Face Space and GitHub Actions harvest.

Free Hugging Face disks are ephemeral. The SQLite catalog lives in a Hub dataset
(default) or optional Cloudflare R2 bucket, and is copied onto local disk at
boot and after each harvest.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("catalog_store")

DEFAULT_DATASET_ID = "mckenziansolutions/cannabis-papers-catalog"
DEFAULT_OBJECT_NAME = "cannabis_papers.db"


class CatalogStoreError(RuntimeError):
    """Raised when the remote catalog cannot be downloaded or uploaded."""


def catalog_dataset_id(environ: Optional[dict] = None) -> str:
    """Return the Hub dataset id that stores cannabis_papers.db."""
    env = environ if environ is not None else os.environ
    return (env.get("CATALOG_DATASET_ID") or DEFAULT_DATASET_ID).strip()


def catalog_object_name(environ: Optional[dict] = None) -> str:
    """Return the object/filename used in the dataset or R2 bucket."""
    env = environ if environ is not None else os.environ
    return (env.get("R2_OBJECT") or env.get("CATALOG_OBJECT") or DEFAULT_OBJECT_NAME).strip() or DEFAULT_OBJECT_NAME


def r2_configured(environ: Optional[dict] = None) -> bool:
    """Return True when Cloudflare R2 env vars are complete."""
    env = environ if environ is not None else os.environ
    return bool(env.get("R2_BUCKET", "").strip() and env.get("R2_ENDPOINT", "").strip())


def hf_token(environ: Optional[dict] = None) -> Optional[str]:
    """Return a Hub token from common environment names."""
    env = environ if environ is not None else os.environ
    for key in ("HF_TOKEN", "HUGGINGFACE_HUB_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = (env.get(key) or "").strip()
        if value:
            return value
    return None


def store_backend(environ: Optional[dict] = None) -> str:
    """Pick hf, r2, or none from env (R2 wins when fully configured)."""
    env = environ if environ is not None else os.environ
    forced = (env.get("CATALOG_STORE") or "").strip().lower()
    if forced in {"hf", "r2", "none"}:
        return forced
    if r2_configured(env):
        return "r2"
    if catalog_dataset_id(env):
        return "hf"
    return "none"


def ensure_local_catalog(dest: Optional[str] = None) -> str:
    """Download the remote catalog onto dest when a store is configured.

    Returns:
        The local SQLite path. Missing remote objects leave dest unchanged.
    """
    dest_path = dest or os.getenv("DATABASE_PATH") or DEFAULT_OBJECT_NAME
    backend = store_backend()
    if backend == "none":
        logger.info("No catalog store configured; using local file %s", dest_path)
        return dest_path
    try:
        download_catalog(dest_path)
    except CatalogStoreError as exc:
        if Path(dest_path).is_file() and Path(dest_path).stat().st_size > 0:
            logger.warning("Catalog download failed (%s); keeping existing %s", exc, dest_path)
            return dest_path
        raise
    return dest_path


def download_catalog(dest: str) -> str:
    """Fetch the catalog from the configured backend onto dest."""
    backend = store_backend()
    if backend == "r2":
        return _download_r2(dest)
    if backend == "hf":
        return _download_hf(dest)
    raise CatalogStoreError("No catalog store configured (set CATALOG_DATASET_ID or R2_BUCKET)")


def upload_catalog(src: str) -> None:
    """Publish a local SQLite catalog to R2 (if configured) and the Hub dataset.

    The Space boots from the Hub dataset and does not have R2 credentials by
    default, so a harvest that only wrote R2 would never show up on the dashboard.
    """
    uploaded: list[str] = []
    errors: list[str] = []
    if store_backend() == "none":
        raise CatalogStoreError("No catalog store configured (set CATALOG_DATASET_ID or R2_BUCKET)")
    if r2_configured():
        try:
            _upload_r2(src)
            uploaded.append("r2")
        except CatalogStoreError as exc:
            errors.append(str(exc))
    if catalog_dataset_id():
        try:
            _upload_hf(src)
            uploaded.append("hf")
        except CatalogStoreError as exc:
            errors.append(str(exc))
    if uploaded:
        logger.info("Catalog uploaded to %s", ",".join(uploaded))
        return
    detail = "; ".join(errors) if errors else "set CATALOG_DATASET_ID or R2_BUCKET"
    raise CatalogStoreError(f"No catalog store configured ({detail})")


def _aws_s3_cp(src: str, dest: str, endpoint_url: str) -> None:
    """Copy one object with the AWS CLI against an S3-compatible endpoint."""
    cmd = ["aws", "s3", "cp", src, dest, "--endpoint-url", endpoint_url]
    logger.info("Running %s", " ".join(cmd))
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise CatalogStoreError(f"R2 copy failed: {err}")


def _download_r2(dest: str) -> str:
    """Download cannabis_papers.db from Cloudflare R2."""
    bucket = os.environ["R2_BUCKET"].strip()
    endpoint = os.environ["R2_ENDPOINT"].strip()
    object_key = catalog_object_name()
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    _aws_s3_cp(f"s3://{bucket}/{object_key}", str(dest_path), endpoint)
    return str(dest_path)


def _upload_r2(src: str) -> None:
    """Upload cannabis_papers.db to Cloudflare R2."""
    bucket = os.environ["R2_BUCKET"].strip()
    endpoint = os.environ["R2_ENDPOINT"].strip()
    object_key = catalog_object_name()
    _aws_s3_cp(str(src), f"s3://{bucket}/{object_key}", endpoint)


def _download_hf(dest: str) -> str:
    """Download cannabis_papers.db from a Hub dataset repo."""
    from huggingface_hub import hf_hub_download

    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    repo_id = catalog_dataset_id()
    filename = catalog_object_name()
    token = hf_token()
    logger.info("Downloading hf://datasets/%s/%s", repo_id, filename)
    try:
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            repo_type="dataset",
            token=token,
        )
    except Exception as exc:
        raise CatalogStoreError(f"Hub download failed for {repo_id}/{filename}: {exc}") from exc
    data = Path(downloaded).read_bytes()
    dest_path.write_bytes(data)
    logger.info("Wrote %s bytes to %s", len(data), dest_path)
    return str(dest_path)


def _upload_hf(src: str) -> None:
    """Upload cannabis_papers.db to a Hub dataset repo (creates the repo if needed)."""
    from huggingface_hub import HfApi

    token = hf_token()
    if not token:
        raise CatalogStoreError("HF_TOKEN is required to upload the catalog dataset")
    repo_id = catalog_dataset_id()
    filename = catalog_object_name()
    api = HfApi(token=token)
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True, private=False)
    logger.info("Uploading %s to hf://datasets/%s/%s", src, repo_id, filename)
    api.upload_file(
        path_or_fileobj=src,
        path_in_repo=filename,
        repo_id=repo_id,
        repo_type="dataset",
        commit_message="Update cannabis papers catalog",
    )
