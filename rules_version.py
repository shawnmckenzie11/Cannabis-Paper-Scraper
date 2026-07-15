"""Semver helpers for rules_config.json version bumps."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

RULES_CONFIG_PATH = Path(__file__).resolve().parent / "rules_config.json"


def parse_semver(version: str) -> Tuple[int, int, int]:
    """Parses a semver string into (major, minor, patch) integers."""
    parts = str(version or "0.0.0").strip().split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise ValueError(f"Invalid semver: {version!r}") from exc


def format_semver(major: int, minor: int, patch: int) -> str:
    """Formats major/minor/patch as a semver string."""
    return f"{major}.{minor}.{patch}"


def bump_patch_version(version: str) -> str:
    """Increments the patch segment of a semver string (e.g. 2.6.0 → 2.6.1)."""
    major, minor, patch = parse_semver(version)
    return format_semver(major, minor, patch + 1)


def compare_semver(left: str, right: str) -> int:
    """Return -1 if left < right, 0 if equal, 1 if left > right."""
    a = parse_semver(left)
    b = parse_semver(right)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def read_rules_config_version(path: Path | None = None) -> str:
    """Returns the version field from rules_config.json."""
    config_path = path or RULES_CONFIG_PATH
    with open(config_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return str(payload.get("version") or "0.0.0")


def update_rules_config_version(new_version: str, path: Path | None = None) -> str:
    """Writes an updated version to rules_config.json and reloads heuristics caches."""
    config_path = path or RULES_CONFIG_PATH
    with open(config_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["version"] = str(new_version)
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    try:
        import heuristics_engine
        heuristics_engine.seed_rules_config_to_db(payload)
        heuristics_engine.reload_rules_config()
    except Exception:
        pass
    try:
        import classifier
        if hasattr(classifier, "load_rules_config"):
            classifier.load_rules_config.cache_clear()
    except Exception:
        pass
    return str(new_version)


def bump_rules_patch_version(path: Path | None = None) -> Tuple[str, str]:
    """Bumps rules_config.json patch version and returns (before, after)."""
    config_path = path or RULES_CONFIG_PATH
    before = read_rules_config_version(config_path)
    after = bump_patch_version(before)
    update_rules_config_version(after, path=config_path)
    return before, after
