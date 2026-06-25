---
type: research
status: debate-converged-not-built
date_created: 2026-06-26
tags: [debate, layer-0, candidate-sweep, activation, capital, flow-not-block]
---

# Capital Activation Redesign — Per-Symbol Movement-First (debate-converged)

**Agenda**: Capital under-trade (emit 0.13% vs OKX 0.73%). Root = `candidate_score = activation×(0.5+0.5×edge)` can't see per-symbol live activity. `tick_inflow` is per-VENUE (every Capital symbol gets identical micro pulse); vol/range from Yahoo DAILY bars → wide-daily agriculturals (COFFEE/CORN/LEANHOGS) outrank tight-daily but intraday-active majors (EURUSD/USDJPY/US100/US500). FX-major edge≈0 (no COT) halves their candidate. Source: `_candidate_sweep.py` (`activation_score`/`edge_score`/`candidate_score`).

**Method**: GPT (codex gpt-5.5) + Gemini (2.5-pro) 2 rounds, adversarial cross-rebuttal. Gemini OAuth client deprecated → ran via `.env GEMINI_API_KEY`; codex hooks broke `exec` → ran via clean `CODEX_HOME`.

## Convergence (both signed identical final spec)

`candidate_score = cold_start_mult × clamp01(0.75×activation + 0.25×edge)`
(no extra ≤1 multipliers beyond the existing cold-start term — 9-stack ban honored)

**activation_score** = additive clamped blend over a NEW bounded per-symbol rolling accumulator (keyed `instrument_id` in quote writer: `ticks_600s`, mid ring / `mid_120s_ago`, `mid_high_600s`, `mid_low_600s`, `last_mid`, `last_ts`):
- `ACTIVATION_TICK_RATE_WEIGHT=0.30` — per-symbol `ticks_600s` floor→hot normalized
- `ACTIVATION_SHORT_MOVE_WEIGHT=0.25` — `|mid - mid_120s_ago|/mid` bps vs HOT_BPS
- `ACTIVATION_INTRADAY_RANGE_WEIGHT=0.15` — `(hi-lo)/mid` 600s bps vs RANGE_HOT_BPS
- `ACTIVATION_SPREAD_WEIGHT=0.20` — `spread_tradeability_score` (see D3)
- `ACTIVATION_DAILY_VOL_WEIGHT=0.10` — existing Yahoo realized/expected vol ratio
→ live per-symbol motion = 0.70 influence; spread 0.20; daily vol demoted 0.45→0.10.

**edge_score** = `clamp01(0.35×sentiment + 0.65×tech)` (tech-dominant for FX/index where COT/sentiment is structurally sparse). Missing sentiment/COT/regime/tech → `EDGE_MISSING_COMPONENT_DEFAULT=0.50` (NEUTRAL, never 0 — an active major is no longer dragged below neutral by empty edge).

**spread (D3)** = ADDITIVE term inside activation, never a multiplier, never a filter:
`spread_tradeability_score = clamp01((SPREAD_WIDE_BPS - spread_bps)/(SPREAD_WIDE_BPS - SPREAD_TIGHT_BPS))`
from per-symbol `quote_ticks.spread_bps`. Tight→1, wide→0. Rank-positive only.

Env constants (all overridable, /debate-calibrated, no magic-in-place):
`ACTIVATION_TICK_RATE_FLOOR_600S` · `_HOT_600S` · `ACTIVATION_SHORT_MOVE_HOT_BPS` · `ACTIVATION_RANGE_HOT_BPS` · `ACTIVATION_SPREAD_TIGHT_BPS` · `_WIDE_BPS` · `ACTIVATION_DAILY_VOL_HOT_RATIO` · `CANDIDATE_ACTIVATION_WEIGHT=0.75` · `CANDIDATE_EDGE_WEIGHT=0.25` · `COLD_START_SCORE_MULTIPLIER=0.50`.

## Divergences resolved (Gemini conceded all three to GPT in R2)
- **D1 spread placement** — Gemini R1 proposed a 3rd `tradeability_score` MULTIPLIER on the whole candidate. R2: CONCEDED — that is a stacked ≤1 multiplier = 9-stack-ban violation. Final = ADDITIVE inside activation.
- **D2 edge** — Gemini R1 kept sentiment-dominant (0.55/0.45); CONCEDED to tech-dominant 0.35/0.65 (Capital sentiment sparse).
- **D3 source** — both: NEW per-symbol rolling accumulator worth the build vs sweep-to-sweep mid deltas (which miss between-sweep bursts).

## Verdict / build path
Mandate-clean: flow_not_block (rank reorder, zero membership/size cut), cold-start = 0.5× penalty not exclusion, additive+clamp+linear (no learned weights, no 9-stack), all knobs named. **Build order**: (1) per-symbol rolling accumulator in quote writer; (2) rewrite `activation_score` 5-component additive; (3) `edge_score` missing→0.5 + tech-dominant; (4) calibrate HOT/FLOOR/SPREAD env constants on live Capital. Builder ≠ reviewer (fresh Claude review post-build).
