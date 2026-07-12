#!/bin/bash
# Remove the six Polaris ops automation agents (watchdog / daily restart /
# daily digest / nightly replay / probe reranker / score_f remap refresh).
# Touches ONLY these labels — never the dashboard agent, never any running
# process (no signals of any kind; a running bot keeps running).
set -euo pipefail

AGENTS_DIR="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
OWN=(com.polaris.watchdog com.polaris.daily.restart com.polaris.daily.digest com.polaris.replay.nightly com.polaris.probe.reranker com.polaris.scoref.remap.refresh)

for label in "${OWN[@]}"; do
    launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
    rm -f "$AGENTS_DIR/$label.plist"
    echo "removed: $label"
done
echo "done — bot process and dashboard untouched."
