"""Layer 6 — position excursion math (MFE / MAE in R units).

BUILD_SCHEMA prerequisite for precise-exit (#26): the close path must persist
the maximum favourable / adverse excursion a position reached over its life,
expressed in R units (price move ÷ ATR-in-USD risk unit). These are the raw
inputs a precise-exit policy (trailing harvest / loser timeout) and the
post-trade reflector learn from — they are *measurement only* and never gate
sizing or block an entry.

Pure + unit-testable. No I/O. Long and short are sign-symmetric:
- MFE = best move *in the direction of the trade* (favourable), >= 0
- MAE = worst move *against the trade* (adverse), <= 0

``peak_price`` is the highest mark seen, ``trough_price`` the lowest — both
direction-agnostic price extremes recorded by the tick loop. For a long the
favourable extreme is the peak and the adverse extreme is the trough; for a
short the roles flip. ``atr_usd`` is the per-R risk unit in price terms (the
same denominator the realised-PnL path uses: ``entry_price * atr_pct * 2``);
when it is non-positive we fall back to a tiny epsilon so the result is finite
rather than dividing by zero.
"""

from __future__ import annotations

from typing import Final

# Floor for the R denominator so excursion is always finite even when ATR is
# missing / degenerate. Matches ``real_pnl_r_from_fills`` (atr_usd floor 1e-6).
_ATR_USD_FLOOR: Final[float] = 1e-6


def compute_mfe_r(
    *, entry_price: float, peak_price: float, trough_price: float,
    side: str, atr_usd: float,
) -> float:
    """Maximum *favourable* excursion in R units (>= 0).

    Long: favourable extreme is the peak (price up). Short: the trough
    (price down). Clamped at 0.0 — a position that never moved in its
    favour has zero MFE, not a negative one.
    """
    atr = atr_usd if atr_usd > _ATR_USD_FLOOR else _ATR_USD_FLOOR
    favourable = (
        peak_price - entry_price if side == "long"
        else entry_price - trough_price
    )
    return max(0.0, favourable / atr)


def compute_mae_r(
    *, entry_price: float, peak_price: float, trough_price: float,
    side: str, atr_usd: float,
) -> float:
    """Maximum *adverse* excursion in R units (<= 0).

    Long: adverse extreme is the trough (price down). Short: the peak
    (price up). Clamped at 0.0 — a position that never moved against
    itself has zero MAE, not a positive one.
    """
    atr = atr_usd if atr_usd > _ATR_USD_FLOOR else _ATR_USD_FLOOR
    adverse = (
        trough_price - entry_price if side == "long"
        else entry_price - peak_price
    )
    return min(0.0, adverse / atr)


def compute_excursion_r(
    *, entry_price: float, peak_price: float, trough_price: float,
    side: str, atr_usd: float,
) -> tuple[float, float]:
    """Return ``(mfe_r, mae_r)`` together (MFE >= 0, MAE <= 0)."""
    return (
        compute_mfe_r(
            entry_price=entry_price, peak_price=peak_price,
            trough_price=trough_price, side=side, atr_usd=atr_usd,
        ),
        compute_mae_r(
            entry_price=entry_price, peak_price=peak_price,
            trough_price=trough_price, side=side, atr_usd=atr_usd,
        ),
    )


__all__ = [
    "compute_excursion_r",
    "compute_mae_r",
    "compute_mfe_r",
]
