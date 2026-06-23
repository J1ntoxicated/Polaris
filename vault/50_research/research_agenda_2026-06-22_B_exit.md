---
type: research
status: active
date_created: 2026-06-22
tags: [research-agenda, exit, monitor]
---

# (B) 모니터·엑싯 타당성 / Monitor + Exit

Index → [[research_agenda_2026-06-22]]. G6 monitor + G7 adaptive-exit + precise-exit FSM + stops. Loss-defense = **precise exit**, never throttle. Format: KNOWN · Q · approach · **Pn** · status.

- **B1 OKX venue-resting conditional stop (close orphan hole)** `P0` research-needed
  KNOWN: audit §손실#2 + execution_layer_p3 §A name venue-resting stop ABSENT as PRIMARY honest-$ bleeder (−1R→−34..−100R orphans). Code: okx/adapter.py has only place_market_order; all stops software-polled market closes; S auto-resume landed but resting SL did NOT. Q: does order-algo/slTriggerPx eliminate gap-through orphans, what ordType/trigger-type does us.okx demo expose, who's authoritative vs software trail? → place/cancel-replace resting stop on fill (venue=backstop @−1R, software trail=primary); map per-symbol order types; instrument trigger-to-fill ms + slippage. Loss-precision rail, never block.
- **B2 Empirical FSM threshold calibration on clean data** `P1` research-needed
  KNOWN: every exit param (TOUCH/PROTECT/HARVEST 0.5/1.0/2.0, trail 2.0→1.0, timeout 900s) is a CONSERVATIVE auto_invasion default, self-flagged "pending /debate"; never fit to Polaris MFE/MAE; R now honest + reset clean. Q: are thresholds optimal or leaving expectancy on the table (trail too wide round-trips winners)? → consume persisted mfe_r/mae_r, build MFE→realized give-back curve per strategy, grid-search trail+R-thresholds; config-only, /debate-flagged; optimize for letting winners run + cutting dead losers, never dampen.
- **B3 Per-strategy / per-edge-shape exit width** `P1` in-D
  KNOWN: execution_layer_p3 D2 — burst tight 1.5-2.5, micro_reversion wide 3-4; only fx_range_fade sets profit_target_r, all others share 2-ATR trail + 900s. Q: should exit width be edge-shaped (trend trail vs reversion target vs fade invalidation)? → extend StrategyMetadata with per-strategy exit profile by edge type, fit from each strategy's own MFE/MAE; pairs w/ D4 fade (needs tight invalidation). Close-tuning only.
- **B4 FADE_TARGET_R=1.0 validity vs realized fade reversions** `P2` research-needed
  KNOWN: _NOW #1 fade=1.0, reviewer nit coarse, code documents overshoot caveat. Q: does fade bank ~+1R at middle or over/under-shoot? → measure realized excursion per symbol/regime, replace static 1.0 w/ fitted or band-middle-anchored dynamic target; /debate-flagged.
- **B5 Loser-timeout calibration (900s base, 2× ext, 2-bar floor)** `P2` research-needed
  KNOWN: timeout=max(900s, MIN_BARS×bar_sec), ×2 if touched profit; conservative default, never fit; no timeout-close R study. Q: is 900s right, does it cut real losers or kill winners-in-waiting / let BEP-oscillators bleed? → measure R-dist of timeout-closed vs >900s-recovered; tune by timeframe class. The "cut dead losers" half of loss-defense.
- **B6 Why mid-trade ADJUST_EXIT fires — justified or churn** `P2` research-needed
  KNOWN: G6 emits ADJUST_EXIT every tick where pnl_r>0.7R → G7 widen (stop−1ATR); SEPARATE from exit_engine._trailing_stop which ratchets the same stop_price column = two writers. Q: meaningful winner-extension or redundant churn; do the two stop authorities conflict? → trace how often G7 widen moves the stop vs no-op + vs FSM trail; if redundant, collapse to FSM trail, make G6 ADJUST_EXIT observability-only.
- **B7 G7 reasoned-exit replacement under AI-free** `P2` research-needed
  KNOWN: W3 (aafb635) made G7 GPT=0 → deterministic Q9 widen-only; the HOLD/WIDEN/TIGHTEN/EXIT_NOW reasoning is dead in-loop; S fixed regime bar-close so regime input reliable. Q: does FSM alone satisfy "reasoned exit" or is a deterministic regime/momentum early-exit needed (stalled winner + adverse regime flip)? → mine retired GPT EXIT_NOW + gate_shadow rows for missed early-exits, encode as deterministic FSM branch, AI-free + aggressive (no premature defensive cut).
- **B8 Hold-time distribution as exit-quality diagnostic** `P3` research-needed
  KNOWN: held_seconds computed+logged but never aggregated; loser_timeout scales by bar_seconds. Q: does hold-time match each strategy's design timeframe (1m burst not holding 1H)? → build per-strategy hold-time×R surface, flag timeframe-inconsistent modes; feeds B3/B5.
- **B9 Stop-fill execution-quality KPIs (instrumentation)** `P3` idea
  KNOWN: execution_layer_p3 D2 names fill_rate / slippage_bps / stop_trigger_to_fill_ms as proof; none instrumented; close_specific logs pnl not latency/slippage. Q: is software-stop→market-close leaking via slippage/latency? → instrument the 3 KPIs in close_specific, surface on dashboard; empirical proof-or-refute for B1, permanent exit-health monitor. Read-only.
