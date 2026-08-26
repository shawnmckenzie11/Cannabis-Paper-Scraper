#!/bin/bash
# Install or remove a macOS LaunchAgent that serves the site from local SQLite.
#
# launchd cannot read ~/Documents (TCC), so it must not exec gunicorn from the
# repo. KeepAlive on `open` is also wrong (it would spawn Terminal forever).
# This agent copies a .command wrapper + watchdog into Application Support and
# runs the watchdog every 60s. If :8080 is down, the watchdog opens Terminal.app
# on the wrapper (Terminal already has Documents access).
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer only runs on your Mac (needs launchctl)." >&2
  echo "This shell is $(uname -s) ($(hostname)), not macOS." >&2
  echo "On the Mac, open Terminal.app and:" >&2
  echo "  cd \"\$HOME/Documents/Cannabis Paper Scraper\"" >&2
  echo "  git checkout cursor/macos-cloudflare-tunnel-d7e6 && git pull" >&2
  echo "  ./scripts/macos/run_local_site.sh   # start gunicorn in THIS window" >&2
  echo "  ./scripts/macos/install_macos_site.sh" >&2
  echo "Do not use the Cursor Cloud 'workspace \$' terminal for LaunchAgents." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.mckenzian.cannabis-site"
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${LABEL}.plist"
SUPPORT_DIR="${HOME}/Library/Application Support/cannabis-paper-scraper"
WRAPPER="${SUPPORT_DIR}/run_local_site.command"
WATCHDOG_SRC="${REPO_ROOT}/scripts/macos/ensure_local_site.sh"
WATCHDOG="${SUPPORT_DIR}/ensure_local_site.sh"
RUNNER="${REPO_ROOT}/scripts/macos/run_local_site.sh"
LOG_DIR="${REPO_ROOT}/logs"
PYTHON="${REPO_ROOT}/venv/bin/python"

usage() {
  echo "Usage: $0 [--uninstall]"
  echo "Installs a LaunchAgent that serves cannabis_papers.db on 127.0.0.1:8080."
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -x "${PYTHON}" ]]; then
  echo "Missing venv python at ${PYTHON}" >&2
  exit 1
fi
if [[ ! -f "${RUNNER}" ]]; then
  echo "Missing site runner at ${RUNNER}" >&2
  exit 1
fi
if [[ ! -f "${WATCHDOG_SRC}" ]]; then
  echo "Missing watchdog at ${WATCHDOG_SRC}" >&2
  exit 1
fi
chmod +x "${RUNNER}" "${WATCHDOG_SRC}"

uid="$(id -u)"
domain="gui/${uid}"

unload_agent() {
  launchctl bootout "${domain}/${LABEL}" >/dev/null 2>&1 || true
  launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
  unload_agent
  rm -f "${PLIST_PATH}" "${WRAPPER}" "${WATCHDOG}"
  echo "Removed ${LABEL}"
  exit 0
fi

mkdir -p "${PLIST_DIR}" "${LOG_DIR}" "${SUPPORT_DIR}"

cat > "${WRAPPER}" <<EOF
#!/bin/bash
# Terminal.app runs this so macOS TCC allows reading ~/Documents.
set -euo pipefail
export PATH="${REPO_ROOT}/venv/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
export PYTHONUNBUFFERED=1
unset DATABASE_URL
export DATABASE_PATH="${REPO_ROOT}/cannabis_papers.db"
cd "${REPO_ROOT}"
mkdir -p "${LOG_DIR}"
echo "\$(date -Iseconds) starting local site" >> "${LOG_DIR}/macos_site.launchd.out.log"
exec /usr/bin/caffeinate -i "${RUNNER}" >> "${LOG_DIR}/macos_site.launchd.out.log" 2>> "${LOG_DIR}/macos_site.launchd.err.log"
EOF
chmod +x "${WRAPPER}"
install -m 755 "${WATCHDOG_SRC}" "${WATCHDOG}"
if command -v xattr >/dev/null 2>&1; then
  if xattr -l "${WRAPPER}" 2>/dev/null | grep -q quarantine; then
    xattr -d com.apple.quarantine "${WRAPPER}" 2>/dev/null || true
  fi
fi

cat > "${PLIST_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MACOS_SITE_WRAPPER</key>
    <string>${WRAPPER}</string>
  </dict>
  <key>ProgramArguments</key>
  <array>
    <string>${WATCHDOG}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>60</integer>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/macos_site.launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/macos_site.launchd.err.log</string>
</dict>
</plist>
EOF

unload_agent
if ! launchctl bootstrap "${domain}" "${PLIST_PATH}" 2>/dev/null; then
  launchctl load "${PLIST_PATH}"
fi

echo "Installed ${LABEL} (watchdog every 60s; no KeepAlive on open)"
echo "  plist:   ${PLIST_PATH}"
echo "  wrapper: ${WRAPPER}"
echo "  catalog: ${REPO_ROOT}/cannabis_papers.db (SQLite; DATABASE_URL unset)"
echo "  bind:    127.0.0.1:8080"
echo "  logs:    ${LOG_DIR}/macos_site.error.log"
echo "Start gunicorn NOW in this Terminal (has Documents access):"
echo "  unset DATABASE_URL"
echo "  export DATABASE_PATH=${REPO_ROOT}/cannabis_papers.db"
echo "  ${RUNNER}"
echo "Leave that window open, then in another tab:"
echo "  curl -sS -o /dev/null -w \"%{http_code}\\n\" http://127.0.0.1:8080/"
echo "Repair tab flags once: unset DATABASE_URL && DATABASE_PATH=${REPO_ROOT}/cannabis_papers.db \\"
echo "  ${PYTHON} ${REPO_ROOT}/scripts/repair_recent_tab_flags.py --since-harvested 2026-07-17"
echo "Then install the tunnel: ${REPO_ROOT}/scripts/macos/install_macos_cloudflared.sh"
