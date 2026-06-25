---
type: digest
status: active
date_created: 2026-06-25
tags: [layer-1, bars, yahoo, 429-fix, demo-paper, data]
---

# Yahoo (yfinance) PRIMARY bar-history source — 2026-06-25

Root fix for the Alpaca free-tier **429 storm** (~23k 429s) + tick-engine
**STALL**: exchange bar-history fetch hammered the venue REST APIs (~99 Alpaca
`/bars` every 5s tick). Yahoo Finance (yfinance 1.2.0, free/unlimited-grade)
becomes the PRIMARY bar-HISTORY source; exchange bar-fetch demoted to a
throttled FALLBACK. DEMO/PAPER. flow_not_block — no entry/size/exit gated.

## What changed
- NEW `polaris/scripts/_yahoo_bars.py`: deterministic ticker map (~95%) +
  cached async GPT(gpt-5-mini) fallback for the ambiguous tail + df→canonical
  `Bar` (reuses `persist_bars`/`ingest_bars_async` downstream — no new write
  path) + `fetch_yahoo_bars` (via `asyncio.to_thread`, never blocks the loop).
- EDIT `_production_bars.py` `fetch_bars_one`: Yahoo PRIMARY first; exchange
  branch only when Yahoo empty, gated by 300s per-symbol fallback cooldown.
  `gpt_client_factory` threaded through ingest fan-out.
- EDIT `_production_tick.py`: passes `default_gpt_factory` (keyless-safe).

## Mapping (deterministic, PURE)
- **alpaca** equity → symbol verbatim (`AAPL`→`AAPL`). **100%** — kills the 429.
- **okx** crypto USD-quote → `{base}-USD` (`BTC-USDT`→`BTC-USD`); non-USD-quote
  → None (fallback).
- **capital** forex clean-6 → `{pair}=X`; index/commodity → explicit tables
  (`US100`→`^NDX`, `J225`→`^N225`, `GOLD`→`GC=F`, `OIL_CRUDE`→`CL=F`, …); HK
  numeric equity → `{0pad4}.HK` (`0700`→`0700.HK`). Unmapped → GPT then fallback.

## 429 / STALL relief (verified)
- 429: Alpaca 100% Yahoo-mapped → ~0 Alpaca `/bars` in steady state. Exchange
  fallback only on Yahoo failure, then 300s-cooldown spaced.
- STALL: every yfinance call via `asyncio.to_thread` (yfinance uses curl_cffi →
  releases GIL on I/O); reviewer measured loop heartbeat gap 23ms during a 1.5s
  fetch — loop stays responsive.
- **within-period frame cache** (`_YF_FRAME_CACHE`, key `(venue,symbol,interval)`):
  the 1m no-skip buckets (OKX volume_burst) would re-pull the full 7d window
  every 5s → Yahoo IP-block risk. Cache caps Yahoo fetch to ~once per bar period
  per symbol regardless of tick cadence (1D→3600s TTL). Composes with the
  upstream `skip_if_current` gate.

## Live-price WS UNTOUCHED (the #1 invariant)
Yahoo supplies ONLY closed candles (indicators/regime/baseline). The stored
`Bar` keeps venue-native identity (`venue:symbol`, `underlying_group_id`); only
`source="yahoo"` flips. Live entry/exit price stays on the exchange WS quote
path (`quote_writer.live_px` / `feature_window`) — `_yahoo_bars.py` imports
none of it (reviewer-proven). Real fills come from the `--real-roundtrip` venue
close, never a Yahoo bar.

## Review
Fresh-Claude adversarial review (builder≠reviewer): APPROVE-WITH-NITS — live
price independence proven, 429+STALL relieved, mappings 0 wrong-instrument,
GPT fallback keyless-safe + cached-once, tz→UTC correct. Within-period cache
added in response to the lone deploy-safety concern. Tests 40 + 109 regression
green; mypy --strict + ruff clean.
