"""Shared OKX-liquid top-N symbol roster for the wave1 1D OKX strategies.

WHY (Jin 2026-06-27 "진입이 너무 안돼 → 늘려"): the four verified 1D OKX
strategies (okx_donchian_55_breakout / tsmom_12_1_multiasset /
macd_ema_trend_pullback / donchian_turtle_breakout) each pinned their OKX leg to
a hardcoded ~5-symbol frozenset (BTC/ETH/SOL/BNB/XRP). On a daily horizon that is
~1 trigger-check/symbol/day × 5 ≈ near-zero entry opportunity → live emit 0.

WHAT this is: the REAL liquidity gate already lives upstream — the Layer 0
``rank_active_universe`` → ``get_focus_targets`` active set only ever hands a
strategy OKX symbols that cleared the top-N liquidity rank, and the dispatch
matches ``metadata.venue == "okx"`` (so equity-ETF legs stay venue-inert). This
module widens the strategy-side leg from 5 hardcoded names to the OKX
USDT-quote liquid major+midcap roster, sliced to the top-N most-liquid by
``POLARIS_STRAT_SYMBOL_TOP_N`` (default 60). A symbol must therefore be in BOTH
the upstream liquid active set AND this top-N roster to emit = exactly "OKX
liquidity top-N". flow_not_block: this is a POSITIVE-MATCH set — the strategy
emits only on its own roster and never blocks any other strategy's emit; the
top-N slice keeps the most-liquid names and drops the thin-alt tail (no
indiscriminate 213-wide admit → no illiquid-whipsaw).

The roster is ordered by descending OKX spot liquidity, so ``roster[:N]`` keeps
the most-liquid N. ``N`` is env-tunable (no magic number).
"""

from __future__ import annotations

import os
from typing import Final

STRAT_SYMBOL_TOP_N_ENV: Final[str] = "POLARIS_STRAT_SYMBOL_TOP_N"
STRAT_SYMBOL_TOP_N_DEFAULT: Final[int] = 60

# OKX SPOT USDT-quote liquid major+midcap roster, ordered by descending
# liquidity. ~80 names so the default-60 slice keeps headroom above the cut and
# the thin-alt tail is dropped (illiquid-whipsaw guard). USDT-quote only (the OKX
# track A trade quote). Membership ∩ upstream liquid-active-set = the real gate.
OKX_LIQUID_ROSTER: Final[tuple[str, ...]] = (
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "XRP-USDT",
    "BNB-USDT",
    "DOGE-USDT",
    "ADA-USDT",
    "TRX-USDT",
    "AVAX-USDT",
    "LINK-USDT",
    "TON-USDT",
    "DOT-USDT",
    "SUI-USDT",
    "LTC-USDT",
    "BCH-USDT",
    "NEAR-USDT",
    "APT-USDT",
    "POL-USDT",
    "UNI-USDT",
    "ICP-USDT",
    "ETC-USDT",
    "XLM-USDT",
    "FIL-USDT",
    "ATOM-USDT",
    "HBAR-USDT",
    "ARB-USDT",
    "OP-USDT",
    "INJ-USDT",
    "AAVE-USDT",
    "RENDER-USDT",
    "IMX-USDT",
    "STX-USDT",
    "GRT-USDT",
    "WIF-USDT",
    "PEPE-USDT",
    "SHIB-USDT",
    "TIA-USDT",
    "SEI-USDT",
    "LDO-USDT",
    "ALGO-USDT",
    "S-USDT",
    "MKR-USDT",
    "RUNE-USDT",
    "FLOW-USDT",
    "EGLD-USDT",
    "SAND-USDT",
    "AXS-USDT",
    "MANA-USDT",
    "GALA-USDT",
    "CRV-USDT",
    "SNX-USDT",
    "DYDX-USDT",
    "COMP-USDT",
    "ENS-USDT",
    "1INCH-USDT",
    "CHZ-USDT",
    "ZRX-USDT",
    "BAT-USDT",
    "KSM-USDT",
    "WLD-USDT",
    "JUP-USDT",
    "PYTH-USDT",
    "ORDI-USDT",
    "JTO-USDT",
    "BLUR-USDT",
    "ENA-USDT",
    "STRK-USDT",
    "MEME-USDT",
    "BONK-USDT",
    "FLOKI-USDT",
    "GMT-USDT",
    "APE-USDT",
    "DASH-USDT",
    "ZEC-USDT",
    "EOS-USDT",
    "NEO-USDT",
    "IOTA-USDT",
    "QTUM-USDT",
    "WAVES-USDT",
    "KAVA-USDT",
)


def _resolve_top_n(n: int | None) -> int:
    """Resolve top-N from arg → env → default, clamped to [0, len(roster)]."""
    if n is None:
        raw = os.environ.get(STRAT_SYMBOL_TOP_N_ENV)
        if raw is not None:
            try:
                n = int(raw)
            except ValueError:
                n = STRAT_SYMBOL_TOP_N_DEFAULT
        else:
            n = STRAT_SYMBOL_TOP_N_DEFAULT
    return max(0, min(n, len(OKX_LIQUID_ROSTER)))


def okx_liquid_top_n(n: int | None = None) -> frozenset[str]:
    """The top-N most-liquid OKX USDT-quote symbols as a flow-safe match set.

    ``n`` resolves arg → ``POLARIS_STRAT_SYMBOL_TOP_N`` env → default 60, clamped
    to the roster length. Slices the liquidity-ordered roster so the most-liquid N
    are kept and the thin-alt tail is dropped (positive match, never a block).
    """
    return frozenset(OKX_LIQUID_ROSTER[: _resolve_top_n(n)])


__all__ = [
    "OKX_LIQUID_ROSTER",
    "STRAT_SYMBOL_TOP_N_DEFAULT",
    "STRAT_SYMBOL_TOP_N_ENV",
    "okx_liquid_top_n",
]
