#!/bin/bash
# mac_tide_push — ANNUAL tide-horizon refresh from a residential IP.
#
# PLA serves GitHub runners a Cloudflare JS challenge (ASN policy, probed
# 2026-08-02 — .github/workflows/pla-probe.yml, all client shapes 403),
# but its tide predictions are astronomy: stable and byte-identical on
# re-pull. So the app's tide backend is data/tides_extrema.json — a year
# of London Bridge HW/LW events distilled from PLA's own minute
# predictions by pipeline/prefetch_tides.py, which needs a residential
# egress. Run this BY HAND, about once a year, when the
# tide-horizon-watch job starts emailing (horizon < 28 d — you'll have a
# month of runway left). Only new tail days are fetched (the scratch
# cache resumes); it re-verifies against the committed cache, pushes,
# and kicks a rebuild. CI itself never fetches tides. Pace yourself:
# ~370 fresh days in one sitting can trip PLA's rate limiter even on a
# home IP (observed 2026-08-02) — the script's retries handle it.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # gh under launchd

REPO="$HOME/Claude/tideway-planner"

# One-time: --install-launchd writes the plist (paths expanded locally, so
# none live in this public repo) and loads the monthly job.
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
    <dict><key>Day</key><integer>1</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>20</integer></dict>
    <key>StandardOutPath</key><string>$HOME/Library/Logs/tideway-tides.log</string>
    <key>StandardErrorPath</key><string>$HOME/Library/Logs/tideway-tides.log</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/com.tideway.tides" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "installed + loaded com.tideway.tides (monthly, 1st 07:20, log ~/Library/Logs/tideway-tides.log)"
    exit 0
fi

cd "$REPO"
echo "=== $(date '+%F %T') tide horizon refresh ==="
git pull --rebase --autostash origin main

(cd pipeline && python3 prefetch_tides.py --days 370)

git add pipeline/data/tides_extrema.json
if git diff --cached --quiet; then
    echo "extrema unchanged — nothing to push"
    exit 0
fi
git commit -m "data: tide extrema horizon refresh $(date -u +%FT%TZ)"
git pull --rebase origin main               # race guard vs the 6-hourly bot commit
git push origin main
gh workflow run build-grid -R Robdog123456/tideway-planner || true
echo "pushed extrema refresh and dispatched build-grid"
