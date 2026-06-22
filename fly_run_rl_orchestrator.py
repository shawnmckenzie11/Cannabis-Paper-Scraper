#!/usr/bin/env python3
"""Fly-friendly entry point for the sub-node RL orchestrator (avoids fly ssh arg parsing)."""

from __future__ import annotations

import json
import os

from calibration_rl_orchestrator import build_orchestrator_arg_parser, run_orchestrator


def main() -> None:
    """Runs the RL orchestrator using environment variables."""
    argv = [
        "--subnode", os.getenv("SUBNODE", "node2b"),
        "--max-calls", os.getenv("MAX_CALLS", "20"),
        "--max-cycles", os.getenv("MAX_CYCLES", "3"),
        "--content-tier", os.getenv("CONTENT_TIER", "pdf_extracted"),
        "--no-preflight",
    ]
    args = build_orchestrator_arg_parser().parse_args(argv)
    result = run_orchestrator(args)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
