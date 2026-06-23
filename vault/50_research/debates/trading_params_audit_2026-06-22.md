---
type: research
status: active
date: 2026-06-22
tags: [debate, regime, session, tick-engine, volume-burst]
---

# Trading Params Audit Debate - 2026-06-22

Grounding: the 2026-06-22 system design audit says the 8-layer architecture is sound and the current failure mode is concentrated in input data, measurement, exits, and tuning. Measurement redesign is in progress separately, so this debate covers only the four input and tuning decisions below.

Cross-validation note: Gemini CLI was present locally, but the CLI call produced no usable response before hanging. Per fallback policy, this file uses two independent GPT passes:

- GPT-Pass-1: signal engineering and venue microstructure framing.
- GPT-Pass-2: aggressive execution and expectancy-preservation framing.

Aggressive-flow rule: loss defense should be expressed through cleaner exits, cleaner directional selection, and better venue signal routing, not through size cuts or entry suppression.

## D1 - Equity Crisis Regime Threshold

Current state: the fixed -1.5% equity crisis threshold caused 81% live misclassification, locking equity into the crisis bucket. Crypto crisis calibration must be preserved.

### GPT-Pass-1 Position

Primary position: choose per-class window-vol adaptive thresholds with an upper bound, applied by asset class rather than globally.

Evidence and rationale:

- Equity volatility behavior is not equivalent to crypto volatility behavior; one fixed drawdown threshold creates class bias.
- The observed 81% misclassification is a direct sign that the equity threshold is measuring normal equity movement as crisis.
- Per-class calibration preserves crypto behavior while letting equities use their own realized window-vol reference.
- An upper bound prevents the adaptive threshold from drifting so wide that true equity stress is ignored.
- For aggressive flow, this restores the intended regime split without shrinking trades or blocking entries.

Steelman counterargument:

Raising only the equity vol floor from 1.0% to 2.5% or more is simpler, easier to reason about, and less likely to introduce a hidden moving-target bug. A single floor correction may solve most of the equity misclassification while preserving the current regime code path.

### GPT-Pass-2 Position

Primary position: prefer adaptive per-class thresholds, but implement the first release as a constrained class-specific formula with diagnostics comparing it against the simple floor raise.

Evidence and rationale:

- The problem is not only that the equity floor is low; the fixed threshold also ignores local volatility state.
- A simple floor raise may work in current conditions but can re-fail when equity volatility contracts or expands.
- The aggressive system needs correct state labeling because regime should tune exits and directional permission quality, not dampen flow.
- Crypto calibration is explicitly protected if the adaptive change is class-scoped and crypto parameters are left unchanged.

Steelman counterargument:

Adaptive thresholds can add calibration surface area before the measurement redesign is complete. If current R metrics are known to be noisy, it may be cleaner to make the smallest equity-only correction first and revisit adaptation after the metric reset.

### Final Recommendation

Adopt option (a): per-class window-vol adaptive crisis thresholds with an explicit upper bound, class-scoped so crypto crisis calibration is unchanged. Ship with shadow logging of option (b), the equity floor raise, so Jin can see whether the simpler rule would have matched the adaptive decision. Do not keep the current fixed -1.5% rule.

## D2 - Remove session='asia' Hardcode

Current state: session is hardcoded to asia, session_mult sits at a 0.3 floor, and triple_block fired 0 times. Tuning is neutered. Need real session separation for US RTH, Capital session, and 24/7 crypto. Naive removal risks full-size flow on losing strategies. The learner is currently win-rate-only.

### GPT-Pass-1 Position

Primary position: remove the hardcode and replace it with venue-native session classification plus expectancy-weighted learning.

Evidence and rationale:

- A permanent asia session label destroys the meaning of session_mult and makes live tuning mostly cosmetic.
- US equities need RTH versus non-RTH separation because spread, liquidity, gap behavior, and momentum follow-through differ sharply.
- Capital CFD needs its own active-session map because the tradable session is not the same object as US equity RTH or crypto 24/7.
- Crypto should be treated as continuous, with liquidity windows rather than a forced equity-style session.
- Win-rate-only learning can reward small wins and large losses; expectancy weighting is needed so a strategy with poor payoff shape does not get promoted just because it wins often.

Steelman counterargument:

Immediate full session separation may create fragmented sample buckets and unstable multipliers. A naive expectancy guard can also overreact to a few large losses while measurement is still being corrected.

### GPT-Pass-2 Position

Primary position: real session separation is mandatory, but the guard should be flow-preserving: adjust strategy/session preference and exit strictness, not entry permission or size.

Evidence and rationale:

- The current triple_block firing 0 times means the system is not learning a meaningful session veto or preference.
- Aggressive flow is best preserved by routing trades toward sessions where the edge expresses, while still allowing active directional bets.
- Expectancy should be computed with realized dollar truth and post-redesign normalized R once available; until then, use conservative diagnostics that separate venue, symbol, strategy, and session.
- The guard should combine win rate, average win, average loss, payoff ratio, and trade count confidence, but output a ranking or multiplier within allowed tuning behavior rather than a hard block.

Steelman counterargument:

Because measurement is known to be flawed, adding expectancy can create false precision. Keeping a simple win-rate learner temporarily may be safer if the only immediate goal is to unfreeze the session hardcode.

### Final Recommendation

Remove the asia hardcode and implement venue-native session classification: US equity RTH and off-hours, Capital active windows, and crypto continuous liquidity windows. Upgrade the learner from win-rate-only to expectancy-weighted scoring using realized dollar ledger now and normalized R after the metric reset. The guard should tune session preference, signal priority, and exit precision; it should not reduce size or block entries by default.

## D3 - P5 Tick Engine Venue

Current state: Capital websocket has zero size/trade data, so burst_rider and flow_pressure are structurally dead. OKX has full depth, but the tick engine is not pointed there.

### GPT-Pass-1 Position

Primary position: move the tick engine to OKX as the primary tick-signal venue.

Evidence and rationale:

- Tick strategies need actual trades, size, depth changes, and pressure data. Capital currently lacks the required fields.
- Keeping the tick engine on Capital means the engine is live in name but signal-dead in practice.
- OKX provides full depth, making it the best immediate venue for burst_rider, flow_pressure, and microstructure-driven entries.
- Aggressive flow benefits from a venue where tick signals can fire often and express with real order book context.

Steelman counterargument:

Capital still produced relatively sane results in the broader audit, and CFD price behavior may be useful for overshoot-fade logic even without full size data. Abandoning Capital as a tick venue could throw away a distinct mean-reversion edge.

### GPT-Pass-2 Position

Primary position: choose option (b): make OKX the main tick engine and keep Capital only for a dedicated overshoot-fade module that does not depend on missing size/trade fields.

Evidence and rationale:

- OKX maximizes tick signal quality because it has the market data required by the current tick indicators.
- Capital should not host burst_rider or flow_pressure until its data feed includes the missing fields.
- Capital can still support price-only or bar-derived overshoot-fade behavior if that logic is explicitly designed around available data.
- This split preserves aggressive flow across both venues while matching each venue to the signals it can actually support.

Steelman counterargument:

Option (b) is more work than simply moving the tick engine to OKX. If the immediate objective is one correction with the highest signal gain, option (a) is cleaner and faster.

### Final Recommendation

Adopt option (b) as the target: point the P5 tick engine to OKX for depth-backed burst_rider and flow_pressure, and reserve Capital for a dedicated overshoot-fade path that uses only fields Capital reliably provides. If implementation must be phased, ship option (a) first and add the Capital price-only fade path next. Do not keep the current Capital tick-engine routing.

## D4 - volume_burst Strategy

Current state: volume_burst is buying spike tops and bleeding. The headline R loss is suspect because measurement has known flaws, and the largest dollar drawdown is Alpaca equity. The strategy must not be isolated or removed. Fade conversion means an active directional SELL bet on spike failure plus resistance, not a block.

### GPT-Pass-1 Position

Primary position: convert volume_burst from naive momentum-follow to conditional fade when spike failure and resistance are present.

Evidence and rationale:

- Buying volume spikes without confirming continuation invites spike-top entries, especially on noisy or synthetic bars.
- A spike that fails at resistance is directional information; fading it is an active trade, not inactivity.
- The conversion preserves flow because the strategy still fires, but with a different side when the local structure rejects the burst.
- Fade logic aligns better with the observed failure pattern than simply adding filters around the existing buy behavior.

Steelman counterargument:

Some volume bursts are genuine continuation starts. A full conversion to fade could miss the strongest breakout moves and invert the strategy during high-momentum regimes.

### GPT-Pass-2 Position

Primary position: implement dual-mode volume_burst: continuation only after acceptance; fade after failure at resistance. The default correction should favor fade until live diagnostics prove continuation quality.

Evidence and rationale:

- The current evidence says spike-top buy is the bleeding behavior, but measurement flaws mean the exact R magnitude should not be over-weighted.
- A dual-mode design avoids turning every burst into a short while still eliminating blind spike chasing.
- Continuation mode should require acceptance above the burst zone, follow-through, and non-synthetic volume confirmation.
- Fade mode should trigger on failed retest, resistance rejection, failed high, or burst exhaustion with a tight invalidation exit.
- This keeps the strategy aggressive by creating more precise directional bets rather than suppressing trades.

Steelman counterargument:

Dual-mode behavior adds branching and could become a hidden classifier problem. A simpler immediate fix is to switch the strategy to fade-only for known spike-top contexts and defer continuation mode until the data quality fixes are complete.

### Final Recommendation

Do not remove, isolate, or silence volume_burst. Convert it to an active fade-first strategy for spike failure plus resistance, with continuation allowed only after clear acceptance and follow-through. The protective mechanism should be precise invalidation exits and better side selection, not lower flow. Prioritize OKX data-quality checks so synthetic or zero-volume bars cannot masquerade as valid burst evidence.

## Jin Decision Delegation

Open choices left to Jin:

- D1 rollout shape: ship adaptive per-class threshold immediately, or ship it with a shadow comparison against the simpler equity floor raise for one measurement cycle.
- D2 guard strictness: decide how much weight expectancy should carry at launch versus win rate while the metric reset is still underway.
- D3 implementation order: move P5 to OKX first, or implement OKX tick routing and Capital overshoot-fade in the same change set.
- D4 mode bias: launch volume_burst as fade-first dual-mode, or run fade-only for spike-failure contexts until continuation evidence is cleaner.
