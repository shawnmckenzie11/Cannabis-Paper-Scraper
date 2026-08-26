#!/bin/bash
# Install or remove a macOS LaunchAgent that serves the site from local SQLite.
# Launchd often cannot read ~/Documents (TCC), so the agent runs a wrapper
# under ~/Library/Application Support.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LABEL="com.mckenzian.cannabis-site"
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${LABEL}.plist"
SUPPORT_DIR="${HOME}/Library/Application Support/cannabis-paper-scraper"
WRAPPER="${SUPPORT_DIR}/run_local_site.command"
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
chmod +x "${RUNNER}"

uid="$(id -u)"
domain="gui/${uid}"

unload_agent() {
  launchctl bootout "${domain}/${LABEL}" >/dev/null 2>&1 || true
  launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
  unload_agent
  rm -f "${PLIST_PATH}" "${WRAPPER}"
  echo "Removed ${LABEL}"
  exit 0
fi

mkdir -p "${PLIST_DIR}" "${LOG_DIR}" "${SUPPORT_DIR}"

cat > "${WRAPPER}" <<EOF
#!/bin/bash
# Runs outside launchd's Documents TCC by executing from Application Support.
set -euo pipefail
export PATH="${REPO_ROOT}/venv/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
export PYTHONUNBUFFERED=1
unset DATABASE_URL
export DATABASE_PATH="${REPO_ROOT}/cannabis_papers.db"
cd "${REPO_ROOT}"
mkdir -p "${LOG_DIR}"
# Keep the Mac from idle-sleeping while the public site and daily harvest run.
exec /usr/bin/caffeinate -i "${RUNNER}" >> "${LOG_DIR}/macos_site.launchd.out.log" 2>> "${LOG_DIR}/macos_site.launchd.err.log"
EOF
chmod +x "${WRAPPER}"
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
  <key>ProgramArguments</key>
  <array>
    <string>${WRAPPER}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
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

echo "Installed ${LABEL}"
echo "  plist:   ${PLIST_PATH}"
echo "  wrapper: ${WRAPPER}"
echo "  catalog: ${REPO_ROOT}/cannabis_papers.db (SQLite; DATABASE_URL unset)"
echo "  bind:    127.0.0.1:8080"
echo "  logs:    ${LOG_DIR}/macos_site.error.log"
echo "Repair tab flags once: unset DATABASE_URL && DATABASE_PATH=${REPO_ROOT}/cannabis_papers.db \\"
echo "  ${PYTHON} ${REPO_ROOT}/scripts/repair_recent_tab_flags.py --since-harvested 2026-07-17"
echo "Then install the tunnel: ${REPO_ROOT}/scripts/macos/install_macos_cloudflared.sh"
