#!/bin/bash
# Polaris dashboard launcher — WEB (Neural Cloud + analysis board).
#
# 2026-05-29: the dashboard is now a single web page (left = sphere, right =
# analysis board), served by tools.visualizer.server. The old terminal
# dashboard (dashboard_v2) + aggressive Terminal-window tty-cleanup are retired
# — that cleanup mis-detected the Claude Code shell's tty and closed Jin's
# window (feedback_never_kill_claude_session). This launcher only starts the
# detached web server and opens the browser; it never touches Terminal windows.
#
# Override: POLARIS_DASH_DB=data/polaris_live.sqlite  POLARIS_DASH_PORT=8770
set -e

POLARIS_DIR="/Users/jinyoon/Projects/Polaris"
DASH_DB="${POLARIS_DASH_DB:-data/polaris_live.sqlite}"
PORT="${POLARIS_DASH_PORT:-8770}"
LOG="data/paper/spaceviz.log"

cd "$POLARIS_DIR"

_start_server() {
    mkdir -p data/paper
    # Mirror the bot's virtual-account mode so the dashboard mode-banner reflects
    # the RUNNING bot's state (botctl defaults POLARIS_VIRTUAL_ACCOUNT=1). The
    # dashboard is a separate process; without this it read its own (empty) env
    # and mislabelled a virtual bot as "real-venue ON". Overridable to match a
    # non-default bot launch.
    export POLARIS_VIRTUAL_ACCOUNT="${POLARIS_VIRTUAL_ACCOUNT:-1}"
    nohup python3 -m tools.visualizer.server --db "$DASH_DB" --port "$PORT" \
        > "$LOG" 2>&1 &
    # Wait for the port to come up (max ~10s).
    for _ in $(seq 1 20); do
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
        sleep 0.5
    done
    echo "✅ web dashboard launched on :$PORT (db=$DASH_DB, log=$LOG)"
}

# P0-1: stale-server guard. If already listening, compare the RUNNING server's
# git SHA (meta.git_sha off /api/snapshot) against this checkout's HEAD — a
# server started before the latest deploy silently serves old code with no
# visible signal. Mismatch → stop + restart; match → no-op (never a spurious
# restart mid-session).
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    HEAD_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    RUNNING_SHA="$(curl -s --max-time 3 "http://localhost:$PORT/api/snapshot" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("meta",{}).get("git_sha","unknown"))' \
        2>/dev/null || echo unknown)"
    if [ "$HEAD_SHA" != "unknown" ] && [ "$RUNNING_SHA" != "unknown" ] && [ "$HEAD_SHA" != "$RUNNING_SHA" ]; then
        echo "⚠️  stale dashboard server (running=$RUNNING_SHA, HEAD=$HEAD_SHA) — restarting"
        ./scripts/stop_dashboard.sh
        for _ in $(seq 1 20); do
            lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
            sleep 0.5
        done
        _start_server
    else
        echo "✅ web dashboard already running on :$PORT (sha=$RUNNING_SHA)"
    fi
else
    _start_server
fi

# Jin 2026-06-22: NO auto browser-launch — Jin refreshes his own tab; Claude
# verifies via the Claude Preview MCP. Set POLARIS_DASH_OPEN=1 to force-open.
if [ "${POLARIS_DASH_OPEN:-0}" = "1" ]; then
    open "http://localhost:$PORT" 2>/dev/null || \
        echo "   open http://localhost:$PORT in your browser"
else
    echo "   (browser not auto-opened — refresh http://localhost:$PORT yourself)"
fi
