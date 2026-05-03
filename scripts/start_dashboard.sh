#!/bin/bash
# Polaris dashboard launcher — opens new Terminal window with auto-refresh
#
# Usage:
#   bash scripts/start_dashboard.sh              # 새 Terminal window
#   bash scripts/start_dashboard.sh --inline     # 현재 terminal에서 실행
#
# Auto-start at login: scripts/com.polaris.dashboard.plist + launchctl

set -e

POLARIS_DIR="/Users/jinyoon/Projects/Polaris"
REFRESH_SEC=60

CMD="cd $POLARIS_DIR && source .venv/bin/activate && python -m src.dashboard.cli --refresh $REFRESH_SEC"

if [[ "$1" == "--inline" ]]; then
    eval "$CMD"
    exit 0
fi

# 새 Terminal window 열기 (macOS)
osascript <<EOF
tell application "Terminal"
    activate
    do script "$CMD"
    set custom title of front window to "Polaris Dashboard"
end tell
EOF

echo "✅ Polaris dashboard opened in new Terminal window (refresh ${REFRESH_SEC}s)"
