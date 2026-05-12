#!/usr/bin/env bash
# Polaris v2 — Dashboard Stop Script
# Closes Terminal.app window(s) titled "Polaris Dashboard"
# Usage: ./scripts/stop_dashboard.sh [--title NAME]

set -euo pipefail

TITLE="${POLARIS_DASH_TITLE:-Polaris Dashboard}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --title) TITLE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--title NAME] (default: 'Polaris Dashboard')"
            exit 0
            ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Kill python dashboard processes
pkill -f "polaris.scripts.dashboard_v0" 2>/dev/null && echo "✓ killed dashboard processes" || echo "(no dashboard processes running)"

# Close Terminal windows matching title
osascript <<APPLESCRIPT
tell application "Terminal"
    set targetTitle to "${TITLE}"
    set closedCount to 0
    set winList to every window whose custom title is targetTitle
    repeat with w in winList
        close w saving no
        set closedCount to closedCount + 1
    end repeat
    return closedCount
end tell
APPLESCRIPT

echo "✓ Dashboard stop complete"
