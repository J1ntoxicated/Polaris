---
type: research
status: active
date_created: 2026-06-22
tags: [research-agenda, enhancements, vision]
---

# (E) 추가·확장 / Enhancements + Vision

Index → [[research_agenda_2026-06-22]]. Forward builds/vision (research-mesh, execution layer, AI conductor, layered regime, self-evolve). Format: KNOWN · Q · approach · **Pn** · status.

- **E1 Research-mesh ResearchSignal contract + auto guards (rail)** `P1` research-needed
  KNOWN: research_agent_mesh — single fuser seam, no new control path; ResearchSignal + compose_exit_evidence_candidate don't exist yet (grep 0). Q: minimal schema + guards feeding fuser as SIGNAL w/o 9-stack mult / tick-determinism break / defensive block? → ResearchSignal{label|score, conf[0,1], freshness, half_life, asset_keys, view_targets, evidence_span, failure_tags} + 3 guards (event dedup, claim-grounding conf→0, per-source calib), source clamp 0.75-1.25; TDD property tests; builder≠reviewer.
- **E2 Research-mesh MVP — P1 news-sentiment + P2 position-reviewer (both shadow)** `P1` research-needed
  KNOWN: Jin locked BOTH parallel shadow (no action/risk), edge decides promotion; P2 EXTEND-ONLY (FSM owns shrink). Q: which promotes first + at what conviction? → stand up both as logged Behavior-0 collectors, define acceptance KPI per stream, ramp conviction from 0; builder→adversarial review.
- **E3 Liquidity-graded T4 continuous scalar (precise-attack, not size-cut)** `P1` research-needed
  KNOWN: execution_layer_p3 D2 — single scalar clamp(0.75,1.5) UNIVERSE-MEDIAN-RELATIVE (1.0 auto-centers, no defensive bias); point is the 1.5 ceiling (lean harder into deep liq); E[t4]<1 long-run = flow_not_block alarm; uses EXISTING continuous_scalar slot. Q: liq_score composite, k, median-centering across 3 venues? → depth@2%+24h vol+fill_rate → existing T4 scalar, monitor E[t4] guardrail; gate behind M+D6; /debate, apply=Jin.
- **E4 Pre-entry liquidity gating (flexible, re-admit — never permanent block)** `P2` research-needed
  KNOWN: execution_layer_p3 D4 — min vol $5M, depth $20k@2%, re-admit on recovery; distinct from OKX 51155 compliance blocklist. Q: where in Layer-0 discovery, how re-admit vs 5/10min refresh churn? → flexible admission score re-eval'd each refresh, below-floor deprioritized in focus NOT removed (flow_not_block); pairs w/ D6.
- **E5 Venue-resting / market-able stops + ATR-R sizing (orphan leak)** `P1` research-needed
  KNOWN: audit #2 + execution_layer_p3 D2 A-package; resting/sl_trigger only in Capital adapter NOT OKX. Q: does OKX US demo support algo sl_trigger per symbol, market-able vs tranche resolution? → resting conditional stop where supported + bot-side market-able fallback, ATR-R sizing for venue-consistent R; bundle w/ tranche/TWAP; debated (P3-A), apply staged. **Same work as B1 — coordinate.**
- **E6 Tranche / TWAP exit + dynamic slippage (give-back on thin alts)** `P2` research-needed
  KNOWN: execution_layer_p3 P3-A; complements fade-exit (29517f3) + entry-anchored R ruler (8fc9aa1). Q: tranche/TWAP horizon minimizing give-back w/o residual exposure? → tranche/TWAP path in live-recalc exit, dynamic slippage keyed to exec grade; shadow vs single-clip; ship w/ E5 A-package.
- **E7 WebSocket real-time tick foundation (3-venue, REST=fallback)** `P1` research-needed
  KNOWN: p4_ws plan, 5-lens needs-changes (M1-M6: executor-offload flush, in-mem recv, OKX US WS smoke, Capital keepalive, teardown, staleness); converters exist uncalled; quote_ticks empty; Alpaca feed-death = the fix. Q: does OKX US expose public ticker WS, does single-writer hold under M1 flush at prod volume? → ws_common base + per-venue + QuoteTickWriter (1Hz batch, shared live_px); dashboard consumer first (behavior-0), then exit + G4 SHADOW; prereq OKX US handshake smoke.
- **E8 AI Conductor — batch/trigger orchestration (out-of-loop GPT)** `P2` in-D
  KNOWN: in-loop AI-FREE already landed (aafb635); conductor = surviving AI role = per-regime/per-N-min/per-session BATCH synthesis injecting deterministic thresholds, never per-signal; bot LLM=GPT. Q: exact cadence + SSOT for threshold injection w/o re-entering loop / touching 9-stack? → out-of-loop GPT jobs (regime confirm, selection/allocation, periodic calibration from MFE/MAE, anomaly); threshold-injection only; /debate cadence/SSOT/non-intrusion.
- **E9 Layered dynamic regime brain (L1 macro/L2 asset-class/L3 ticker)** `P1` in-D
  KNOWN: skeleton exists, brain missing (classify_regime stub → confidence pinned 0.5); S landed regime bar-close; layered weighted synthesis open. Q: L1/L2/L3 combine weights + confidence=axis-agreement, evidence-crisis stays behind 2-close while price-crisis immediate? → phased (fuse_evidence record → compute_real_regime_signal → weighted compose SIGNAL-only → candidate_source tag → dynamic confidence); asset-class mult clamp 0.75-1.25; consumers read label only.
- **E10 Alt-data → MarketView strategy features (edge substrate)** `P1` research-needed
  KNOWN: alt-data→strategies=0, MarketView fixed TA; p3_self_evolve REFRAME (Jin accepted): biggest near-term lever = features+fee+exit, NOT a generator; COT pctile landed (gate/regime). Q: which alt-data → strategy-visible MarketView w/o AI-free violation / sizing mult, do they widen edge (KILL-spike re-test)? → pipe altdata into MarketView as deterministic numeric features, re-run offline KILL-spike on expanded space BEFORE building any generator. **= D5, coordinate.**
- **E11 volume_burst fade-first (D4)** `P1` in-D
  KNOWN: D4 approved 4/4; buys spike tops; convert to fade (spike + failed follow-through + resistance → SELL), dual-mode continuation gated on acceptance + non-synthetic volume; bundle w/ A-package; prereq OKX synthetic-bar filter. Q: fade-first dual-mode vs fade-only, what N + resistance def avoids hidden classifier? → fade branch w/ tight invalidation, continuation gated; synthetic filter first; apply staged TDD+review. **= A3, coordinate.**
- **E12 Replay / backtest harness activation (edge-verification substrate)** `P2` research-needed
  KNOWN: replay_runs=0, OOS is_oos_spread hardcoded 0 gating nothing; Jin: price-skeleton edge verification = judge for ALL promotions. Q: minimal harness for honest OOS verdicts (CPCV/purge/embargo, real-fee)? → activate real-fee harness, wire walk-forward OOS gating, add CPCV/purge/embargo(=exit horizon)+corr-dedup. **Cross-cutting unblocker** for self-evolve KILL-spike, swap-pairs, exit-evolution, mesh acceptance — every shadow→promote stalls without it.
- **E13 Capital overshoot-fade tick path (D3)** `P2` in-D
  KNOWN: Capital WS zero size/trade → burst_rider+flow_pressure dead there; move tick engine to OKX, Capital = price-only fade. Q: OKX-first only, or OKX routing + Capital fade same change set? → point P5 to OKX depth, build Capital overshoot-fade on price-only/bar fields; phased; apply after reset. **= A2, coordinate.**
- **E14 OKX tradable-universe redesign (compliance = thin signal)** `P2` research-needed
  KNOWN: _NOW #4 open, no debate/plan; 51155 blocks 44 alts (legit), majors quiet; venue constraint = ROOT. Q: tune majors OR redesign universe/strategy mix? → intersect-map strategies×tradable symbols, decide majors-tuning vs redesign; upstream of E3 (no point sizing what doesn't fire); /debate. **= A4, coordinate.**
- **E15 Conviction-stack ladder + capital-rotation + swap shadow** `P3` in-D
  KNOWN: holding_organs debate resolved 11/11; ladder L1+0.5R/L2+1.0R (turtle, new price progress), rotation cap 4/h, swap pairs shadow-measure, tf-asymmetric swap ban; B1-B5 build-blockers; env-flag default-off. Q: do swap pairs show positive shadow edge, does ladder avoid churn-loop? → 3-wave (rotation → conviction+swap-shadow → swap-apply) w/ B1-B5 baked, event-count gate per wave; conviction=amplify on confirmed progress; promote on shadow edge.
- **E16 Self-evolve generator (config-mutation) — GATED behind edge proof** `P3` idea
  KNOWN: p3_self_evolve REFRAME "prove edge first"; KILL-spike decision gate; validation stack greenfield; 9-stack-safe (0 T4 slots). Q: does KILL-spike pass >0 on current fixed-feature space (likely ~0)? → build KILL-spike harness only (E12 seam + bounded variants + honest-N registry + OOS), run gate; build generator ONLY if gate passes AND alt-data features land (E10); defer RAG/bandit.
- **E17 Fee/churn control + live exit-recompute wiring** `P2` research-needed
  KNOWN: p3_self_evolve P0b near-term real lever; taker-fee floor vs small alpha + short holds; entry_admission regime-gate + anti-churn B built-but-off; live exit-recompute stub. Q: real turnover/fee-drag on clean ledger, which exit params most improve give-back when evolved? → turn on admission gate + anti-churn, measure turnover/fee-drag post-reset, wire exit-recompute stub→live, evolve exit params via replay; anti-churn = timing not size-cut.
- **E18 Strategy-discussion chat (operator second-brain)** `P3` idea
  KNOWN: Jin idea, build-deferred; GPT-only; prereq EXPLICITLY = after telemetry honesty (now met); decisions = vault proposals only. Q: form factor (dashboard panel vs Obsidian vs CLI), context/retrieval, write authority? → dashboard right-panel chat consuming /api/snapshot + vault retrieval, output = vault append + config CHANGE PROPOSAL (never touches deterministic loop); build after reset confirmed clean.
- **E19 Data-driven instrument display names (dashboard quality)** `P3` idea
  KNOWN: loop_state (l) — bot has no company names; straightforward BUILD, timing = reset restart. Q: none material. → universe.display_name column + migration, fetch metadata per venue at restart, thread through /api/snapshot, drop hardcoded SYM_NAME map; no penny-stock hardcode.
