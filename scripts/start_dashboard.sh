#!/bin/bash
# Polaris v2 Dashboard launcher — WORK/OFFHOURS profile + fixed cols×rows
#
# 모태 invasion start.sh 패턴 (Jin 2026-04-13 mandate, pre-reset 3c1f823 carry-over):
#   🟢 WORK     주중 09:00~17:00     INTEL @ (1913, 30)   — 우측 모니터 top-left (3 monitor 정렬)
#   🟦 OFFHOURS 그 외 (저녁/주말)    INTEL @ (3465, 30)   — 우측 LG monitor (확장 layout)
#
# Window cell size = 140 cols × 40 rows (dashboard_v2 한국어 가독성).
# Refresh = 5s default. Target = polaris.scripts.dashboard_v2 (Jin 2026-05-08 재설계).
# Override: POLARIS_DASH_TARGET=polaris.scripts.dashboard_v1 (legacy 220×55 trader-grade).
#
# Override:
#   POLARIS_DASH_PROFILE=WORK|OFFHOURS  (시간 자동 감지 override)
#   POLARIS_DASH_BOUNDS="x1, y1, x2, y2"
#   POLARIS_DASH_REFRESH=2              (refresh interval seconds)
#   POLARIS_DASH_WIDTH=220               (dashboard render width)
#   POLARIS_DASH_DB=data/polaris.sqlite
#   SKIP_TTY_CLEANUP=1                   (skip aggressive close)
#   ./scripts/start_dashboard.sh --inline (run in current terminal)

set -e

POLARIS_DIR="/Users/jinyoon/Projects/Polaris"
REFRESH_SEC="${POLARIS_DASH_REFRESH:-5}"
DASH_DB="${POLARIS_DASH_DB:-data/polaris.sqlite}"
DASH_TARGET="${POLARIS_DASH_TARGET:-polaris.scripts.dashboard_v2}"

# ── Profile detection (WORK 주중 9-17 / OFFHOURS 그 외) ─────────────────────
if [[ -n "${POLARIS_DASH_PROFILE:-}" ]]; then
    PROFILE="$POLARIS_DASH_PROFILE"
else
    DOW=$((10#$(date +%u)))    # 10# = force base 10 (avoid 08/09 octal)
    HOUR=$((10#$(date +%H)))
    if (( DOW <= 5 && HOUR >= 9 && HOUR < 17 )); then
        PROFILE="WORK"
    else
        PROFILE="OFFHOURS"
    fi
fi

# Profile별 INTEL window full bounds (모태 invasion start.sh 정확 매칭)
#   WORK     INTEL_BOUNDS="1913, 30, 3833, 1069"   — 우측 모니터 (3 monitor work setup)
#   OFFHOURS INTEL_BOUNDS="3465, -1050, 5382, -11" — 우측 LG monitor (확장 layout)
if [[ "$PROFILE" == "WORK" ]]; then
    BOUNDS_DEFAULT="1913, 30, 3083, 850"
else
    BOUNDS_DEFAULT="3465, -1050, 4635, -250"
fi
BOUNDS="${POLARIS_DASH_BOUNDS:-$BOUNDS_DEFAULT}"

# Wrapper script — OSC 0 escape sets Terminal title once, then exec dashboard
LAUNCHER="/tmp/polaris_dashboard.command"
cat > "$LAUNCHER" <<WRAPPER
#!/bin/bash
printf '\033]0;Polaris Dashboard\007'
cd "$POLARIS_DIR"
if [[ -d ".venv" ]]; then
    source .venv/bin/activate
fi
exec python3 -m ${DASH_TARGET} \\
    --refresh ${REFRESH_SEC} \\
    --db ${DASH_DB}
WRAPPER
chmod +x "$LAUNCHER"

CMD="exec $LAUNCHER"

if [[ "${1:-}" == "--inline" ]]; then
    eval "$CMD"
    exit 0
fi

# ── 1a. Kill dashboard process (always) ─────────────────────────────────────
pkill -f "polaris.scripts.dashboard_v[12]" 2>/dev/null || true
sleep 0.5

# ── 1b. Close existing Polaris Dashboard / orphan shell Terminal windows ───
osascript <<'APPLESCRIPT' 2>/dev/null
tell application "Terminal"
    set i to count of windows
    repeat while i > 0
        try
            set w to window i
            set tt to ""
            try
                set tt to custom title of front tab of w
            end try
            if (tt starts with "Polaris Dashboard") or (tt contains "polaris.scripts.dashboard_v") or (tt contains "polaris_dashboard.command") then
                close w saving no
            end if
        end try
        set i to i - 1
    end repeat
end tell
APPLESCRIPT
sleep 0.3

# ── 1c. (Optional) Aggressive tty cleanup — close ALL except Claude tty ────
# Jin mandate "이 클러드 창 제외 나머지 다 끄고 — 메모리 누수로 뻑나서":
# Override: SKIP_TTY_CLEANUP=1 ./start_dashboard.sh (skip aggressive)
if [[ -z "${SKIP_TTY_CLEANUP:-}" ]]; then
    CLAUDE_TTY=$(ps -p $$ -o tty= 2>/dev/null | tr -d ' ')
    if [[ -n "$CLAUDE_TTY" ]]; then
        osascript <<APPLESCRIPT 2>/dev/null || true
tell application "Terminal"
    set targetTTY to "/dev/$CLAUDE_TTY"
    set i to count of windows
    repeat while i > 0
        try
            set w to window i
            set wtty to tty of front tab of w
            if wtty is not equal to targetTTY then
                close w saving no
            end if
        end try
        set i to i - 1
    end repeat
end tell
APPLESCRIPT
        echo "🧹 Closed all Terminal windows except Claude tty=$CLAUDE_TTY"
    fi
    sleep 0.3
fi

# ── 2. Launch dashboard window — full INTEL bounds (모태 패턴) ─────────────
osascript <<EOF
tell application "Terminal"
    activate
    set newTab to do script "$CMD"
    set targetWindow to window 1 where its tabs contains newTab
    set custom title of newTab to "Polaris Dashboard"
    try
        set custom title of targetWindow to "Polaris Dashboard"
    end try
    delay 0.3
    try
        set bounds of targetWindow to {$BOUNDS}
    on error errMsg
        log "bounds set failed: " & errMsg
    end try
end tell
EOF

echo "✅ Polaris dashboard launched ($PROFILE profile)"
echo "   bounds:   $BOUNDS"
echo "   target:   $DASH_TARGET"
echo "   refresh:  ${REFRESH_SEC}s"
echo "   db:       $DASH_DB"
echo "   override: POLARIS_DASH_PROFILE / POLARIS_DASH_BOUNDS / POLARIS_DASH_REFRESH / POLARIS_DASH_TARGET"
