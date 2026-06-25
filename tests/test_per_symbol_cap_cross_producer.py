"""Wave 1 PRE-REQ — the open-dedup backstop keys on (venue, symbol) across BOTH
producers (tick engine + bar pipeline).

DEMO/PAPER. After the routing carve-out the ``owns_okx`` skip no longer keeps the
two producers off the same symbol, so the per-symbol RISK cap must be the
load-bearing cross-producer backstop. It is: ``per_symbol_remaining_pct`` sums
``open_risk_pct`` over EVERY open position matching ``(venue, symbol)`` regardless
of ``strategy`` — so a tick-engine open (strategy="flow_pressure") and a bar open
(strategy="xau_indices_trend") on the SAME symbol both consume the same
per-symbol headroom. Both producers persist via the shared ``reserve_and_submit``
→ ``persist_position_risk_state`` path, so ``position_risk_state`` carries both.
"""

from __future__ import annotations

from polaris.core.sizing import per_symbol_remaining_pct, venue_per_symbol_cap
from polaris.core.sizing.schema import PositionRiskState


def _pos(*, venue: str, symbol: str, strategy: str, risk: float) -> PositionRiskState:
    return PositionRiskState(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id="g",
        cluster_id=None,
        strategy=strategy,
        track="B",
        signal_strength=0.5,
        open_risk_pct=risk,
        notional_usd=1000.0,
        opened_ts=0,
    )


def test_tick_and_bar_open_share_per_symbol_headroom() -> None:
    cap = venue_per_symbol_cap("capital")
    # Tick-engine open on GOLD (different strategy_id than any bar strategy).
    tick_open = _pos(venue="capital", symbol="GOLD", strategy="flow_pressure", risk=0.04)
    # A bar strategy now reaching GOLD sees REDUCED headroom — the tick open is
    # counted even though the strategy_id differs. This is the cross-producer
    # backstop that prevents an unbounded double-open after the routing widen.
    remaining = per_symbol_remaining_pct(
        venue="capital", symbol="GOLD", open_positions=[tick_open]
    )
    assert remaining == cap - 0.04
    assert remaining < cap  # the tick open DID consume bar headroom


def test_per_symbol_cap_is_strategy_agnostic() -> None:
    # Two producers stacking the same symbol consume the SAME (venue, symbol)
    # budget — the cap does not reset per strategy_id.
    p1 = _pos(venue="capital", symbol="US100", strategy="flow_pressure", risk=0.05)
    p2 = _pos(venue="capital", symbol="US100", strategy="session_breakout", risk=0.05)
    remaining = per_symbol_remaining_pct(
        venue="capital", symbol="US100", open_positions=[p1, p2]
    )
    cap = venue_per_symbol_cap("capital")
    assert abs(remaining - max(0.0, cap - 0.10)) < 1e-9


def test_okx_tick_scalp_and_bar_swing_share_per_symbol_headroom() -> None:
    # STEP1 multi-horizon: after the OKX-spot bar carve-out a tick SCALP
    # (strategy="micro_reversion") and a bar SWING (strategy="tsmom") can both
    # open on the SAME OKX symbol. The per-symbol risk cap is the resource backstop
    # — the swing entry sees headroom REDUCED by the live scalp open (summed across
    # producers regardless of strategy_id), so the two horizons cannot each claim
    # the full per-symbol budget on one symbol. flow_not_block: neither producer is
    # throttled; whoever fills uses the shared headroom, the cap is the existing rail.
    cap = venue_per_symbol_cap("okx")
    scalp = _pos(venue="okx", symbol="BTC-USDT", strategy="micro_reversion", risk=0.03)
    swing = _pos(venue="okx", symbol="BTC-USDT", strategy="tsmom", risk=0.03)
    remaining = per_symbol_remaining_pct(
        venue="okx", symbol="BTC-USDT", open_positions=[scalp, swing]
    )
    assert abs(remaining - max(0.0, cap - 0.06)) < 1e-9
    assert remaining < cap  # the scalp + swing BOTH consumed the per-symbol budget


def test_other_symbol_does_not_consume_headroom() -> None:
    # An open on a DIFFERENT symbol leaves GOLD's headroom untouched.
    other = _pos(venue="capital", symbol="US30", strategy="flow_pressure", risk=0.04)
    remaining = per_symbol_remaining_pct(
        venue="capital", symbol="GOLD", open_positions=[other]
    )
    assert remaining == venue_per_symbol_cap("capital")
