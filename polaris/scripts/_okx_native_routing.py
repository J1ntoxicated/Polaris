"""① OKX-NATIVE source-routing — top-N majors routed to OKX-native candles.

Split out of ``_yahoo_bars`` (≤500 LOC). DEMO/PAPER only. flow_not_block: this is
a bar-HISTORY source choice only — the live entry/exit price rides the WS quote
path (untouched). #21 regression guard: the OKX candles bucket is unchanged (storm
physically impossible) and ``POLARIS_OKX_NATIVE_TOP_N=0`` is the instant
kill-switch (full Yahoo routing back). N is bounded by intra-minute freshness, NOT
rate-limit (the bucket hard-ceils ≤10 req/s regardless of N).

These verified MAJORS are routed to the OKX-native candles path (fresher exchange
bars) instead of Yahoo ``BASE-USD`` (aggregation lag). The set is ORDERED by
liquidity (most-liquid first) so the env cap selects a stable top-N, and is a
SUBSET of the caller's verified-base set by construction (never an unverified
collision coin). DEFAULT cap = 0 (OFF) so the live default is byte-identical to the
pre-① Yahoo routing (strategy regression 0); opt-in via the env.
"""

from __future__ import annotations

import os
from typing import Final

__all__ = [
    "OKX_NATIVE_PREFERRED_ORDER",
    "OKX_NATIVE_TOP_N_DEFAULT",
    "OKX_NATIVE_TOP_N_ENV",
    "build_okx_native_preferred",
    "okx_symbol_base_quote",
    "read_okx_native_top_n",
]

OKX_NATIVE_TOP_N_ENV: Final[str] = "POLARIS_OKX_NATIVE_TOP_N"
# DEFAULT = 0 (OFF): opt-in only (e.g. 30 = top-30 majors OKX-native).
OKX_NATIVE_TOP_N_DEFAULT: Final[int] = 0

# Liquidity-ordered (most-liquid first) — the env cap takes a stable prefix.
OKX_NATIVE_PREFERRED_ORDER: Final[tuple[str, ...]] = (
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "TRX", "AVAX", "LINK",
    "DOT", "BCH", "LTC", "NEAR", "ICP", "ATOM", "XLM", "ETC", "FIL", "OP",
    "INJ", "AAVE", "TIA", "RENDER", "SEI", "ALGO", "HBAR", "CRV", "LDO", "SAND",
    "MANA", "AXS", "SHIB",
)


def read_okx_native_top_n() -> int:
    """``POLARIS_OKX_NATIVE_TOP_N`` (no-hardcode), default 0 (OFF); clamped ≥ 0."""
    raw = os.environ.get(OKX_NATIVE_TOP_N_ENV)
    if raw is None or raw.strip() == "":
        return OKX_NATIVE_TOP_N_DEFAULT
    try:
        n = int(raw.strip())
    except ValueError:
        return OKX_NATIVE_TOP_N_DEFAULT
    return max(0, n)


def build_okx_native_preferred(verified_bases: frozenset[str]) -> frozenset[str]:
    """Top-N ordered majors ∩ ``verified_bases`` (never an unverified coin)."""
    n = read_okx_native_top_n()
    chosen = OKX_NATIVE_PREFERRED_ORDER[:n]
    return frozenset(b for b in chosen if b in verified_bases)


def okx_symbol_base_quote(symbol: str) -> tuple[str, str]:
    """Parse an OKX symbol into ``(base, quote)`` the SAME way the Yahoo OKX
    branch does, so routing + cooldown-bypass can never disagree. An unparseable
    symbol → ``("", "")``. Pure / no I/O."""
    sym = symbol.upper()
    if "-" in sym:
        base, _, quote = sym.partition("-")
        return base, quote
    if sym.endswith(("USDT", "USDC")):
        return sym[:-4], sym[-4:]
    if sym.endswith("USD"):
        return sym[:-3], "USD"
    return "", ""
