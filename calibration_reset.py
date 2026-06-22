# calibration_reset.py
"""Archive calibration batch artifacts and rebuild an empty RL progress dashboard."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Sequence

import calibration_coordinator
import calibration_metrics

ARCHIVE_GLOBS = (
    "calibration_*.json",
    "node1_calibration_*.json",
    "node2a_calibration_*.json",
    "node2b_calibration_*.json",
    "node2c_calibration_*.json",
    "llm_pdf_maude_ab_*.json",
    "*_feedback_report.json",
    "*_walkthrough.md",
    "*_review.md",
    "calibration_dashboard_data.json",
    "all_nodes_llm_reclassify_eval.json",
    "llm_reclassify_eval_set.json",
)

KEEP_FILENAMES = frozenset({
    "dashboard.html",
    "maude_cues_production_overlay.json",
})


def collect_reset_paths(output_dir: Path) -> List[Path]:
    """Returns calibration artifact paths that should be archived on reset."""
    paths: List[Path] = []
    seen = set()
    for pattern in ARCHIVE_GLOBS:
        for path in output_dir.glob(pattern):
            if path.name in KEEP_FILENAMES:
                continue
            if path.name.endswith("_data.json"):
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)
    staged_dir = output_dir / "staged_patches"
    if staged_dir.exists():
        for path in staged_dir.glob("*.json"):
            key = str(path.resolve())
            if key not in seen:
                seen.add(key)
                paths.append(path)
    return sorted(paths)


def archive_calibration_artifacts(
    output_dir: Path,
    *,
    archive_label: str | None = None,
) -> Path:
    """Moves calibration artifacts into a timestamped archive folder."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = output_dir / "archive" / f"pre_rl_reset_{archive_label or timestamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)

    moved: List[str] = []
    for path in collect_reset_paths(output_dir):
        destination = archive_dir / path.name
        if destination.exists():
            destination = archive_dir / f"{path.stem}_{timestamp}{path.suffix}"
        shutil.move(str(path), str(destination))
        moved.append(path.name)

    manifest = {
        "archived_at": datetime.now().isoformat(),
        "archive_dir": str(archive_dir),
        "source_dir": str(output_dir),
        "moved_files": moved,
        "moved_count": len(moved),
    }
    with open(archive_dir / "_reset_manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    staged_dir = output_dir / "staged_patches"
    if staged_dir.exists() and not any(staged_dir.iterdir()):
        staged_dir.rmdir()

    return archive_dir


def release_calibration_lock(db=None) -> None:
    """Returns the calibration lock to idle after a reset."""
    try:
        calibration_coordinator.release_lock(db=db)
    except Exception:
        pass


def reset_calibration_dashboard(
    output_dir: Path,
    *,
    archive_label: str | None = None,
    release_lock: bool = True,
    db=None,
) -> dict:
    """Archives existing calibration artifacts and writes a fresh empty dashboard."""
    archive_dir = archive_calibration_artifacts(output_dir, archive_label=archive_label)
    if release_lock:
        release_calibration_lock(db=db)

    session_path = output_dir / "rl_session.json"
    reset_at = datetime.now().isoformat()
    manifest = json.loads((archive_dir / "_reset_manifest.json").read_text())
    archived_count = len(manifest["moved_files"])
    with open(session_path, "w", encoding="utf-8") as handle:
        json.dump({
            "reset_at": reset_at,
            "archive_dir": str(archive_dir),
            "archived_count": archived_count,
        }, handle, indent=2)

    data_path, html_path = calibration_metrics.build_dashboard(
        output_dir=output_dir,
        rules_path=Path("rules_config.json"),
    )
    return {
        "archive_dir": str(archive_dir),
        "archived_count": archived_count,
        "dashboard_data": str(data_path),
        "dashboard_html": str(html_path),
        "reset_at": reset_at,
        "rl_session_path": str(session_path),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds CLI parser for calibration dashboard reset."""
    parser = argparse.ArgumentParser(
        description="Archive calibration batch artifacts and rebuild an empty RL dashboard.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Calibration artifacts directory (default: Fly volume or scratch/calibration_runs).",
    )
    parser.add_argument(
        "--label",
        default=None,
        help="Optional suffix for the archive folder name.",
    )
    parser.add_argument(
        "--keep-lock",
        action="store_true",
        help="Do not release the calibration coordination lock.",
    )
    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()
    output_dir = calibration_metrics.resolve_calibration_output_dir(
        Path(args.output_dir) if args.output_dir else None,
    )
    result = reset_calibration_dashboard(
        output_dir,
        archive_label=args.label,
        release_lock=not args.keep_lock,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
