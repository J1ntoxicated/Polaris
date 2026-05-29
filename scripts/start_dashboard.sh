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

# Already listening? Just (re)open the browser.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "✅ web dashboard already running on :$PORT"
else
    mkdir -p data/paper
    nohup python3 -m tools.visualizer.server --db "$DASH_DB" --port "$PORT" \
        > "$LOG" 2>&1 &
    # Wait for the port to come up (max ~10s).
    for _ in $(seq 1 20); do
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
        sleep 0.5
    done
    echo "✅ web dashboard launched on :$PORT (db=$DASH_DB, log=$LOG)"
fi

open "http://localhost:$PORT" 2>/dev/null || \
    echo "   open http://localhost:$PORT in your browser"
