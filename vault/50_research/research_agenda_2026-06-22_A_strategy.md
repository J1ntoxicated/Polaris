---
type: research
status: active
date_created: 2026-06-22
tags: [research-agenda, strategy]
---

# (A) 전략 타당성 / Strategy Validity

Index → [[research_agenda_2026-06-22]]. All **14** strategies (11 bar registry + 3 tick: burst_rider/flow_pressure/micro_reversion). Format: KNOWN · Q · approach · **Pn** · status.

- **A1 Per-strategy real edge vs bleed (all 14, honest R)** `P0` research-needed
  KNOWN: arch sound, PF 0.39 real-bad, $-ledger okx −$646/alpaca −$1881/cap +$431; NIG posterior infra built, display-only never gates. Q: which of 14 own positive cost-adj expectancy vs structural bleed? → read learner_posterior P(exp>0) per (strategy×cell×regime) after n≥~20/cell, rank by mu; retire only on clear negative posterior w/ adequate n (NOT sample-count).
- **A2 Tick strategies (registry-orphan, DOMINANT post-reset)** `P0` research-needed
  KNOWN: micro_reversion=5/volume_burst=1 closed post-reset; outside STRATEGY_REGISTRY/ADR-008; D3 moves engine to OKX depth. Q: edge on OKX depth, is micro_reversion fade sound vs noise on 5s? → register in posterior keyspace, A/B Capital(dead-feed)→OKX move, validate overshoot_z fade vs realized reversion on OKX flow.
- **A3 volume_burst fade-first (D4)** `P0` in-D
  KNOWN: D4 LOCKED fade-first dual-mode, precise-invalidation exit not size-cut. Q: does fade branch have edge or does spike-top just move to short side; is prior_high(20-bar)+vol_z≥2.5 right? → measure fade vs continuation expectancy separately (mode tag emitted), gate on OKX synthetic-bar filter active, condition on regime (fades pay in chop).
- **A4 OKX tradable-majors ∩ signal-emission (thin-trade root)** `P0` research-needed
  KNOWN: _NOW #4 open — 68 OKX symbols US-blocklisted (51155, legit not throttle), majors calm rarely trigger. Q: re-tune OKX strategies for majors OR redesign universe/strategy pairing? → quantify emission rate on tradable universe; if ~0, recalibrate to major vol OR add majors-native (funding/basis, BTC/ETH range-fade). flow_not_block: expand opportunity + validate, never blind-relax.
- **A5 Per-strategy regime-fit: when should each fire** `P1` research-needed
  KNOWN: all 14 fire regardless of regime; regime only nudges clipped size + expectancy learner; no selection-time gate. Q: move regime-fit upstream to signal-side routing or stay sizing nudge? → mine (strategy×regime) posterior vs a-priori trend/range map; down-route weak via regime_mult/expectancy FIRST (flow_not_block), hard gate only if nudge insufficient + flow preserved.
- **A6 Entry-price basis: bar-close-as-entry vs venue fill** `P1` research-needed
  KNOWN: open path records entry=signal bar close (last_price); close-side R reads actual fill; OKX 1m 73% synthetic. Q: how large is bar-close-vs-fill slippage per venue, flatter/punish which strategies (breakouts buy strength=adverse)? → measure realized entry slippage into cost overlay; if breakouts adverse, candidate = maker/post-only entry.
- **A7 fx_range_fade profit-target heuristic (1R≈2σ≈BB dist)** `P1` in-D
  KNOWN: _NOW #1 fade=1.0; review flagged 1R≈2σ coarse; code self-flags overshoot when intrabar ATR≫close-stdev. Q: is 1.0 right or derive per-position from bb_extreme→middle in R? → measure realized band distance in R per FX pair, derive target=(extreme−middle)/atr_R. EXPECTANCY harvest, not throttle.
- **A8 Equity (Alpaca) strategies: feed health + long-only edge** `P1` research-needed
  KNOWN: Alpaca worst $-bleeder −$1881, feed was DEAD (4-10d stale); S added recency guard + equity-halt+restore. Q: real edge once feed restored, or pure stale-data execution? penny-stock universe corrupt? → re-measure ONLY after S restore confirmed live (don't blame logic for stale-feed loss); add liquidity/price floor; validate gap_go on clean RTH.
- **A9 Short-side branch edge (newly symmetric)** `P2` research-needed
  KNOWN: fx_breakout/xau/session/fx_range_fade/volume_burst now emit shorts; equity long-only; loss_profit_asymmetry mandate. Q: do shorts pay symmetrically or is there CFD-financing/borrow/gap asymmetry? → tag long vs short expectancy separately; symmetric ENTRY ≠ symmetric edge — validate before trusting.
- **A10 alt-data as per-strategy entry-quality SIGNAL** `P2` in-D
  KNOWN: research-mesh built (ResearchSignal, P1 news + P2 reviewer, shadow-first, Jin: both parallel shadow); COT per-contract percentile built; no strategy consumes alt-data at signal yet. Q: does an alt-data confirmation (signal-only, never block) improve entry precision? → run P1/P2 shadow, promote proven-edge collector, wire as entry-quality strength mult (SIGNAL).
- **A11 Add/retire/reweight strategy SET (coverage gaps)** `P3` idea
  KNOWN: profit_structure_backlog — majors-native crypto, meta-labeling (collect-first), vol-targeting; aggressive = expand/reallocate not throttle. Q: is set matched to tradable opportunity given OKX majors-only + dead Capital tick? → defer add/retire until posterior n adequate; prefer REWEIGHT via expectancy learner over removal; any retire cites clear negative posterior, never sample-count.
