#!/usr/bin/env bash
set -euo pipefail

# Cloud Agent install script for the Cannabis Paper Scraper (Flask app).
# Runs from the repository root after the source has been checked out.
#
# The default base image ships a PEP 668 "externally managed" system Python,
# so project dependencies are isolated in a local virtualenv (.venv).
# The script is idempotent: it is safe to run repeatedly and on cached state.

# Ensure the stdlib venv/ensurepip support is available (absent on some base images).
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends python3-venv
fi

# Create the virtualenv only if it does not already exist.
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
