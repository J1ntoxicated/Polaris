#!/bin/bash
# Auto Invasion — Stop Bot + Close Windows
#
# Usage:
#   bash stop.sh             # graceful stop (30s SIGTERM grace)
#   bash stop.sh --flatten   # run emergency flatten_all first, then stop

FLATTEN=0
for arg in "$@"; do
    case "$arg" in
        --flatten) FLATTEN=1 ;;
    esac
done

# 0. Optional flatten — close all cap+alpaca positions before killing bot
if [ "$FLATTEN" = "1" ]; then
    echo "Running emergency flatten_all..."
    PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
    (cd "$PROJECT_DIR" && python3 -c "from invasion.ops.emergency import flatten_all; import json; print(json.dumps(flatten_all(), indent=2, default=str))")
    echo "Flatten complete."
fi

# 1. Graceful kill — 30s grace period so bot can close positions & save state
pgrep -f "[Pp]ython.*-m invasion" | xargs kill 2>/dev/null
sleep 30

# 2. Force kill (bot + all dashboards)
pgrep -f "[Pp]ython.*-m invasion" | xargs kill -9 2>/dev/null
pgrep -f "[Pp]ython.*dashboard" | xargs kill 2>/dev/null
sleep 0.3

# 3. Cleanup
pkill -f afplay 2>/dev/null

# 4. Remove state file (viewer will show "waiting for bot")
rm -f /tmp/invasion_state.json /tmp/invasion_state.json.tmp

# 5. Verify
if pgrep -f "[Pp]ython.*-m invasion" > /dev/null 2>&1; then
    echo "WARNING: invasion process still alive!"
else
    echo "Bot stopped"
fi

# 6. Close bot + dashboard windows
osascript -e '
tell application "Terminal"
    set wList to every window
    repeat with w in wList
        try
            set tabTitle to ""
            try
                set tabTitle to custom title of front tab of w
            end try
            if tabTitle starts with "Auto Invasion" or tabTitle is "invasion-bot" or tabTitle is "invasion-dashboard" or tabTitle is "Command Center" or tabTitle is "v9 Dashboard" or tabTitle is "invasion-log" or (name of w) contains "invasion_bot" or (name of w) contains "invasion_dash" or (name of w) contains "invasion_cmd" or (name of w) contains "invasion_log" then
                repeat with t in tabs of w
                    try
                        do script "exit" in t
                    end try
                end repeat
            end if
        end try
    end repeat
    delay 2
    set wList to every window
    repeat with w in wList
        try
            set tabTitle to ""
            try
                set tabTitle to custom title of front tab of w
            end try
            if tabTitle starts with "Auto Invasion" or tabTitle is "invasion-bot" or tabTitle is "invasion-dashboard" or tabTitle is "Command Center" or tabTitle is "v9 Dashboard" or tabTitle is "invasion-log" or (name of w) contains "invasion_bot" or (name of w) contains "invasion_dash" or (name of w) contains "invasion_cmd" or (name of w) contains "invasion_log" then
                close w
            end if
        end try
    end repeat
end tell' 2>/dev/null

echo "Bot stopped"
