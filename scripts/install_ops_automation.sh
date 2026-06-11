#!/bin/bash
# Polaris ops automation installer (idempotent).
#
# 1. Removes the two GHOST agents (com.polaris.paper.realtime, com.polaris.paper.daily)
#    whose modules (src.paper.*) no longer exist — they have been silently
#    failing/crash-loop-throttled daily. Backed up first, never deleted blind.
#    bootout is a label-level launchd op; with the modules gone there is no
#    live process behind either label, so nothing can be terminated by this.
# 2. Installs com.polaris.{watchdog,daily.restart,daily.digest}.
#
# Untouchable: the dashboard agent (not referenced anywhere in this script)
# and every running process — this script sends no signals of any kind.
# NOTE: avoid re-running around 07:30 local (daily-restart window); a reload
# there can interrupt a restart between stop and start (watchdog recovers <=5min).
set -euo pipefail

PROJECT_DIR="/Users/jinyoon/Projects/Polaris"
AGENTS_DIR="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$AGENTS_DIR/_polaris_ghost_backup_$TS"

GHOSTS=(com.polaris.paper.realtime com.polaris.paper.daily)
OWN=(com.polaris.watchdog com.polaris.daily.restart com.polaris.daily.digest)

echo "== 1/4 ghost cleanup =="
for label in "${GHOSTS[@]}"; do
    plist="$AGENTS_DIR/$label.plist"
    if [[ -f "$plist" ]]; then
        mkdir -p "$BACKUP_DIR"
        cp "$plist" "$BACKUP_DIR/"
        launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
        rm "$plist"
        echo "  removed ghost: $label (backup: $BACKUP_DIR)"
    else
        echo "  ghost absent:  $label"
    fi
done

echo "== 2/4 stale pidfile check (file-only — no process is ever signalled) =="
PIDFILE="$PROJECT_DIR/data/paper/production.pid"
if [[ -f "$PIDFILE" ]]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || echo "")"
    if [[ -n "$pid" ]] && ps -p "$pid" -o command= 2>/dev/null \
            | grep -qF "polaris.scripts.ignite_p1"; then
        echo "  pidfile live (pid $pid) — kept"
    else
        rm -f "$PIDFILE"
        echo "  stale pidfile removed"
    fi
else
    echo "  no pidfile"
fi

mkdir -p "$PROJECT_DIR/data/paper/ops"

echo "== 3/4 install own agents (bootout-if-loaded -> bootstrap = idempotent) =="
for label in "${OWN[@]}"; do
    src="$PROJECT_DIR/scripts/launchd/$label.plist"
    dst="$AGENTS_DIR/$label.plist"
    plutil -lint "$src" >/dev/null
    cp "$src" "$dst"
    launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_N" "$dst"
    echo "  installed: $label"
done

echo "== 4/4 verify =="
for label in "${OWN[@]}"; do
    if launchctl print "gui/$UID_N/$label" >/dev/null 2>&1; then
        echo "  loaded: $label"
    else
        echo "  WARN not loaded: $label"
    fi
done
echo "done. alerts: $PROJECT_DIR/data/paper/ops/ops_alerts.log"
