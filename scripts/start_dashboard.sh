#!/bin/bash
# Polaris dashboard launcher — content-fit Terminal window + live refresh
#
# Window size: 180 cols × 50 rows (content fit, NOT fullscreen)

set -e

POLARIS_DIR="/Users/jinyoon/Projects/Polaris"
REFRESH_SEC=10
COLS=180
ROWS=50

CMD="cd $POLARIS_DIR && source .venv/bin/activate && python -m src.dashboard.cli --refresh $REFRESH_SEC"

if [[ "$1" == "--inline" ]]; then
    eval "$CMD"
    exit 0
fi

osascript <<EOF
tell application "Terminal"
    activate
    do script "$CMD"
    set custom title of front window to "Polaris Dashboard"
    delay 0.4
    set number of rows of front window to $ROWS
    set number of columns of front window to $COLS
    set position of front window to {50, 50}
end tell
EOF

echo "✅ Polaris dashboard opened — ${COLS}×${ROWS} (refresh ${REFRESH_SEC}s)"
