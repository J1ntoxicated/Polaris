#!/bin/bash
# Polaris — Start bot + Ops dashboard (single window).
#
# 시간 분기:
#   🟢 주중 09:00~17:00 = WORK     (내장 모니터 OPS @ 1913,30)
#   🟦 그 외             = OFFHOURS (LG 우측 OPS @ 3465,-1050)
#
# Behavior:
#   1. Kill realtime_runner + dashboard (graceful 10s)
#   2. Close orphan Polaris terminal windows
#   3. Restart realtime_runner via launchctl
#   4. Open single Ops dashboard window in profile-correct bounds
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ulimit -n 4096 2>/dev/null || true

echo "→ Killing existing Polaris processes..."
pgrep -f "[Pp]ython.*-m src\.paper\.realtime_runner" | xargs kill 2>/dev/null || true
pgrep -f "[Pp]ython.*-m src\.dashboard\." | xargs kill 2>/dev/null || true
for i in {1..10}; do
    if [ -z "$(pgrep -f '[Pp]ython.*-m src\.paper\.realtime_runner')" ]; then
        break
    fi
    sleep 1
done
pgrep -f "[Pp]ython.*-m src\." | xargs kill -9 2>/dev/null || true
sleep 0.5

echo "→ Closing orphan Polaris terminals..."
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
sleep 0.3

DOW=$((10#$(date +%u)))
HOUR=$((10#$(date +%H)))
if (( DOW <= 5 && HOUR >= 9 && HOUR < 17 )); then
    PROFILE="WORK"
    OPS_BOUNDS="1913, 30, 3833, 1069"
else
    PROFILE="OFFHOURS"
    OPS_BOUNDS="3465, -1050, 5382, -11"
fi
echo "Profile: $PROFILE (DOW=$DOW HOUR=$HOUR)"

if launchctl list | grep -q "com.polaris.paper.realtime"; then
    echo "→ Restarting realtime_runner via launchctl kickstart..."
    launchctl kickstart -k "gui/$(id -u)/com.polaris.paper.realtime"
else
    echo "→ Loading realtime_runner plist..."
    launchctl load ~/Library/LaunchAgents/com.polaris.paper.realtime.plist 2>/dev/null || true
fi
sleep 0.5

LAUNCHER="/tmp/polaris_ops.command"
cat > "$LAUNCHER" <<WRAPPER
#!/bin/bash
printf '\033]0;Polaris Ops\007'
cd "$PROJECT_DIR"
source .venv/bin/activate
exec python -m src.dashboard.ops
WRAPPER
chmod +x "$LAUNCHER"

osascript <<APPLESCRIPT
tell application "Terminal"
    activate
    set newTab to do script "exec $LAUNCHER"
    set targetWindow to window 1 where its tabs contains newTab
    set custom title of newTab to "Polaris Ops"
    try
        set custom title of targetWindow to "Polaris Ops"
    end try
    delay 0.3
    try
        set bounds of targetWindow to {$OPS_BOUNDS}
    on error errMsg
        log "bounds set failed: " & errMsg
    end try
end tell
APPLESCRIPT

echo ""
echo "✓ Polaris started ($PROFILE profile)"
echo "  realtime_runner: launchctl-managed (always-on)"
echo "  Ops dashboard:   bounds=$OPS_BOUNDS"
echo ""
echo "Stop: ./stop.sh   |   Restart bot only: launchctl kickstart -k gui/\$UID/com.polaris.paper.realtime"
