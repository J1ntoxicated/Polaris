#!/bin/bash
# Auto Invasion — 3-Window Dashboard + Headless Bot
#
# 시간 분기 (Jin 2026-04-13 지시):
#   🟢 주중 09:00~17:00 (근무)  = WORK profile (모니터 1개 사라짐 대응)
#   🟦 그 외 (저녁/주말)         = OFFHOURS profile (LG 3 모니터 full)

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Raise FD limit
ulimit -n 4096 2>/dev/null || ulimit -n 2048 2>/dev/null || true

# ── 1. Kill existing processes (P1-13 barrier wait) ──────────────────────────
# Jin DB corruption (2026-04-24 15:35) 사고 — sleep 3 후 SIGKILL 이 hourly_stats
# tick mid-write 도중 fire → WAL 손상. P1-13: SIGTERM 후 30s polling + 봇이 작성한
# data/.shutdown_blockers (backup_snapshot in-progress / hourly_stats tick) flag
# 가 있으면 추가 대기. 30s 후도 alive 면 SIGKILL warn.
pgrep -f "[Pp]ython.*-m invasion\.spot" | xargs kill 2>/dev/null
pgrep -f "[Pp]ython.*-m invasion --headless" | xargs kill 2>/dev/null
pgrep -f "[Pp]ython.*dashboard\.(operations|spot|intel|chart_window)" | xargs kill 2>/dev/null
pgrep -f "[Pp]ython.*logview" | xargs kill 2>/dev/null
# Poll up to 30s for graceful shutdown + barrier wait
PROJECT_DIR_LOCAL="$(cd "$(dirname "$0")" && pwd)"
BARRIER_FILE="${PROJECT_DIR_LOCAL}/data/.shutdown_blockers"
for i in {1..30}; do
    # 봇 process 가 종료됐고 barrier flag 도 비었으면 즉시 break
    if [ -z "$(pgrep -f '[Pp]ython.*-m invasion --headless')" ]; then
        if [ ! -s "$BARRIER_FILE" ]; then
            break
        fi
    fi
    sleep 1
done
# 30s 안에 barrier flag 안 비워졌으면 warn log
if [ -s "$BARRIER_FILE" ]; then
    echo "WARN: shutdown_blockers still present after 30s: $(cat $BARRIER_FILE)" >> "${PROJECT_DIR_LOCAL}/data/bot_restart.log"
fi
# Force kill only if still alive after 30s grace
pgrep -f "[Pp]ython.*-m invasion" | xargs kill -9 2>/dev/null
pkill -f afplay 2>/dev/null
# Cleanup stale barrier (process dead, flag stale)
rm -f "$BARRIER_FILE" 2>/dev/null
sleep 0.5

# ── 2. Close old windows ─────────────────────────────────────────────────────
osascript -e 'tell application "Terminal"
    set i to count of windows
    repeat while i > 0
        try
            set w to window i
            set tabTitle to ""
            try
                set tabTitle to custom title of front tab of w
            end try
            if tabTitle starts with "Auto Invasion" or tabTitle starts with "AI" or tabTitle starts with "v9" or tabTitle starts with "Sys" or tabTitle starts with "Bot" or tabTitle starts with "Trading" or tabTitle starts with "Signal" or tabTitle starts with "System" or tabTitle starts with "Intel" or tabTitle starts with "Log" or tabTitle starts with "Chart" or tabTitle starts with "Ops" then
                close w saving no
            end if
        end try
        set i to i - 1
    end repeat
end tell' 2>/dev/null
sleep 0.3

# ── 3. Profile detection (주중 9-17 = WORK / else = OFFHOURS) ────────────────
DOW=$(date +%u)    # 1-7 (월=1, 일=7)
HOUR=$(date +%H)
if [ "$DOW" -le 5 ] && [ "$HOUR" -ge 9 ] && [ "$HOUR" -lt 17 ]; then
    PROFILE="WORK"
    # Jin 2026-04-13 15:30 실측 (근무 중 창 배치):
    #   OPS/INTEL: 내장 모니터 나란히 / CHART: 왼쪽 external / BOT: 우측 상단 코너
    BOT_BOUNDS="3756, 30, 4056, 400"
    OPS_BOUNDS="18, 30, 1938, 1069"
    INTEL_BOUNDS="1913, 30, 3833, 1069"
    CHART_BOUNDS="-1918, 30, -1184, 1069"
else
    PROFILE="OFFHOURS"
    # Jin 2026-05-03 재배치 — LG RIGHT 모니터 fail / 배치 변경.
    #   Ops: LG LEFT 유지
    #   Intel: main UltraGear+ ultrawide 좌상 영역 (Jin 직접 reposition)
    BOT_BOUNDS="4800, 30, 5100, 400"
    OPS_BOUNDS="1545, -1050, 3465, -11"
    INTEL_BOUNDS="3465, -1050, 5382, -11"
    CHART_BOUNDS="-375, -1050, 359, -11"
fi

echo "Profile: $PROFILE (DOW=$DOW HOUR=$HOUR)"

# ── 4. Create launcher .command files ────────────────────────────────────────
BOT_CMD="/tmp/invasion_bot.command"
W_OPS="/tmp/invasion_ops.command"
W_INTEL="/tmp/invasion_intel.command"
W_CHART="/tmp/invasion_chart.command"

printf '#!/bin/bash\nclear\nulimit -n 4096 2>/dev/null || true\ncd '"'"'%s'"'"'\nexec python3 -m invasion.spot --headless\n' "$PROJECT_DIR" > "$BOT_CMD"
printf '#!/bin/bash\ncd '"'"'%s'"'"'\nexec python3 -m invasion.dashboard.operations\n' "$PROJECT_DIR" > "$W_OPS"
printf '#!/bin/bash\ncd '"'"'%s'"'"'\nexec python3 -m invasion.dashboard.intel\n' "$PROJECT_DIR" > "$W_INTEL"
printf '#!/bin/bash\ncd '"'"'%s'"'"'\nexec python3 -m invasion.dashboard.chart_window\n' "$PROJECT_DIR" > "$W_CHART"

chmod +x "$BOT_CMD" "$W_OPS" "$W_INTEL" "$W_CHART"

# ── 5. Launch bot + 3 dashboards (with profile bounds) ──────────────────────
osascript <<APPLESCRIPT
tell application "Terminal"

    -- BOT (headless — hidden)
    set wcBefore to count of windows
    do shell script "open /tmp/invasion_bot.command"
    repeat 20 times
        delay 0.5
        if (count of windows) > wcBefore then exit repeat
    end repeat
    delay 2.0
    set bounds of window 1 to {$BOT_BOUNDS}
    set custom title of front tab of window 1 to "Auto Invasion"

    -- W1: OPERATIONS
    delay 1.0
    set wc1 to count of windows
    do shell script "open /tmp/invasion_ops.command"
    repeat 20 times
        delay 0.5
        if (count of windows) > wc1 then exit repeat
    end repeat
    delay 0.5
    set bounds of window 1 to {$OPS_BOUNDS}
    set custom title of front tab of window 1 to "Ops: OPERATIONS"

    -- W2: INTEL (Jin 2026-05-02 부활 — operations + intel 2-window)
    delay 1.0
    set wc2 to count of windows
    do shell script "open /tmp/invasion_intel.command"
    repeat 20 times
        delay 0.5
        if (count of windows) > wc2 then exit repeat
    end repeat
    delay 0.5
    set bounds of window 1 to {$INTEL_BOUNDS}
    set custom title of front tab of window 1 to "Intel: INTELLIGENCE"

    -- CHART 폐기 유지 (Jin 2026-05-01) — 클라우드 visualizer 사용

end tell
APPLESCRIPT

echo ""
echo "Dashboard launched ($PROFILE profile):"
echo "  LEFT:   Operations     [$OPS_BOUNDS]"
echo "  RIGHT:  Intel          [$INTEL_BOUNDS]"
echo "  Bot:    Headless       [$BOT_BOUNDS]"
echo "  (Chart 폐기 — 클라우드 visualizer 사용)"
echo ""
echo "Adjust bounds in start.sh WORK/OFFHOURS blocks if needed."
