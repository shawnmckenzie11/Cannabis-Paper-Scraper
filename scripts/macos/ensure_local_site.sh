#!/bin/bash
# Watchdog for the Mac public site. launchd must not KeepAlive `open`, or
# Terminal.app would spawn a new window every crash. This script is safe to
# run on a 60s StartInterval: it no-ops when gunicorn already bound :8080.
#
# Environment:
#   MACOS_SITE_WRAPPER  absolute path to run_local_site.command
set -euo pipefail

WRAPPER="${MACOS_SITE_WRAPPER:-}"
if [[ -z "${WRAPPER}" ]]; then
  echo "MACOS_SITE_WRAPPER is not set" >&2
  exit 1
fi

if curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8080/; then
  exit 0
fi
if pgrep -f "gunicorn .*app:app" >/dev/null 2>&1; then
  exit 0
fi

exec /usr/bin/open -gj -a Terminal "${WRAPPER}"
