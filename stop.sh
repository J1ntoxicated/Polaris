#!/bin/bash
# Polaris — Stop bot + close Ops dashboard.
#
# Usage:
#   ./stop.sh              # graceful (10s SIGTERM grace)
#   ./stop.sh --force      # immediate SIGKILL
set -e

FORCE=0
[[ "$1" == "--force" ]] && FORCE=1

echo "→ Unloading realtime_runner launchd job..."
launchctl unload ~/Library/LaunchAgents/com.polaris.paper.realtime.plist 2>/dev/null || true

echo "→ Killing Polaris processes..."
if [[ $FORCE -eq 1 ]]; then
    pgrep -f "[Pp]ython.*-m src\." | xargs kill -9 2>/dev/null || true
else
    pgrep -f "[Pp]ython.*-m src\.paper\.realtime_runner" | xargs kill 2>/dev/null || true
    pgrep -f "[Pp]ython.*-m src\.dashboard\." | xargs kill 2>/dev/null || true
    for i in {1..10}; do
        if [ -z "$(pgrep -f '[Pp]ython.*-m src\.')" ]; then
            break
        fi
        sleep 1
    done
    pgrep -f "[Pp]ython.*-m src\." | xargs kill -9 2>/dev/null || true
fi
sleep 0.5

echo "→ Closing Polaris terminal windows..."
osascript -e 'tell application "Terminal"
    set i to count of windows
    repeat while i > 0
        try
            set w to window i
            set tt to ""
            try
                set tt to custom title of front tab of w
            end try
            if (tt starts with "Polaris") then
                close w saving no
            end if
        end try
        set i to i - 1
    end repeat
end tell' 2>/dev/null

echo "✓ Polaris stopped."
echo "  Restart: ./start.sh"
