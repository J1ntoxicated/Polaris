---
type: research
status: recorded
date_created: 2026-06-26
tags: [research, backfilled-frontmatter]
---

# Alpaca WS budget + bars feed follow RUNTIME feed (#43, 2026-06-26)

DEMO/PAPER. flow_not_block preserved. Base 6d39fd3. Follow-up to [[alpaca_sip_feed_fix_2026-06-26]] (#38 residual defect).

## Problem (night-live, PID 33073)
`POLARIS_ALPACA_FEED=sip` default, but the key lacks the SIP entitlement → WS
downgrades the LIVE feed to IEX (`_active_feed='iex'`). The #38 fix made the WS
SUBSCRIPTION cap follow `_active_feed` (30), BUT two consumers still keyed on the
STATIC configured token (`alpaca_feed_token()`=sip):
- **WS budget** `ws_budget_for_venue('alpaca')`=60 → `_focus_by_venue` seated 60
  symbols on a 30-cap IEX socket → "symbol limit exceeded" recurs (17:44·18:03·…);
  30 focus symbols blind + log noise.
- **Bar fetch** `fetch_bars(feed=sip)` on a non-SIP key → IEX 200/min 429 storm.

## Root cause
Budget + bars read the static feed token, not the runtime `_active_feed`. The
downgrade was visible only to the WS object, not to the schema budget / REST bars.

## Change (3 source files)
- **schema.py**: process-level latch `_alpaca_feed_downgraded` + `alpaca_runtime_feed()`
  resolver (iex once downgraded, else configured) + `mark_alpaca_feed_downgraded()`
  + `reset_alpaca_runtime_feed()` (test-only). `_alpaca_ws_budget_default()` now keys
  on `alpaca_runtime_feed()` → budget drops 60→30 in lockstep with the WS cap. One-way
  latch (a SIP entitlement does not appear mid-session → no flap).
- **ws.py**: `_downgrade_to_iex()` also calls `mark_alpaca_feed_downgraded()` so the
  schema budget tracks the live IEX cap. `_active_feed` (per-client) unchanged; the
  process latch is the cross-layer SSOT the budget reader shares with the WS writer.
- **adapter.py**: `fetch_bars` feed default sip→**iex** (bars don't need the SIP tape;
  IEX 1m prints are real-time + adequate; kills cold-start `feed=sip` 429 even BEFORE
  a downgrade is observed). Explicit `feed='sip'` still overrides (SIP-confirmed caller).

## flow_not_block
Budget is a resource/venue-API ceiling with REST bar fallback (name over budget still
bar-ingests) — NOT a membership cut, drop, throttle, or size-cut. SIP WS path is
byte-identical when no downgrade occurs (budget stays 60, `_effective_symbols` uncapped).

## Verify
TDD: budget follows runtime downgrade (60→60→30) · env override wins post-downgrade ·
WS re-subscribe re-caps to 30 after a runtime error frame (env-unset=sip path) · bars
default iex even on `POLARIS_ALPACA_FEED=sip` · explicit feed override. 5 new tests pass.
Full suite 3353 passed (only 2 pre-existing base failures + 4 pre-existing debate
errors, all confirmed independent). mypy --strict + ruff clean. Fresh adversarial
review APPROVE (0 key-leak, SIP non-regression, flow_not_block confirmed); its 1 valid
finding fixed: autouse `conftest` reset of the process latch (test-pollution guard).
Expected: symbol-limit recurrence 0 · all 30 IEX focus stream · bar 429↓.
