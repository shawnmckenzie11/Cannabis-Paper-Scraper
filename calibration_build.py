# calibration_build.py
"""Build identity for Maude/classifier code deployed to Fly (RL preflight checks)."""

from __future__ import annotations

# Bump when extractor/maude_classifier handoffs land so RL runs can verify Fly image.
MAUDE_CLASSIFIER_BUILD_ID = "20260628-manual-edit-tracker-v2"


def maude_build_info() -> dict:
    """Returns build metadata printed during Fly RL preflight."""
    return {
        "maude_classifier_build_id": MAUDE_CLASSIFIER_BUILD_ID,
    }
