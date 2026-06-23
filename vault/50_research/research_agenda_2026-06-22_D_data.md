---
type: research
status: active
date_created: 2026-06-22
tags: [research-agenda, data, tuning]
---

# (D) 데이터·튜닝 / Data + Tuning

Index → [[research_agenda_2026-06-22]]. Feeds + learners. Live evidence (2026-06-22): OKX 1m 73% flat / 15m 40% / 1H 26%; Alpaca RESTORED (0.3h); 4 alt collectors fresh; Coinglass/MyFxBook keys EMPTY; learners sparse (max n_eff=5 < 20 commit). Format: KNOWN · Q · approach · **Pn** · status.

- **D1 OKX synthetic/flat-bar pollution (73% of 1m live)** `P0` research-needed
  KNOWN: audit loss-cause #1; loop_state lists "OKX flat-bar filter" under S as DONE, but live DB still 73% flat + no filter code found in ingest (only div-by-zero guards) → DONE claim NEEDS VERIFY. Q: did the filter ship, how to handle 73% degenerate bars so vol_z/ATR/burst aren't computed on them? → verify; if absent, flag synthetic at ingest (high==low|vol==0), exclude from baseline windows, gate burst/tick on min non-synthetic fraction, surface data-quality score. Signal-eligibility hygiene, not throttle.
- **D2 Alpaca restore durability + equity-halt auto-clear** `P1` in-D
  KNOWN: feed restored (0.3h); alpaca_health.py 36h staleness + entry-halt shipped; intraday 1m present. Q: is 36h window right or does intraday need shorter window; auto-clear correct? → confirm halt auto-clears on fresh MAX(ts); research intraday vs 1D resolution; add per-venue feed-health strip + watchdog staleness alert (catch re-death in minutes).
- **D3 Learner adaptation correctness on clean data** `P0` research-needed
  KNOWN: all learner_state sparse (n_eff≤5 < 20); D2 expectancy-aware promotion (withhold +0.1 from high-WR/neg-exp) wired, untested live; M made R honest. Q: does the L5 network adapt correctly + does expectancy gate fire as designed once buckets cross 20? → learner-adaptation telemetry panel (n_eff growth, WR, expectancy, committed, source); shadow WR-only vs expectancy-weighted on real closes; verify ClosedTrade.pnl_r uses risk_unit.py SSOT.
- **D4 D2 venue-native session (asia hardcode removal) — apply to prod** `P1` in-D
  KNOWN: expectancy half built+wired; SESSION-LABELING half NOT in prod (get_mult still defaults 'asia', no classify_session found); Jin lock-in #3. Q: when/how applied so session_mult stops being cosmetic? → build pure venue→session classifier (OKX continuous / Capital active hours / Alpaca RTH-offhours-gap), stamp on close, apply next restart; TDD + builder≠reviewer; tunes preference/exit, never size.
- **D5 Alt-data reaches strategies ZERO — MarketView bottleneck** `P1` research-needed
  KNOWN: MarketView has ~20 pure-TA fields, ZERO alt-data; fuser only feeds regime (gate side); p3_self_evolve: "missing edge can't be manufactured by a generator." Q: promote alt-data (funding/OI/COT pctile/VIX/HY/F&G) into MarketView as strategy-visible SIGNAL fields? → pipe AltDataCache outputs into MarketView (deterministic numeric), additive/no-op when stale; highest-leverage tuning item. SIGNAL not defensive.
- **D6 Execution-quality feedback loop → per-symbol liquidity grade** `P1` in-D
  KNOWN: execution_layer_p3 D4 names it missing; trap-liquidity (OKX 5s gaps→non-fill) invisible; slippage/fill_rate primitives exist but no closed loop. Q: capture fill_rate/slippage_bps/stop_trigger_to_fill_ms → per-symbol grade feeding T4 liquidity scalar + flexible gate? → build fills-derived execution-quality table, feed T4 scalar (clamp 0.75-1.5 median-relative, bigger on deep liq) + re-admitting gate. Depends on M + B1.
- **D7 Per-source alt-data dynamic confidence + consumer staleness telemetry** `P2` research-needed
  KNOWN: research_agent_mesh D3 — dynamic per-source calibration + claim-grounding; fuser uses static weights clamp [0.75,1.25]; Coinglass/MyFxBook keys EMPTY contributing nothing silently. Q: per-source outcome-calibrated weight + consumer staleness/coverage panel? → freshness/coverage panel, calibrate source_weights vs realized regime-hint accuracy, decide fund-or-retire dead sources; weight-adjust never block.
- **D8 Alt-data regime-hint edge attribution (is the evidence sound?)** `P2` research-needed
  KNOWN: fuser COT thresholds (_COT_BULL_STRONG=0.85) /debate-flagged momentum-vs-contrarian; ported auto_invasion F&G/VIX cuts; no edge-attribution. Q: do hints actually improve outcomes vs price-only regime, are thresholds calibrated for this universe? → behavior-0 counterfactual (hint vs price-only regime), attribute outcomes hint-on/off, validate COT/F&G/VIX cut points vs realized edge. Converts soundness from asserted to measured.
- **D9 Bar-resolution policy per asset class vs horizon** `P2` idea
  KNOWN: OKX/Capital on 1m (flat-bar peak), Alpaca 1D, alt-data FRED-daily/COT-weekly; no documented mapping; flat drops 73%→40%→26% across 1m/15m/1H. Q: is the implicit resolution choice coherent, move OKX signal off 1m onto 5m/15m? → document mapping, research resolution shift vs aggression, define per-stream resolution-vs-horizon-vs-freshness policy doc.
- **D10 cell_matrix calibration on clean data (N=4 post-reset)** `P3` idea
  KNOWN: cell_matrix_p0=4 rows; quartile/decay/shrinkage/alignment params unexercised on clean data. Q: meaningful routing mults at low N? → monitor maturation (n_eff/score/quartile/routing_mult), verify CELL_MIN falls back to neutral while sparse (no 1-2-trade noise), alignment is re-rank not new dampen. Low priority until cells mature.
