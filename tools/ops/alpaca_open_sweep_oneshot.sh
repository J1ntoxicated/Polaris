#!/bin/zsh
# Polaris — ONE-SHOT Alpaca orphan liquidation at the US equity OPEN.
#
# WHY: the venue held 10 untracked long orphans (DB open/active alpaca rows = 0,
# so the exit engine is BLIND to them) bleeding ~-$2,416 + tying up margin. They
# can only be market-flattened while the session is OPEN; the daily restart sweep
# runs at 07:30 AEST (= US CLOSED) so it always defers the equity leg. This job
# fires at 23:32 AEST (= 09:32 ET, just past the 09:30 open) to run the same
# guarded sweep when the market is actually open. The script self-guards on the
# Alpaca clock (defers if closed) and NEVER touches a DB-tracked position.
#
# flow_not_block — frees capital to TRADE / clears blind orphans; not a defensive
# throttle. DEMO/PAPER only; virtual funds. Self-removes so it fires exactly once.
# Installed 2026-06-22 per Jin "알파카 포지션 읽어서 정리하고 시작".
set -u
REPO="/Users/jinyoon/Projects/Polaris"
LABEL="com.polaris.alpaca.opensweep"
cd "$REPO" || exit 1
"$REPO/.venv/bin/python" -m polaris.scripts.liquidate_alpaca_orphans \
    --db "$REPO/data/polaris_live.sqlite"
# one-shot teardown: drop the plist first (so it can't reload), then bootout the
# in-memory job last (may SIGTERM this wrapper — the sweep already completed).
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
