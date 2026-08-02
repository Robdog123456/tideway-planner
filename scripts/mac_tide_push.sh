#!/bin/bash
# mac_tide_push — daily tide-cache top-up from a residential IP.
#
# PLA's bot-wall started 403ing GitHub-runner IPs on 2026-07-31, so the
# Actions pipeline can no longer fetch tides itself (build.yml soft-gates
# and keeps publishing off the committed cache). This script does the one
# thing that needs a residential egress: refill the rolling 35-day tide
# cache, push it, and kick an immediate rebuild. Run daily by launchd
# (com.tideway.tides, plist in ~/Library/LaunchAgents; log:
# ~/Library/Logs/tideway-tides.log). Safe to run by hand any time.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # gh under launchd

REPO="$HOME/Claude/tideway-planner"

# One-time: --install-launchd writes the plist (paths expanded locally, so
# none live in this public repo) and loads the daily 07:20 job.
if [[ "${1:-}" == "--install-launchd" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.tideway.tides.plist"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.tideway.tides</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO/scripts/mac_tide_push.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>20</integer></dict>
    <key>StandardOutPath</key><string>$HOME/Library/Logs/tideway-tides.log</string>
    <key>StandardErrorPath</key><string>$HOME/Library/Logs/tideway-tides.log</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/com.tideway.tides" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed + loaded com.tideway.tides (daily 07:20, log ~/Library/Logs/tideway-tides.log)"
    exit 0
fi

cd "$REPO"
echo "=== $(date '+%F %T') tide top-up ==="
git pull --rebase --autostash origin main

(cd pipeline && python3 fetch_tides.py --live --days 35)

git add pipeline/data/tides                 # stages deletions (prunes) too
if git diff --cached --quiet; then
    echo "tides unchanged — nothing to push"
    exit 0
fi
git commit -m "data: tide cache top-up from Mac $(date -u +%FT%TZ)"
git pull --rebase origin main               # race guard vs the 6-hourly bot commit
git push origin main
gh workflow run build-grid -R Robdog123456/tideway-planner || true
echo "pushed tide top-up and dispatched build-grid"
