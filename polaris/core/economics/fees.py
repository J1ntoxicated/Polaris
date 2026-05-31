"""Venue fee schedules — real vs demo (pure functions, no I/O).

Single source of truth for per-fill trading fees so accounting (the dashboard
dual-equity curve + confidence metrics) and any future trading-logic import the
SAME schedule instead of re-deriving bps constants ad hoc.

Why two schedules
-----------------
The OKX **demo** account charges a flat **0.70% (70 bps) taker = maker**,
confirmed via ``/api/v5/account/trade-fee`` (Lv1) + live fills (forensic
2026-05-31). That is the venue's own demo billing — NOT a Polaris bug — and it
drained the demo equity almost entirely on fees alone. Because demo maker ==
taker, the maker tier is meaningless on demo.

The **real** OKX SPOT Lv1 schedule is **0.10% taker / 0.08% maker** (official).
So OKX demo is a **7x fee penalty** vs real (0.70% / 0.10% = 7.0). The go-live
confidence signal must therefore evaluate the bot under the REAL schedule, not
the punitive demo one — see ``real_fee_usd`` (used by the real-fee-net equity
curve) vs ``demo_fee_usd`` (mirrors the stored ``fills.fee_usd`` demo drain).

Capital CFD demo reports ``fee=0``; the project uses ``COST_BPS_CAPITAL`` (3 bps
per leg) as the implicit spread/financing proxy — reused here as the Capital
real proxy so the cost model stays centralized. Alpaca equity is
commission-free (0 bps).

These are PURE helpers: no DB, no network, no global state. Trading behavior is
unchanged by importing this module — it is accounting/economics only.
"""

from __future__ import annotations

import math
from typing import Final

from polaris.core.learners._primitives import COST_BPS_CAPITAL

__all__ = [
    "ALPACA_REAL_BPS",
    "CAPITAL_REAL_BPS",
    "DEMO_VS_REAL_OKX_PENALTY",
    "OKX_DEMO_FEE_BPS",
    "OKX_REAL_MAKER_BPS",
    "OKX_REAL_TAKER_BPS",
    "demo_fee_usd",
    "real_fee_usd",
]

# --- Real schedules (per fill leg, bps of notional) ------------------------
OKX_REAL_TAKER_BPS: Final[float] = 10.0   # official OKX SPOT Lv1 taker = 0.10%
OKX_REAL_MAKER_BPS: Final[float] = 8.0    # official OKX SPOT Lv1 maker = 0.08%
# Capital CFD demo reports fee=0 → reuse the project's per-leg implicit-cost
# proxy (COST_BPS_CAPITAL = 3.0 bps) as the real proxy. Centralized here so the
# fee model is one source of truth.
CAPITAL_REAL_BPS: Final[float] = COST_BPS_CAPITAL
ALPACA_REAL_BPS: Final[float] = 0.0       # commission-free equities

# --- Demo schedule (per fill leg, bps of notional) -------------------------
OKX_DEMO_FEE_BPS: Final[float] = 70.0     # OKX demo flat 0.70% taker = maker

# 0.70% / 0.10% = 7.0 → OKX demo is a 7x fee penalty vs real taker.
DEMO_VS_REAL_OKX_PENALTY: Final[float] = OKX_DEMO_FEE_BPS / OKX_REAL_TAKER_BPS

_BPS_DIVISOR: Final[float] = 10_000.0


def _bps_to_usd(bps: float, notional_usd: float) -> float:
    """``bps`` of ``|notional|`` in USD; 0.0 for non-finite / zero notional."""
    if not math.isfinite(notional_usd):
        return 0.0
    return bps / _BPS_DIVISOR * abs(notional_usd)


def real_fee_usd(venue: str, notional_usd: float, is_maker: bool = False) -> float:
    """REAL per-leg fee in USD for one fill of ``notional_usd`` on ``venue``.

    OKX uses the real taker/maker schedule (10 / 8 bps). Capital uses the 3 bps
    per-leg proxy (maker flag is a no-op — the proxy has no maker tier). Alpaca
    is commission-free. An unknown venue falls back to the OKX real taker bps so
    a new venue is never silently treated as free.
    """
    v = venue.lower()
    if v == "okx":
        bps = OKX_REAL_MAKER_BPS if is_maker else OKX_REAL_TAKER_BPS
    elif v == "capital":
        bps = CAPITAL_REAL_BPS
    elif v == "alpaca":
        bps = ALPACA_REAL_BPS
    else:
        bps = OKX_REAL_TAKER_BPS
    return _bps_to_usd(bps, notional_usd)


def demo_fee_usd(venue: str, notional_usd: float) -> float:
    """DEMO per-leg fee in USD — the schedule the demo venue ACTUALLY charged.

    OKX demo bills a flat 0.70% (70 bps) taker = maker, so there is no maker
    distinction. Capital / Alpaca demo report fee=0, so the real proxy stands in
    (3 bps Capital, 0 Alpaca). Unknown venue falls back to the OKX demo bps.
    """
    v = venue.lower()
    if v == "okx":
        bps = OKX_DEMO_FEE_BPS
    elif v == "capital":
        bps = CAPITAL_REAL_BPS
    elif v == "alpaca":
        bps = ALPACA_REAL_BPS
    else:
        bps = OKX_DEMO_FEE_BPS
    return _bps_to_usd(bps, notional_usd)
