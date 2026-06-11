#!/bin/bash
# Remove the three Polaris ops automation agents (watchdog / daily restart /
# daily digest). Touches ONLY these labels — never the dashboard agent, never
# any running process (no signals of any kind; a running bot keeps running).
set -euo pipefail

AGENTS_DIR="$HOME/Library/LaunchAgents"
UID_N="$(id -u)"
OWN=(com.polaris.watchdog com.polaris.daily.restart com.polaris.daily.digest)

for label in "${OWN[@]}"; do
    launchctl bootout "gui/$UID_N/$label" 2>/dev/null || true
    rm -f "$AGENTS_DIR/$label.plist"
    echo "removed: $label"
done
echo "done — bot process and dashboard untouched."
