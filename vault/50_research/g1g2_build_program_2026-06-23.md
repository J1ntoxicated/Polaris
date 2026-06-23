---
type: research
status: active
date_created: 2026-06-23
tags: [g1-g2, discovery, build-program, signal, arbitration, probe]
related: [[ADR-012-probe-engine-tuning-log|ADR-012]], [[ADR-013-entry-multiprobe-conviction|ADR-013]], [[research_agenda_2026-06-22]], [[_NOW]]
---

# G1/G2 Discovery Build Program (Jin: G1/G2 = foundation, full autonomy 2026-06-23)

## Critical reframe (grounding from 3 landed analyses)
Bot is **NOT bad-strategy starved — it's UPSTREAM**: (1) OKX 51155 compliance thins the tradable∩signal universe (vol-alts removed, majors calm → rarely trigger); (2) **alt-data reaches strategies = ZERO** (MarketView is fixed TA only); (3) FX bar strategies are **plumbing-starved** (a routing skip vacates non-forex Capital to the tick engine; session fields under-populated). Architecture sound (audit). → highest leverage = **discovery substrate (universe + alt-data)**, not strategy rewrites.

## Pillars (Jin)
1. Discovery logic · 2. Data integrity (multi-source/multi-res/fresh + on-signal per-ticker pull) · 3. Multi-signal arbitration (**BLEND = learned expectancy × current-evidence conviction**) · 4. Entry multi-probe ensemble ([[ADR-013-entry-multiprobe-conviction|ADR-013]]).

## Build order (TIER A, by leverage) — design→build(TDD)→adversarial review→deploy
- **W1 strategy revival** (BUILDING `wohavhwq9`): finish index/commodity routing carve-out (`_production_tick.py:406`) + GOLD name fix + session-open anchor + **persist L1 emitted signals** (signals table empty). Additive/flow_not_block. Calibration→/debate.
- **W2 alt-data → MarketView** (A-2, substrate — build before any generator): pipe AltDataCache numerics (funding/OI/COT pctile/VIX/F&G/HY) into MarketView as additive no-op-when-stale fields. + **on-signal per-ticker fresh data pull** (multi-res bars, news, macro — Yahoo etc.). Jin's data-integrity pillar.
- **W3 OKX majors-native** (A-1, the #1 root): emission-rate telemetry over tradable universe + majors-native generators (BTC/ETH range-fade, funding/basis).
- **W4 entry probe scaffold** (A-5 = ADR-013 Slice 1, observe-only byte-identical): reuse ADR-012 probe framework; conviction logged, threads nothing.
- **W5 arbitration seam** (A-6, shadow-first): blend = posterior expectancy × probe conviction → ranked allocation; shadow-log vs first-come before live.
- **W6 register 3 tick strategies** (A-3) into posterior/cell keyspace (the dominant traders are unmeasured).

## DISCARD (Jin "쓸데없는 로직 과감히 버려") — cut on evidence
- Dead in-loop-LLM reasoning: G7 HOLD/WIDEN/TIGHTEN scaffold (W3 cutover = GPT 0, deterministic Q9 only) + ADR-004 in-loop-gate design (superseded [[ADR-011-ai-free-cutover|ADR-011]]) — conceptual + dead-code cut.
- G6 per-tick ADJUST_EXIT churn / dual stop-writers (B6) — collapse to FSM trail, make G6 ADJUST observability-only (trace first).
- KEEP (NOT discard): the 3 FX strategies (starved not dead — fix feeder), volume_burst (polarity-flipped fade-first).

## /debate-flagged (trading-param) — Jin decides
OKX universe/majors thresholds · arbitration blend weights · probe lean→knob weights + alt-data-act policy · ADX dead-zone overlap (breakout 20→15 / fade 20→25) · session window widen · COT/flow_pressure thresholds.
