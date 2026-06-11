#!/bin/bash
# Polaris bot manual stop — creates the MANUAL_STOP sentinel BEFORE SIGTERM
# so the watchdog can never resurrect an intentionally stopped bot.
# SIGTERM to the exact pidfile-verified PID only; never escalates.
set -euo pipefail
cd /Users/jinyoon/Projects/Polaris
exec .venv/bin/python -m tools.ops.botctl stop --manual
