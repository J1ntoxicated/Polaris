---
type: research
status: recorded
date_created: 2026-06-25
tags: [research, backfilled-frontmatter]
---

# STEP② Candidate Sweep — dynamic active-focus over the static ground

2026-06-25 · DEMO/PAPER · OBSERVATION/focus-selection only (flow_not_block, gates nothing) · built on STEP① `24a6e6c`

## Problem (Jin "맨날 들어오는 애들만")
STEP① (`_static_ground.py`, `24a6e6c`) gave all ~1650 active tickers a static ground
(Yahoo 1D/1H/15m bars + per-ticker fused sentiment/event via `read_ticker_ground`). But
the LIVE focus is still **fixed merit-rank**: `refresh_focus_watchlist` →
`compute_dynamic_focus` ranks all active by `score_focus_candidates` (grouped-z on
vol/sig/atr/depth/cell/opp), and consumers read the top-`FOCUS_CYCLE_TARGET`=120 by
`focus_rank ASC`. Liquidity-z dominates → the same ~53 high-vol names sit at the rank head
forever → only they get WS/tick/signal/trade. `tick_inflow` shows 51k ticks/10min (market
alive) but the focus symbols are calm. **The static ground is never used to pick today's
movers.**

## Design (Jin vision + /debate GPT+Gemini, Jin locked cap=200)

### Seam (no schema/consumer break)
STEP② replaces the **score + selection** inside the focus producer, keeping the EXACT
output contract so all 4 `get_focus_targets` consumers (bar ingest `_production_tick.py:378`,
tick engine `_production_tick_engine.py:484/493`, WS subscribe `_production_ws.py:62`,
held-union `production_paper_loop.py:757`) are untouched:
- NEW `polaris/scripts/_candidate_sweep.py` — pure scoring + bucket selection functions.
- `refresh_focus_watchlist` (`_production_layers.py:449`) gains a sweep path: when the
  candidate sweep is enabled (default ON), it builds `FocusSelection` rows from the sweep
  instead of `compute_dynamic_focus`, then the SAME `persist_focus`. `focus_rank 1..200` =
  the dynamic active set; `tier` from rank-percentile (unchanged cadence contract);
  `trade_eligible` still owned by the entrance judge (WATCH/TRADE decouple preserved).
- The legacy `compute_dynamic_focus` stays callable (tests / explicit callers); the sweep
  is the production producer.

### Two-stage score (look-ahead-safe, anti-overfit)
`candidate_score = ActivationScore × (0.5 + 0.5 × EdgeScore)` — a row that will not move
today scores ~0 (gate form), a mover is amplified by its edge.

- **ActivationScore ∈ [0,1]** ("will it move today", REQUIRED gate). Built ONLY from
  CONFIRMED stored bars (close ≤ sweep_ts; look-ahead guard) + `tick_inflow`:
  - realized/expected vol ratio (recent 15m/1H true-range vs the ticker's own ~1D ATR
    baseline) — clamp;
  - recent range expansion (last 15m–1H range vs trailing median) — clamp;
  - micro activity: `tick_inflow.ticks_600s` / `max_flow_size_600s` for the venue,
    normalized (venue-level — per-symbol tick counts are not stored; venue micro-pulse
    is the available signal), clamp.
  - ActivationScore = clamped linear blend; **0 when no movement** (gate).
- **EdgeScore ∈ [0,1]** ("edge WHEN it moves", direction). From `read_ticker_ground`
  evidence + multi-timeframe technical alignment of the CONFIRMED bars:
  - sentiment direction × strength from `ground` (the fused `scores`/`label`);
  - multi-TF technical setup alignment (15m vs 1H vs 1D trend agreement) on confirmed bars.
  - EdgeScore = clamped linear blend; neutral (0.5-ish via the `0.5 + 0.5×` envelope) when
    no edge, so a strong mover with no edge still scores on activation alone.

🚨 **Look-ahead**: every bar consumed must have its bar-close time ≤ sweep timestamp
(`ticker_ground.updated_ts` / explicit `now_ts`). The sweep reads stored bars; the guard
filters any bar whose period has not closed. **Anti-overfit**: component functions are
FIXED (no learned weights), linear + clamp only, no per-ticker tuning.

### Cap 200 bucket decomposition (FOCUS_CYCLE_TARGET 120→200)
`focus_rank 1..200` is filled by buckets (env-tunable counts, /debate calibration targets):
- **anchor (~40)** — top merit-rank (the existing `score_focus_candidates` head): the base
  engine keeps its proven liquid names.
- **dynamic (~140)** — top candidate_score (the sweep's main thrust): today's movers.
- **event_hot (~10)** — top by sentiment/event spike (`has_event` + evidence strength).
- **exploration (~10)** — random from the ground (anti-blindness + ground freshness).
- De-dup across buckets (a name in two buckets is seated once, highest-priority bucket).
- **Open-position symbols force-seated** (already done by `get_focus_targets` held-union;
  the sweep ADDITIONALLY guarantees they are in the active 200 for exit precision).
- **venue allocation**: OKX (24/7, steady share) / Capital (session share) / Alpaca (US
  session, dynamic↑ near US open) — venue weights modulated by session state
  (`capital_seconds_to_close`, equity RTH). **cold-start** (ground not yet populated /
  thin bars) = vol-penalty 0.5× (NOT excluded — flow_not_block).

### Rotation (dual cadence + hysteresis)
- **micro_sweep** (2–3 min): re-score activation-spiking candidates (cheap, activation only).
- **macro_sweep** (15–30 min): full re-rank of all ~1650 (the existing L0 cadence).
- market-tempo modulation: `tick_inflow`-driven dynamic period (hotter market → faster).
- **hysteresis**: enter/exit deltas on candidate_score (a name must beat the current
  occupant by `enter_delta` to displace, and only drops below `exit_delta` — anti-flicker).
- **open positions pinned** to focus (exit-precision data) regardless of score.
- event-trigger async sweep (sentiment/event spike → immediate re-score).

### fast-track
A scan-detected outlier (vol/range spike, currently outside focus) is promoted into the
active 200 immediately (anti-blindness; NOT a block — it WIDENS what is watched).

## flow_not_block / 9-stack / aggressive coherence
- Candidate selection = WHERE to look live (scan covers ALL 1650 + fast-track catches the
  rest). It gates NO entry/size/exit/halt. Stop-loss rails untouched.
- 9-stack untouched: the sweep produces `focus_rank`/`tier`, never a sizing multiplier. No
  ≤1 mult stack.
- aggressive: cap 200 (was 120) WIDENS the live set; cold-start = penalty not exclusion;
  exploration bucket fights blindness. Rejection keywords: 0.
- runtime-LLM = OpenAI only (sweep is deterministic Python; any ground resolution already
  gated by `ai_free_mode` in STEP①). Anthropic absent.

## Verify (TDD + live + adversarial)
two-stage score · activation gate (0-movement→0) · bucket allocation · venue allocation ·
hysteresis · open-position pin · fast-track promotion · look-ahead guard · cold-start
penalty · 1650-scan perf · mypy --strict + ruff clean · live ticker_ground sweep sample
(active vs calm) · fresh adversarial review (flow_not_block/aggressive/9-stack + no
scan-miss + look-ahead). Deploy STEP①+② together. Knobs (bucket counts, sweep periods,
hysteresis deltas, venue weights) = env-tunable /debate calibration targets, never magic.
