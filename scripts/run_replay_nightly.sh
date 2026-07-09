#!/bin/bash
# Polaris nightly replay — validate strategy retunes OFFLINE on stored bars (OOS).
#
# Runs the deterministic replay harness (signal -> regime -> sizing -> exit, real
# fees, next-bar-open fills) over the trailing window of persisted 1H bars and
# persists one replay_runs row + per-tier benchmark_results to the live DB the
# dashboard reads. flow_not_block: offline analysis only, zero live behaviour.
#
# Root cause this closes: the harness CLI was present but never invoked outside
# the test suite, and its read-model default pointed at an unread DB -> 0 rows.
# This wrapper is the missing scheduled caller.
#
# Override: POLARIS_REPLAY_DB / POLARIS_REPLAY_INTERVAL / POLARIS_REPLAY_DAYS /
#           POLARIS_REPLAY_INSTRUMENTS (space-separated venue:symbol, pins an
#           exact list) / POLARIS_REPLAY_TOP_N (no pinned list -> replay the
#           top-N most-liquid ACTIVE universe instruments, DB-driven — no
#           hardcoded symbol default) / POLARIS_REPLAY_TRIALS
set -e

POLARIS_DIR="/Users/jinyoon/Projects/Polaris"
DB="${POLARIS_REPLAY_DB:-data/polaris_live.sqlite}"
INTERVAL="${POLARIS_REPLAY_INTERVAL:-1H}"
DAYS="${POLARIS_REPLAY_DAYS:-30}"
TRIALS="${POLARIS_REPLAY_TRIALS:-7}"
TOP_N="${POLARIS_REPLAY_TOP_N:-3}"
INSTRUMENTS="${POLARIS_REPLAY_INSTRUMENTS:-}"

cd "$POLARIS_DIR"

# Trailing window: [max_bar_ts - DAYS, max_bar_ts] for the chosen interval.
END_TS="$(sqlite3 "$DB" "SELECT MAX(ts) FROM bars WHERE bar_interval='$INTERVAL';")"
if [ -z "$END_TS" ] || [ "$END_TS" = "" ]; then
  echo "run_replay_nightly: no $INTERVAL bars in $DB — nothing to replay" >&2
  exit 0
fi
START_TS=$((END_TS - DAYS * 24 * 3600))

# No pinned POLARIS_REPLAY_INSTRUMENTS -> let run_replay itself resolve the
# top-N most-liquid ACTIVE universe instruments from the live DB (Layer 0
# dynamic universe; no hardcoded venue:symbol default in this script).
if [ -n "$INSTRUMENTS" ]; then
  INSTRUMENT_ARGS=(--instruments $INSTRUMENTS)
  echo "run_replay_nightly: $INTERVAL window $START_TS..$END_TS instruments=[$INSTRUMENTS] (pinned)"
else
  INSTRUMENT_ARGS=(--top-n-active "$TOP_N")
  echo "run_replay_nightly: $INTERVAL window $START_TS..$END_TS top-$TOP_N active by vol_24h_usd (DB-driven)"
fi
python3 -m polaris.scripts.run_replay \
  --db "$DB" \
  --read-model-db "$DB" \
  --interval "$INTERVAL" \
  "${INSTRUMENT_ARGS[@]}" \
  --start-ts "$START_TS" \
  --end-ts "$END_TS" \
  --trials "$TRIALS"
