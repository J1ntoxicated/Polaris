"""Pure fill model — entry/exit price, round-trip REAL fee, pnl USD + R.

No look-ahead by construction: ``entry_fill_price`` is the **next bar's open**
(the bar AFTER the signal bar), never the signal bar's close. Slippage is half
the ``spread_bps_close`` of the fill bar, applied adversely (long pays up, short
sells down). Fees use the live ``real_fee_usd`` schedule verbatim — round-trip
== exactly 2 legs (TDD #3).

PnL is reported in USD and in R units. ``PNL_R_USD_DENOM`` is the per-trade R
denominator (USD risk per 1R) used to normalise USD pnl into the R units the
cell-matrix / learner network consume on close.
"""

from __future__ import annotations

from typing import Final

from polaris.core.economics.fees import real_fee_usd

__all__ = [
    "PNL_R_USD_DENOM",
    "apply_slippage",
    "gross_pnl_usd",
    "pnl_to_r",
    "round_trip_fee_usd",
]

# Per-trade R denominator: USD of pnl that equals 1R. Replay normalises every
# net USD pnl by this so the cell-matrix update + R-based metrics stay on one
# scale. Chosen to match the project's $50/R sizing convention (spec §fill).
PNL_R_USD_DENOM: Final[float] = 50.0

_BPS_DIVISOR: Final[float] = 10_000.0


def apply_slippage(*, raw_price: float, spread_bps: float, side: str, is_entry: bool) -> float:
    """Half-spread adverse slippage on a fill price.

    Entry adverse = long pays the ask side (price up), short hits the bid
    (price down). Exit adverse = the mirror (long sells down, short covers up).
    ``spread_bps`` is the bar's ``spread_bps_close``; half of it is the touch.
    """
    half = max(0.0, spread_bps) / 2.0 / _BPS_DIVISOR
    long_pos = side == "long"
    # Entry long -> pay up; entry short -> sell down. Exit long -> sell down;
    # exit short -> cover up.
    pay_up = long_pos if is_entry else (not long_pos)
    factor = (1.0 + half) if pay_up else (1.0 - half)
    return raw_price * factor


def gross_pnl_usd(*, side: str, entry_price: float, exit_price: float, qty: float) -> float:
    """Gross (pre-fee) USD pnl for ``qty`` units. ``qty = notional / entry``."""
    if side == "long":
        return (exit_price - entry_price) * qty
    return (entry_price - exit_price) * qty


def round_trip_fee_usd(*, venue: str, entry_notional: float, exit_notional: float) -> float:
    """REAL fee for both legs (entry + exit), taker on each leg."""
    return real_fee_usd(venue, entry_notional) + real_fee_usd(venue, exit_notional)


def pnl_to_r(net_pnl_usd: float, *, denom: float = PNL_R_USD_DENOM) -> float:
    """Net USD pnl -> R units (clamped finite). ``denom`` = USD per 1R."""
    if denom <= 0.0:
        return 0.0
    return net_pnl_usd / denom
