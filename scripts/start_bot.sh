#!/bin/bash
# Polaris bot manual start — single canonical path (same code as watchdog).
# Clears the MANUAL_STOP sentinel on verified start success.
set -euo pipefail
cd /Users/jinyoon/Projects/Polaris
exec .venv/bin/python -m tools.ops.botctl start --manual
