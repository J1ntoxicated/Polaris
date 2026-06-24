---
type: debate
status: design-only
topic: rank-attention-gradient
date_created: 2026-06-24
participants: [claude-opus-4-8, gpt-5.5, gemini-2.5-pro]
rounds: 1
verdict: CONVERGED — no-drop frequency gradient; build pending Jin approval
related: [[feedback_per_ticker_tailored_gates]], [[layer-0-universe-discovery]], [[feedback_flow_not_block]]
tags: [debate, layer-0, attention-gradient, flow-not-block, design]
---

# Rank-Attention Gradient — count-cap → frequency 경사

Jin: "개수 제한을 왜 걸어. 랭킹은 OK, 갯수 제한은 아니지." Count cap = wrong
abstraction. Rank(priority) OK; cut-off-N + drop rest = wrong. 전부 watch +
rank로 attention 차등 (절벽 아닌 경사). 설계만 — 거동변경 build는 Jin 승인 후.

## Two verified cliffs (file:line)
- CLIFF 1 valid→active: `WATCH_MAX=120` (schema.py:168; _ranking.py:133).
  Live: OKX 189→120, Capital 235→120, Alpaca 13282→120 dropped from deep-watch.
- CLIFF 2 active→focus: `[12,48]` dynamic target (watchlist.py:175,:414
  `merged[:target_size]`) × asset-class quota (schema.py:244). OKX 8 of 120.

## Gradient architecture (drop NONE; tier = cadence band)
- Tier-S Hot: WS realtime + every-tick eval. Bound by per-venue WS budget.
- Tier-A Warm: REST bar poll every cycle + eval. No WS.
- Tier-B Cool: REST poll every K cycles, lighter eval.
- Tier-T Tail: low-freq REST heartbeat (price/vol/spread/ATR fresh), f_floor>0.
- Membership = ALL valid. Tier from grouped-z opportunity-merit rank.

## Quota removal
Asset-class quota + `_apply_asset_class_quota` (~145 LOC, watchlist.py:269-414)
exist ONLY to share a scarce ≤48 window. No scarce window ⇒ no starvation ⇒
quota obsolete. Single grouped-z merit rank (curator 5-lens; _ranking.py:120
group-z already present) replaces it. Diversity, if needed = soft crowding
penalty (nudge rank), never a hard slot floor. `compute_dynamic_target_size`
+ `[12,48]` clamp + `FOCUS_CYCLE_TARGET` become dead.

## Resource safety = three frequency knobs, zero membership cut
- WS: Σ Tier-S subs ≤ venue WS budget. Capital ~40 genuine; OKX/Alpaca no
  in-code cap (raise `WS_SYMBOLS_PER_VENUE` per-venue), ~25% headroom reserve.
- REST: Semaphore(8) bounds instantaneous QPS regardless of N (_production_
  bars.py:408). Total work/sec = Σ N_tier/period_tier — bound by tier period.
- DB: 1Hz coalesced flush (quote_writer.py:130) — one txn/sec, symbol-count
  independent. Tier-aware downsample (S tick … T heartbeat) bounds row growth.
- Tick eval: no count cap, cheap, already full-watch (tick_engine:448).

## Debate convergence (GPT 5.5 + Gemini 2.5-pro)
Both: no-drop / no focus cap / rank gradient / S=WS+hi-res / mid=REST /
tail=heartbeat / promotion=refresh-first / DB tier-downsample / grouped-z
calibrated single merit / quota removed / diversity=soft crowding not quota.
Traps both flagged: stale-tail → recency-decay + max_stale_age force-poll +
refresh-before-promote; churn → hysteresis + min-dwell; DB → downsample;
latency-inversion → separate hot/tail ingest queues; + Fast-Path for flash.

## Build increments (gradient migration; each observe-only, byte-identical)
INC1 tier metadata → INC3 tier-cadence scaffold → INC2 remove 120 (tail lands
in low-cadence T) → INC4 remove quota → INC5 staleness SLA + refresh-first +
hysteresis + Fast-Path. SAFER order: cadence BEFORE removing 120 (else 13282
Alpaca full-rate). Each INC: verify flow up, trade behavior unchanged.

## Jin decision needed (pre-build)
(a) tier count + percentile boundaries; (b) per-tier poll periods (calibration,
not hardcode); (c) per-venue WS budgets (Capital 40 hard; OKX/Alpaca target);
(d) pure merit rank vs keep a soft crowding penalty; (e) confirm INC ordering
(cadence-first). flow_not_block · 9-stack untouched · in-loop GPT=0 · aggressive.
