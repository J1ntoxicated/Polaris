"""Volume Burst — OKX SPOT, 1m bar (correlation_group=spot_intraday_event).

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md (#1).
D4 fade-first conversion: /debate vault/50_research/debates/trading_params_audit_2026-06-22.md.

Common gate (a real volume burst): ``volume_z >= 2.5`` AND ``atr_pct >= 0.05%``
(liquidity floor — micro-caps without ATR get blocked). Once the burst is real,
the *side* is chosen by how the spike interacts with structural resistance
(``prior_high`` over the lookback window):

  - FADE / SHORT (default): the bar's HIGH pierces resistance but the CLOSE
    fails to hold above it (close <= prior_high) — a spike rejected at the level
    (the spike-top that was bleeding when bought blindly). Take the fade.
  - CONTINUATION / LONG: the CLOSE is decisively above resistance — clear
    acceptance above the level — so the old long is allowed.

flow_not_block: the strategy still FIRES on every real burst that touches the
level — it is re-aimed (polarity by structure), not blocked / size-cut.
Deterministic gates still own the lifecycle decision.

Frozen P0 params (Day 4 task brief):
  - ``vol_z_threshold = 2.5``
  - ``lookback = 20``
  - ``atr_floor_pct = 0.05`` (0.05% — fraction = 0.0005)
"""

from __future__ import annotations

from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

VOL_Z_THRESHOLD = 2.5
LOOKBACK = 20
ATR_FLOOR_PCT = 0.0005  # 0.05% (fraction)

# Strength + sizing curve constants (frozen v1 — Day 4 R2 hardcode lift).
STRENGTH_BASE = 0.6
STRENGTH_SLOPE = 0.1
SIZING_BASE = 0.5
SIZING_SLOPE = 0.1
TTL_BARS = 10


class VolumeBurstStrategy(BaseStrategy):
    # Varyable ENTRY-trigger knobs (P0a). Class defaults == module constants ==
    # frozen baseline -> behavior-0 for default instances.
    vol_z_threshold: float = VOL_Z_THRESHOLD
    atr_floor_pct: float = ATR_FLOOR_PCT
    # ttl_bars is intentionally NOT in PARAM_BOUNDS (inert-in-replay: the
    # precise-exit FSM never reads the signal TTL). Kept for behavior-0.
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="volume_burst",
        timeframe="1m",
        warmup_bars=LOOKBACK + 5,
        max_positions=3,
        gross_cap=0.24,
        per_symbol_cap=0.08,
        expected_holding_bars=15,
        asset_class="spot",
        venue="okx",
        correlation_group_id="spot_intraday_event",
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if not self.warmup_ok(market_view):
            return None
        if not is_finite(market_view.atr_pct) or market_view.atr_pct < self.atr_floor_pct:
            return None
        if not is_finite(market_view.volume_z) or market_view.volume_z < self.vol_z_threshold:
            return None
        bars = market_view.bars
        if len(bars) < LOOKBACK + 1:
            return None
        last = bars[-1]
        prior_high = max(b.high for b in bars[-(LOOKBACK + 1):-1])
        # Strength scales with vol_z above threshold (clamped 0-1).
        excess = market_view.volume_z - self.vol_z_threshold
        strength = min(1.0, STRENGTH_BASE + STRENGTH_SLOPE * excess)
        sizing = min(1.0, SIZING_BASE + SIZING_SLOPE * excess)
        # CONTINUATION / LONG: clear acceptance — the body CLOSED above resistance.
        if last.close > prior_high:
            return RawSignal(
                signal_id=make_signal_id(),
                strategy_id=self.metadata.strategy_id,
                symbol=market_view.symbol,
                side="long",
                strength=strength,
                sizing_hint=sizing,
                ttl_bars=self.ttl_bars,
                thesis_tag=f"vol_z={market_view.volume_z:.2f}>break",
                correlation_group=self.metadata.correlation_group_id,
                venue_constraints={},
                created_at_bar=last.ts,
                tags={"vol_z": f"{market_view.volume_z:.2f}",
                      "atr_pct": f"{market_view.atr_pct:.5f}"},
            )
        # FADE / SHORT: the HIGH pierced resistance but the CLOSE failed to hold
        # above it — spike rejected at the level. (high <= prior_high = burst
        # that never reached resistance: no structure to fade -> no signal.)
        if last.high > prior_high:
            return RawSignal(
                signal_id=make_signal_id(),
                strategy_id=self.metadata.strategy_id,
                symbol=market_view.symbol,
                side="short",
                strength=strength,
                sizing_hint=sizing,
                ttl_bars=self.ttl_bars,
                thesis_tag=f"vol_z={market_view.volume_z:.2f}>fade_reject",
                correlation_group=self.metadata.correlation_group_id,
                venue_constraints={},
                created_at_bar=last.ts,
                tags={"vol_z": f"{market_view.volume_z:.2f}",
                      "atr_pct": f"{market_view.atr_pct:.5f}",
                      "mode": "fade"},
            )
        return None


__all__ = [
    "ATR_FLOOR_PCT",
    "LOOKBACK",
    "SIZING_BASE",
    "SIZING_SLOPE",
    "STRENGTH_BASE",
    "STRENGTH_SLOPE",
    "TTL_BARS",
    "VOL_Z_THRESHOLD",
    "VolumeBurstStrategy",
]
