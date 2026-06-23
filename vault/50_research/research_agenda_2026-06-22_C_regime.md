---
type: research
status: active
date_created: 2026-06-22
tags: [research-agenda, regime, selection]
---

# (C) 레짐 + 전략 선택 / Regime + Selection

Index → [[research_agenda_2026-06-22]]. Regime engine (L6) + cell matrix (L4) + regime-driven selection/sizing. Format: KNOWN · Q · approach · **Pn** · status.

- **C1 Strategy-id mismatch no-ops regime alignment for 4 strategies** `P0` research-needed
  KNOWN: score.py _TREND/_COUNTER_TREND frozensets EXCLUDE equity_tsmom/equity_rsi_bb/equity_gap_go/fx_range_fade → always REGIME_ALIGN_NEUTRAL(1.0); Alpaca was worst $-bleeder. Q: is regime-fit silently dead for the whole equity track + fx_range_fade? → audit registry ids vs frozensets, map each of 11 to family, replace hand-coded sets with StrategyMetadata.edge_type SSOT (no drift); TDD asserts every id resolves. Surgical, no behavior change for covered ids.
- **C2 Two opposing selection mechanisms (tilt vs hard-select) unreconciled** `P1` in-D
  KNOWN: bar pipeline ALL fire + regime tilts cell-EV (1.25/1.0/0.8); tick engine regime_gate HARD-SELECTS; no doc decides which is right. Q: should bar pipeline adopt regime-conditioned selection, or is EV-tilt enough? → frame as selection-by-EV not block (redistribute misaligned notional → aligned = flow_not_block); MEASURE whether misaligned cells land bottom-quartile once warm; /debate. Compare aligned vs misaligned expectancy at n_eff≥20.
- **C3 Per-(strategy×regime) cell EV map is hand-coded, never validated** `P1` research-needed
  KNOWN: alignment asserts trend wins in trend, rsi_bb in chop — a PRIOR, never checked vs realized avg_pnl_r on THIS restricted universe. Q: does each strategy actually earn in its "aligned" regime; are there structurally-negative cells? → once n_eff≥20: build (strategy×regime×venue) realized-EV heatmap from cell_matrix_p0 (honest R); where realized disagrees w/ prior, the table is wrong; correct frozensets/edge_type OR trust quartile router override. Quantify warmup time.
- **C4 Regime LABEL accuracy + flip-cadence sanity (no live metric)** `P0` research-needed
  KNOWN: 1233/24h flip-flop fixed structurally (bar_id dedup, 2-close confirm); classify_regime still a STUB; no live flip-rate or label-accuracy metric. Q: is new flip cadence sane (handful/day not 1233), does label predict forward move? → instrument flips/day per (venue,group), alert if >N/day; forward-return confusion check (bull>chop>bear ordering). Read-only, SIGNAL-only; acceptance gate before trusting regime-conditioned EV.
- **C5 confidence/hysteresis calibration — computed, consumed by nobody actionable** `P2` in-D
  KNOWN: _compose_confidence persists to regime_state but only G3/G7 read-only; 2-close confirm fixed for all vol. Q: worth computing if nothing acts; should low-confidence need more confirm closes / route to tighter exit? → (a) research confirm-closes scaling w/ confidence (keep crisis fast-path); (b) route confidence into G7 EXIT precision (low-conf → tighter stop, not entry suppress); /debate-class; validate vs C4 first.
- **C6 D1 crisis-adaptive built, apply-after-reset pending** `P1` done
  KNOWN: D1 (per-class window-vol adaptive crisis cap + equity floor 1.0→2.5%, crypto frozen) BUILT+tested, wired into compute_real_regime_signal; debate left rollout-shape choice (adaptive-now vs shadow-vs-floor one cycle). Q: which, and does the running post-reset bot thread real asset_class (not default 'crypto') into compute_and_flip_regime? → verify asset_class reaches equity/FX calls; implement shadow-log of both decisions per bar; measure equity crisis-bucket rate (was 81% mis-bucket) drops.
- **C7 Per-(venue×group) regime independence — right SSOT granularity?** `P3` idea
  KNOWN: regime_state PK=(venue, group_id); corrected from symbol; cross-asset correlation_group unpopulated. Q: should same-underlying-across-venues SHARE a macro regime + venue-local microstructure modifier? → research 2-tier (shared macro per group from dominant-liquidity venue + venue-local modifier); tie to correlation_group + research-mesh per-asset alpha. Low priority until single-venue accuracy proven (C4-gated).
- **C8 Cell quartile router warmup on cold start (seed priors?)** `P2` research-needed
  KNOWN: router activates at ≥20 eligible cells w/ n_eff≥5, else 1.0 neutral; post-reset matrix empty; strategy_regime_prior has NO seeding path → cold-starts uninformative. Q: how long is warmup at thin OKX cadence; is hand-coded alignment the only regime signal meanwhile; seed priors to shorten? → estimate warmup vs trade cadence; if weeks, seed strategy_regime_prior/parent3 from alignment direction OR offline backtest so shrinkage-blend has non-zero parent; priors tilt routing score, never block.
