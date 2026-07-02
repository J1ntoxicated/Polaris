"""Layer 2 — G5 Entry Sizer payload builder (SignalIntent + risk + portfolio).

Split out of ``payload_builder.py`` to keep each module ≤500 LOC. ``payload_builder``
re-exports ``build_sizer_payload`` + ``default_strategy_risk_state`` so existing
import paths keep working. Spec: vault/30_components/layer-2-per-gate-pipeline.md.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, cast

from polaris.core.live_recalc.exit_engine import (
    Bucket,
    bucket_from_correlation_group,
)
from polaris.core.pipeline._payload_db import _safe_query
from polaris.core.sizing import (
    PortfolioState,
    PositionRiskState,
    SignalIntent,
    StrategyRiskState,
    Track,
)
from polaris.strategies.base import RawSignal


def default_strategy_risk_state(
    *,
    venue: str,
    strategy: str,
    closed_trades: int = 0,
    kelly_p: float = 0.0,
    kelly_q: float = 0.0,
    kelly_fraction: float = 0.0,
    win_streak: int = 0,
    hit_rate_10: float = 0.0,
    now_ts: int | None = None,
) -> StrategyRiskState:
    """Cold-start risk state used when ``strategy_risk_state`` row missing."""
    return StrategyRiskState(
        venue=venue,
        strategy=strategy,
        closed_trades=int(closed_trades),
        kelly_p=float(kelly_p),
        kelly_q=float(kelly_q),
        kelly_fraction=float(kelly_fraction),
        win_streak=int(win_streak),
        hit_rate_10=float(hit_rate_10),
        updated_ts=int(now_ts if now_ts is not None else time.time()),
    )


def _read_strategy_risk_state(
    conn: sqlite3.Connection, *, venue: str, strategy: str, now_ts: int
) -> StrategyRiskState:
    rows = _safe_query(
        conn,
        """
        SELECT closed_trades, kelly_p, kelly_q, kelly_fraction,
               win_streak, hit_rate_10, updated_ts
        FROM strategy_risk_state
        WHERE venue = ? AND strategy = ?
        """,
        (venue, strategy),
    )
    if not rows:
        return default_strategy_risk_state(
            venue=venue, strategy=strategy, now_ts=now_ts
        )
    r = rows[0]
    return StrategyRiskState(
        venue=venue,
        strategy=strategy,
        closed_trades=int(r[0]),
        kelly_p=float(r[1]),
        kelly_q=float(r[2]),
        kelly_fraction=float(r[3]),
        win_streak=int(r[4]),
        hit_rate_10=float(r[5]),
        updated_ts=int(r[6]),
    )


def _read_portfolio_state(
    conn: sqlite3.Connection,
    *,
    equity_usd: float,
    track: Track,
) -> PortfolioState:
    """Snapshot of open risk usage + venue/total daily.

    P0 = best-effort: if ``position_risk_state`` is empty (cold start) we
    return zero usage. Layer 5 will populate this row by closing trades
    through the cell-matrix update path.
    """
    rows = _safe_query(
        conn,
        """
        SELECT venue, symbol, instrument_id, underlying_group_id, cluster_id,
               strategy, track, signal_strength, open_risk_pct, notional_usd,
               opened_ts
        FROM position_risk_state
        """,
    )
    open_positions: list[PositionRiskState] = []
    for r in rows:
        try:
            # 3-way decode (A/B/C): pass the stored DB value through, never
            # silently collapse C -> B. ``cast`` documents the Track contract;
            # an unexpected stray value is preserved as-is for audit fidelity.
            track_val = cast(Track, str(r[6]))
            open_positions.append(
                PositionRiskState(
                    venue=str(r[0]),
                    symbol=str(r[1]),
                    instrument_id=str(r[2]),
                    underlying_group_id=str(r[3]),
                    cluster_id=str(r[4]) if r[4] is not None else None,
                    strategy=str(r[5]),
                    track=track_val,
                    signal_strength=float(r[7]),
                    open_risk_pct=float(r[8]),
                    notional_usd=float(r[9]),
                    opened_ts=int(r[10]),
                )
            )
        except (TypeError, ValueError):
            continue
    return PortfolioState(
        equity_usd=float(equity_usd),
        venue_daily_used_pct=0.0,
        total_daily_used_pct=0.0,
        track_used_pct={track: 0.0},
        open_positions=open_positions,
        fill_rate_active_cut=False,
    )


def build_sizer_payload(
    *,
    raw_signal: RawSignal,
    venue: str,
    symbol: str,
    instrument_id: str,
    underlying_group_id: str,
    asset_class: str,
    regime: str,
    track: Track,
    listing_age_hours: float,
    leverage: float = 1.0,
    base_risk_pct: float | None = None,
    equity_usd: float = 10_000.0,
    conn: sqlite3.Connection | None = None,
    now_ts: int | None = None,
    entry_price: float = 0.0,
    atr_pct: float = 0.0,
    stop_atr_mult: float = 0.0,
) -> dict[str, Any]:
    """Compose the G5 payload — SignalIntent + StrategyRiskState + PortfolioState.

    ``equity_usd`` defaults to a paper-account proxy ($10k); the smoke loop
    overrides with the real demo balance once we wire ``fetch_balance`` in
    Day 7. ``leverage`` is venue-set (OKX SPOT = 1, Capital from constraint
    translator). ``entry_price``/``atr_pct``/``stop_atr_mult`` default 0.0
    (byte-identical no-op — Wave B agenda ① R-budget shadow compute in
    ``compute_size`` treats <=0 as data-integrity/module-default fallback);
    callers that have a live last price / bar ATR% / fee-floored stop mult
    (``exit_strategy_config._stop_atr_mult_for_strategy`` — scripts-layer
    only, core must not import it) thread them so the shadow observation
    fires with the real fee-floor.
    """
    ts = int(now_ts if now_ts is not None else time.time())
    # Regime-fit family: derive from the strategy's correlation-group archetype
    # (REVERSION bucket → "reversion"; everything else → "momentum"). Makes the
    # BAR entry path regime-aware via seam1 (the ONE T4 continuous scalar).
    signal_family = (
        "reversion"
        if bucket_from_correlation_group(raw_signal.correlation_group) is Bucket.REVERSION
        else "momentum"
    )
    intent = SignalIntent(
        signal_id=raw_signal.signal_id,
        venue=venue,
        symbol=symbol,
        instrument_id=instrument_id,
        underlying_group_id=underlying_group_id,
        asset_class=asset_class,
        strategy=raw_signal.strategy_id,
        track=track,
        regime=regime,
        direction=raw_signal.side,
        signal_strength=float(raw_signal.strength),
        listing_age_hours=float(listing_age_hours),
        leverage=float(leverage),
        base_risk_pct=float(base_risk_pct) if base_risk_pct is not None else 0.02,
        signal_family=signal_family,
        entry_price=float(entry_price),
        atr_pct=float(atr_pct),
        stop_atr_mult=float(stop_atr_mult),
    )
    if conn is None:
        risk_state = default_strategy_risk_state(
            venue=venue, strategy=raw_signal.strategy_id, now_ts=ts
        )
        portfolio = PortfolioState(
            equity_usd=float(equity_usd),
            venue_daily_used_pct=0.0,
            total_daily_used_pct=0.0,
            track_used_pct={track: 0.0},
        )
    else:
        risk_state = _read_strategy_risk_state(
            conn, venue=venue, strategy=raw_signal.strategy_id, now_ts=ts
        )
        portfolio = _read_portfolio_state(
            conn, equity_usd=equity_usd, track=track
        )
    return {
        "signal_intent": intent,
        "risk_state": risk_state,
        "portfolio": portfolio,
    }
