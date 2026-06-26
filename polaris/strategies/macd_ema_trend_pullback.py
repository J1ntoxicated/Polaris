"""macd_ema_trend_pullback — 1D MACD re-acceleration inside a 200-EMA uptrend.

Spec source: research selection ``macd_ema_trend_pullback`` (rank-3, DEMO/PAPER).
A momentum-CONTINUATION archetype (buy re-acceleration inside an established
trend) distinct from the channel-breakout family (``donchian_*``): a different
entry trigger, lower correlation within the trend bucket. Strengthens OKX-crypto
trend coverage and gives the equity-ETF sleeve a second trend engine alongside
``tsmom_12_1_multiasset`` (the equity leg is blocked on the Alpaca SIP key #42; the
crypto leg is live now).

⚠️ EXPLICIT WARNINGS:
  (a) OOS 4x decay (H1 +706 → H2 +171bps) → conservative sizing.
  (b) Thin per-symbol n (3-15 trades; SOL's 1909bps = 3 trades = noise) → no
      thin-crypto-solo trust; the per-symbol gate kills XRP-class chronic losers.
  (c) The SHORT mirror is UNVERIFIED → build LONG-only.
expected_edge_bps = 318 = the trade-weighted random-excess (real selection edge
above beta, survives in low-beta ETFs +69~110bps) — conservative vs the +397
headline.

Signaling-strategy contract (ADR-008): this module emits the ENTRY trigger ONLY.
The verified exit (swing-low or 2*ATR initial stop + MACD bearish-cross OR a
2.5*ATR chandelier trail letting the continuation leg run) is owned by G5/G7 via
``StrategyMetadata`` (TREND let-run — ``correlation_group_id`` has no reversion
substring; ``hold_overnight=True``; ``profit_target_r=None`` so it is asymmetric:
defined swing-low risk vs a trailed target, NOT a fixed take-profit;
``expected_holding_bars=15``). flow_not_block: a clean trigger ALWAYS emits.

ENTRY (1d bar close, LONG-only, deterministic, no look-ahead) requires ALL:
  (1) regime filter: ``close > 200-EMA`` (uptrend; reuse ``market_view.ma_200``
      when finite, else recompute);
  (2) MACD line crosses ABOVE its signal line THIS bar (12/26 EMA diff, 9-EMA
      signal — recomputed in-module);
  (3) MACD line <= 0 (re-acceleration AFTER a shallow pullback, NOT an exhaustion
      top);
  (4) volume confirm: current volume > the 20-bar average volume (participation
      that clears fees).
🚨 PER-SYMBOL GATE: emit ONLY on the validated symbol subset (XRP was net-NEG
-35bps, -235 vs random) — applied flow-safe as a symbol-SET match (this strategy
emits only on its set; it never blocks the universe / other strategies).

🚨 INDICATOR GAP: MACD (12/26/9) and the 20-bar average volume are NOT pre-fed —
the MACD line/signal/histogram are recomputed in-module from closes; the 200-EMA
reuses the pre-fed ``market_view.ma_200`` when finite, else recomputes.
warmup_bars = 200 + 26 + 9 = 235 (the regime filter needs MA-200).

Verified params are named Final constants (no magic numbers):
  - ``EMA_FILTER = 200`` / ``MACD_FAST = 12`` / ``MACD_SLOW = 26``
  - ``MACD_SIGNAL = 9`` / ``VOL_LOOKBACK = 20``
"""

from __future__ import annotations

from typing import Final

from polaris.strategies._okx_liquid_universe import okx_liquid_top_n
from polaris.strategies.base import (
    BaseStrategy,
    MarketView,
    RawSignal,
    StrategyMetadata,
    is_finite,
    make_signal_id,
)

EMA_FILTER: Final[int] = 200
MACD_FAST: Final[int] = 12
MACD_SLOW: Final[int] = 26
MACD_SIGNAL: Final[int] = 9
VOL_LOOKBACK: Final[int] = 20

STRENGTH_BASE: Final[float] = 0.5
HIST_STRENGTH_GAIN: Final[float] = 4.0
TTL_BARS: Final[int] = 3

# OKX-liquid top-N crypto leg (env POLARIS_STRAT_SYMBOL_TOP_N, default 60 —
# widened from the old 3-major pin so the daily trend-pullback has real entry
# breadth; Jin 2026-06-27) + a low-beta liquid-ETF sleeve (Alpaca, SIP #42 —
# venue-inert until routed). Flow-safe symbol SET — emits only here, never a
# universe block.
EQUITY_ETF_LEG: Final[frozenset[str]] = frozenset({"SPY", "QQQ", "GLD"})
SUPPORTED_SYMBOLS: Final[frozenset[str]] = okx_liquid_top_n() | EQUITY_ETF_LEG


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


class MACDEMATrendPullbackStrategy(BaseStrategy):
    ttl_bars: int = TTL_BARS

    metadata = StrategyMetadata(
        strategy_id="macd_ema_trend_pullback",
        timeframe="1D",
        warmup_bars=EMA_FILTER + MACD_SLOW + MACD_SIGNAL,  # 200 + 26 + 9 = 235
        max_positions=3,
        gross_cap=0.20,
        per_symbol_cap=0.07,
        expected_holding_bars=15,
        asset_class="crypto",
        venue="okx",
        # No reversion substring → TREND exit archetype (let-winners-run).
        correlation_group_id="macd_ema_trend_continuation",
        product_class="spot",
        hold_overnight=True,
        profit_target_r=None,
    )

    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        if _norm_symbol(market_view.symbol) not in SUPPORTED_SYMBOLS:
            return None
        if not self.warmup_ok(market_view):
            return None
        bars = market_view.bars
        if len(bars) < self.metadata.warmup_bars:
            return None
        last = bars[-1]
        closes = [b.close for b in bars]

        # (1) Regime filter: close > 200-EMA (uptrend). Reuse the pre-fed MA-200
        # when finite, else recompute the 200-EMA from closes.
        if is_finite(market_view.ma_200):
            ema_200 = market_view.ma_200
            assert ema_200 is not None
        else:
            ema_200 = _ema_last(closes, EMA_FILTER)
        if last.close <= ema_200:
            return None

        # (2)+(3) MACD line/signal/histogram (recompute in-module). The MACD line
        # = EMA(fast) - EMA(slow); the signal = EMA(MACD line, 9). A bullish cross
        # THIS bar = line was <= signal on the prior bar and > signal now.
        fast_series = _ema_series(closes, MACD_FAST)
        slow_series = _ema_series(closes, MACD_SLOW)
        macd_line = [f - s for f, s in zip(fast_series, slow_series, strict=True)]
        signal_series = _ema_series(macd_line, MACD_SIGNAL)
        line_now, line_prev = macd_line[-1], macd_line[-2]
        sig_now, sig_prev = signal_series[-1], signal_series[-2]
        bullish_cross = line_prev <= sig_prev and line_now > sig_now
        if not bullish_cross:
            return None
        # (3) re-acceleration AFTER a shallow pullback (NOT an exhaustion top):
        # the MACD line must be at/below zero at the cross.
        if line_now > 0.0:
            return None

        # (4) Volume confirm: current volume > the 20-bar average volume.
        prior_vols = [b.volume for b in bars[-(VOL_LOOKBACK + 1):-1]]
        avg_vol = sum(prior_vols) / len(prior_vols)
        if last.volume <= avg_vol:
            return None

        # Strength scales with the MACD histogram magnitude (re-acceleration
        # impulse), floored + capped. EXPECTANCY-positive size, never a dampen.
        hist = line_now - sig_now
        scored = STRENGTH_BASE + HIST_STRENGTH_GAIN * abs(hist) / max(last.close, 1e-9)
        strength = min(1.0, max(STRENGTH_BASE, scored))
        return RawSignal(
            signal_id=make_signal_id(),
            strategy_id=self.metadata.strategy_id,
            symbol=market_view.symbol,
            side="long",
            strength=strength,
            sizing_hint=strength,
            ttl_bars=self.ttl_bars,
            thesis_tag=f"macd_ema_pullback+macd_cross_le0={line_now:.6f}",
            correlation_group=self.metadata.correlation_group_id,
            venue_constraints={},
            created_at_bar=last.ts,
            tags={
                "macd_line": f"{line_now:.6f}",
                "macd_signal": f"{sig_now:.6f}",
                "macd_hist": f"{hist:.6f}",
                "ema_200": f"{ema_200:.4f}",
            },
        )


__all__ = [
    "EMA_FILTER",
    "HIST_STRENGTH_GAIN",
    "MACDEMATrendPullbackStrategy",
    "MACD_FAST",
    "MACD_SIGNAL",
    "MACD_SLOW",
    "STRENGTH_BASE",
    "SUPPORTED_SYMBOLS",
    "TTL_BARS",
    "VOL_LOOKBACK",
]
