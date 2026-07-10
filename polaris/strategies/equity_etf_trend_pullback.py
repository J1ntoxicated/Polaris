"""equity_etf_trend_pullback — Alpaca US equity ETF sleeve, daily MACD
re-acceleration inside a 200-EMA uptrend. Per-venue CLONE of ``macd_ema_
trend_pullback`` (math cloned verbatim from lines 131-203 of that module) —
DEMO/PAPER virtual, no real capital / fees. The OKX original's 3 modules
(``macd_ema_trend_pullback`` / ``tsmom_12_1_multiasset`` /
``donchian_turtle_breakout``) and their dispatch are UNCHANGED — this is an
ADDITIVE clone, not a modification, resolving their "venue-inert
EQUITY_ETF_LEG" via a dedicated Alpaca-venue strategy (registry precedent:
``silver_breakout_1h`` / ``us100_breakout_1h`` / ``uk100_breakout_1h`` per-
symbol clones of ``gold_breakout_1h``, ``__init__.py:180-184``).

``SUPPORTED_SYMBOLS = {"SPY", "QQQ", "GLD"}`` is a FIXED confirmed ETF sleeve
universe (not the §0d value-based liquidity gate — these 3 are inherently
liquid, no per-symbol dollar-volume gate needed).

Signaling-strategy contract (ADR-008): entry trigger ONLY. Exit is G7-owned
(swing-low / 2*ATR stop + MACD bearish-cross OR 2.5*ATR chandelier trail).
``correlation_group_id`` carries no reversion substring → TREND let-run
bucket; ``hold_overnight=True``; ``profit_target_r=None``;
``expected_holding_bars=15``. flow_not_block: a clean trigger ALWAYS emits.

ENTRY (1d bar close, LONG-only, deterministic, no look-ahead) — IDENTICAL
math to the OKX original:
  (1) regime filter: ``close > EMA_FILTER-EMA`` (reuse pre-fed ``market_view.
      ma_200`` when finite AND ``EMA_FILTER == 200``, else recompute);
  (2) MACD line crosses ABOVE its signal line THIS bar (12/26 EMA diff, 9-EMA
      signal, recomputed in-module);
  (3) MACD line <= 0 at the cross (re-acceleration after a shallow pullback,
      NOT an exhaustion top) — DROPPED in VIRTUAL mode (identical loosening to
      the OKX original: ``virtual_mode_enabled()``);
  (4) volume confirm: current volume > the 20-bar average volume.

VIRTUAL-mode loosening mirrors the OKX original EXACTLY: ``EMA_FILTER =
virtual_loosen(20, 200)`` and condition (3) drops in virtual. REAL is byte-
identical (env unset).

🚨 metadata table values are PINNED (verbatim, per spec §1) rather than
re-derived from ``EMA_FILTER`` — ``WARMUP_BARS = 235`` stays the real
200+26+9 basis in BOTH modes (unlike the OKX original, whose own
``warmup_bars`` shrinks with the virtual-loosened ``EMA_FILTER``). This is a
deliberate divergence from the OKX original's dynamic warmup formula, per the
build spec's "metadata 테이블값 그대로" instruction.

**12-1 shadow comparator** (measurement only, NO gating — not an independent
strategy, promotion judged after live performance separation): ``mom_12_1 =
close/close[-253] - close/close[-22]``, stamped as ``tags={"tsmom_12_1":
"<value>", "tsmom_12_1_pos": "1"|"0"}``. Skipped (tags simply omit the two
keys) when history is shorter than 253 bars — degrade-never-crash.

Verified params are named Final constants (no magic numbers):
  - ``EMA_FILTER`` (virtual-loosened) / ``MACD_FAST=12`` / ``MACD_SLOW=26``
  - ``MACD_SIGNAL=9`` / ``VOL_LOOKBACK=20``
  - ``TSMOM_12M_LOOKBACK=253`` / ``TSMOM_1M_LOOKBACK=22`` (shadow only)
"""

from __future__ import annotations

from typing import Final

from polaris.strategies._virtual_loosen import virtual_loosen, virtual_mode_enabled
from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

# Identical virtual-mode loosening to the OKX original (Jin 2026-07-07/08):
# 200 -> 20-EMA regime filter; the MACD<=0 pullback-only gate is DROPPED in
# virtual (see generate_raw_signal). REAL byte-identical (env unset).
EMA_FILTER: Final[int] = virtual_loosen(20, 200)
MACD_FAST: Final[int] = 12
MACD_SLOW: Final[int] = 26
MACD_SIGNAL: Final[int] = 9
VOL_LOOKBACK: Final[int] = 20
# metadata table value (verbatim) — NOT re-derived from the virtual-loosened
# EMA_FILTER (which would collapse the gate in virtual mode); the real
# 200+26+9 basis stays the warmup floor in BOTH modes.
WARMUP_BARS: Final[int] = 235

STRENGTH_BASE: Final[float] = 0.5
HIST_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3

# Fixed confirmed ETF sleeve universe (NOT the §0d value-liquidity gate).
SUPPORTED_SYMBOLS: Final[frozenset[str]] = frozenset({"SPY", "QQQ", "GLD"})

# 12-1 shadow comparator lookbacks (measurement only, no gating).
TSMOM_12M_LOOKBACK: Final[int] = 253
TSMOM_1M_LOOKBACK: Final[int] = 22


def _norm_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "-").replace("_", "-")


def _ema_series(values: list[float], period: int) -> list[float]:
    """Standard EMA over ``values`` (seeded with the first value). Returns a
    series the same length as ``values`` (caller has ensured ``len >= period``)."""
    k = 2.0 / (period + 1.0)
    ema = values[0]
    out = [ema]
    for v in values[1:]:
        ema = v * k + ema * (1.0 - k)
        out.append(ema)
    return out


def _ema_last(values: list[float], period: int) -> float:
    return _ema_series(values, period)[-1]


class EquityEtfTrendPullbackStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="equity_etf_trend_pullback",
        timeframe="1D",
        warmup_bars=WARMUP_BARS,
        max_positions=3,
        gross_cap=0.15,
        per_symbol_cap=0.06,
        expected_holding_bars=15,
        asset_class="equity",
        venue="alpaca",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="equity_etf_trend_continuation",
        product_class="equity",
        hold_overnight=True,
        profit_target_r=None,
        dispatch_eligible=virtual_loosen(True, False),
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if _norm_symbol(market_view.symbol) not in SUPPORTED_SYMBOLS:
            return None
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < WARMUP_BARS:
            return None
        last = bars[-1]
        closes = [b.close for b in bars]

        # (1) Regime filter: close > EMA_FILTER-EMA. Reuse the pre-fed MA-200
        # when finite AND EMA_FILTER is still 200 (REAL mode only), else
        # recompute in-module.
        if EMA_FILTER == 200 and is_finite(market_view.ma_200):
            ema_filter_val = market_view.ma_200
            assert ema_filter_val is not None
        else:
            ema_filter_val = _ema_last(closes, EMA_FILTER)
        if last.close <= ema_filter_val:
            return None

        # (2)+(3) MACD line/signal/histogram (recompute in-module).
        fast_series = _ema_series(closes, MACD_FAST)
        slow_series = _ema_series(closes, MACD_SLOW)
        macd_line = [f - s for f, s in zip(fast_series, slow_series, strict=True)]
        signal_series = _ema_series(macd_line, MACD_SIGNAL)
        line_now, line_prev = macd_line[-1], macd_line[-2]
        sig_now, sig_prev = signal_series[-1], signal_series[-2]
        bullish_cross = line_prev <= sig_prev and line_now > sig_now
        if not bullish_cross:
            return None
        # (3) re-acceleration AFTER a shallow pullback. VIRTUAL-mode
        # loosening (identical to the OKX original): DROP this filter in
        # virtual, REAL keeps it byte-identical.
        if not virtual_mode_enabled() and line_now > 0.0:
            return None

        # (4) Volume confirm: current volume > the 20-bar average volume.
        prior_vols = [b.volume for b in bars[-(VOL_LOOKBACK + 1):-1]]
        avg_vol = sum(prior_vols) / len(prior_vols)
        if last.volume <= avg_vol:
            return None

        # Strength scales with the MACD histogram magnitude, floored + capped.
        hist = line_now - sig_now
        scored = STRENGTH_BASE + HIST_STRENGTH_GAIN * abs(hist) / max(last.close, 1e-9)
        strength = min(1.0, max(STRENGTH_BASE, scored))

        tags = {
            "macd_line": f"{line_now:.6f}",
            "macd_signal": f"{sig_now:.6f}",
            "macd_hist": f"{hist:.6f}",
            "ema_filter": f"{ema_filter_val:.4f}",
        }
        # 12-1 shadow comparator (measurement only). Needs a 253-bar-back
        # close; omit the stamp (degrade-never-crash) when history is shorter.
        if len(closes) >= TSMOM_12M_LOOKBACK:
            base_12m = closes[-TSMOM_12M_LOOKBACK]
            base_1m = closes[-TSMOM_1M_LOOKBACK]
            if base_12m != 0.0 and base_1m != 0.0:
                mom_12_1 = last.close / base_12m - last.close / base_1m
                tags["tsmom_12_1"] = f"{mom_12_1:.4f}"
                tags["tsmom_12_1_pos"] = "1" if mom_12_1 > 0.0 else "0"

        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"equity_etf_trend_pullback+macd_cross_le0={line_now:.6f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags=tags,
        )


__all__ = [
    "EMA_FILTER",
    "EquityEtfTrendPullbackStrategy",
    "HIST_STRENGTH_GAIN",
    "MACD_FAST",
    "MACD_SIGNAL",
    "MACD_SLOW",
    "STRENGTH_BASE",
    "SUPPORTED_SYMBOLS",
    "TSMOM_12M_LOOKBACK",
    "TSMOM_1M_LOOKBACK",
    "TTL_BARS",
    "VOL_LOOKBACK",
    "WARMUP_BARS",
]
