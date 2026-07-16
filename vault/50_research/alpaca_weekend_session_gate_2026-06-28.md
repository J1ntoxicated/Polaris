---
type: research
status: recorded
date_created: 2026-06-28
tags: [research, backfilled-frontmatter]
---

# Alpaca weekend session-gate + anti-churn fix — 2026-06-28

Branch `agent-alpaca-session` (base main 654f1df). DEMO/PAPER. flow_not_block /
aggressive / 9-stack / -1.0R rail untouched. builder ≠ reviewer (fresh adversarial
review pending).

## Root cause (one bug, two gaps)
`us_equity_session_state` (`equity_session_gate.py`) was a pure **time-of-day**
clock — weekend-BLIND. Sat 10:08 ET (14:08 UTC) fell in the [09:30,16:00) RTH
band → it returned `"rth"`. Both gates that read this SSOT were wrongly OPEN all
weekend:
- **WS gate** `AlpacaQuoteWS._gated_at` (`ws.py`) → `_gated_at`=False → WS
  connected to a CLOSED market → 0 data → idle watchdog (`ws_common.py:318`)
  → `idle 30.1s >= 30.0s — forcing reconnect` infinite loop + handshake timeout.
- **Entry-hold** `equity_entry_held_for_session` → `held`=False → weekend equity
  entries reached Alpaca → closed-market reject → buying-power drain.

## Fix (3, all connect/entry-timing — no sizing/rail/trade-path touch)
1. **① WS gate weekend/warm** — `us_equity_session_state` now WEEKEND-AWARE
   (Sat/Sun → `"closed"`, one guard shuts BOTH gates via the shared SSOT).
   `_gated_at` ungates RTH **OR** new `equity_ws_warm_active` (weekday pre-open
   30-min lead, `WS_WARM_LEAD_MINUTES`) so the socket reconnects ahead of the
   open (#66 pre-warm preserved). Weekend/overnight = gated (no socket) → the
   idle-reconnect loop stops.
2. **② entry-hold gap** — auto-closed by the same weekend-aware SSOT (entry-hold
   reads `equity_entry_held_for_session` → `us_equity_session_state`). One fix,
   both gates. Reference pattern: `_session_map.equity_fetch_active`
   (`weekday()>=5`, already weekend-correct — now redundant but left untouched).
3. **③ anti-churn novelty stamp** — `_handle_open_reject` (`_production_reject.py`)
   now stamps `state.last_entry_by_key[(venue,symbol,strategy_id)] =
   (created_at_bar, side)` on a TRANSIENT external reject (buying_power /
   market_closed / no_fill …). Was written ONLY on a real fill, so a reject left
   the key None → every tick `is_novel_reentry`=True → cooldown exempted → same
   signal re-fired (42× churn; a reject INSERTs no positions row so cooldown /
   same-side guards could not catch it). Same-bar same-side → not novel → cooldown
   applies → churn stops; new bar / side flip still novel (entry resumes,
   flow_not_block). COMPLIANCE 51155 EXCLUDED (blocklist is its mechanism).

## Scope safety (verified)
- OKX 24/7 untouched (`OKXTickerWS._is_gated()`=False on Sat; no `is_gated`).
- Capital weekend gate independent (`CapitalMarketWS._is_weekend`); `fx_weekend`
  label unchanged. Alpaca CRYPTO (asset_class crypto) NOT gated — only equity WS
  exists in `venues/alpaca` (no crypto WS class). `equity_fetch_active` crypto/
  Capital = True (unchanged).

## Verify
- New TDD: `test_venue_ws.py` (Sat gated / warm ungate / overnight gated),
  `test_us_equity_cal_pdt_gate.py` H-section (weekend closed / entry-held / warm),
  `test_reject_novelty_stamp.py` (BP-reject stamps, 51155 doesn't, new-bar refire).
- 258 pass across affected modules; full suite 3896 pass (2 fail + 4 err
  PRE-EXISTING on base 654f1df: layer0 universe/sentinel + run_debate FileNotFound).
- `mypy --strict` 318 files clean; `ruff` clean; rejection-keyword sweep 0.
