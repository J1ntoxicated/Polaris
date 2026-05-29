#!/usr/bin/env bash
# Polaris v2 — Dashboard Stop Script (WEB).
# Stops the detached web visualizer server (tools.visualizer.server).
# 2026-05-29: terminal dashboard retired — no Terminal-window handling here.
set -euo pipefail

pkill -f "tools.visualizer.server" 2>/dev/null \
    && echo "✓ stopped web dashboard server" \
    || echo "(no web dashboard server running)"
