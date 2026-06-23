"""Sizing / equity SSOT constants — Day 9 F12 fix.

Single source of truth for the demo-account starting equity used by:

* ``polaris.scripts._production_pipeline.EQUITY_USD_DEMO_DEFAULT`` —
  the equity passed into Layer 5 sizing.
* ``polaris.scripts.dashboard.snapshot.STARTING_CAPITAL`` —
  the base used to render DD / exposure / equity-curve in dashboard v1.

Before this fix the dashboard hard-coded ``STARTING_CAPITAL = 10_000`` while
the production pipeline used ``EQUITY_USD_DEMO_DEFAULT = 79_000``. That
mismatch produced bogus dashboard DD / exposure ratios (e.g. -127% DD on a
$3.5K loss because base was wrongly $10K instead of $130K).

Demo-account split (per CLAUDE.md + 2026-05-07 audit):

* OKX SPOT demo  : USD $79,000           (env: ``POLARIS_DEMO_STARTING_EQUITY_OKX``)
* Capital CFD    : A$78,000 ≈ USD $51,000 (env: ``POLARIS_DEMO_STARTING_EQUITY_CAPITAL``)
* Total          : USD $130,000          (env: ``POLARIS_DEMO_STARTING_EQUITY_TOTAL``)

Per-venue overrides resolve in priority order:
    1. Per-venue env var (``POLARIS_DEMO_STARTING_EQUITY_OKX|CAPITAL``)
    2. Total env var (``POLARIS_DEMO_STARTING_EQUITY_TOTAL``) — applied to total only
    3. Hard-coded constants below

The renderer reads ``demo_starting_equity_okx`` + ``demo_starting_equity_capital``
straight off ``DashboardSnapshot`` to produce the venue-split top-bar entry.
"""

from __future__ import annotations

import os
from typing import Final

__all__ = [
    "ALPACA_DEMO_STARTING_EQUITY_USD",
    "CAPITAL_DEMO_STARTING_EQUITY_USD",
    "OKX_DEMO_STARTING_EQUITY_USD",
    "TOTAL_DEMO_STARTING_EQUITY_USD",
    "demo_starting_equity_alpaca",
    "demo_starting_equity_capital",
    "demo_starting_equity_okx",
    "demo_starting_equity_total",
    "production_default_equity_usd",
]

OKX_DEMO_STARTING_EQUITY_USD: Final[float] = 79_000.0
CAPITAL_DEMO_STARTING_EQUITY_USD: Final[float] = 51_000.0
TOTAL_DEMO_STARTING_EQUITY_USD: Final[float] = (
    OKX_DEMO_STARTING_EQUITY_USD + CAPITAL_DEMO_STARTING_EQUITY_USD
)

# Alpaca paper-account starting-equity fallback (USD). Unlike OKX/Capital, the
# Alpaca paper account is funded at the venue, so the live ``/v2/account`` probe
# (snapshot_queries._alpaca_account_equity) is the display source of truth. But
# the stream-common R denominator (``R_budget`` in metrics.risk_unit) must be
# DEFINED even in the loop / tests where no probe runs — so this deterministic
# constant is the close-path / R-budget fallback (mirrors the OKX/Capital
# constants). ~$100k matches a typical Alpaca paper baseline; env-overridable.
ALPACA_DEMO_STARTING_EQUITY_USD: Final[float] = 100_000.0

_ENV_OKX: Final[str] = "POLARIS_DEMO_STARTING_EQUITY_OKX"
_ENV_CAPITAL: Final[str] = "POLARIS_DEMO_STARTING_EQUITY_CAPITAL"
_ENV_ALPACA: Final[str] = "POLARIS_DEMO_STARTING_EQUITY_ALPACA"
_ENV_TOTAL: Final[str] = "POLARIS_DEMO_STARTING_EQUITY_TOTAL"
_ENV_LEGACY_PRODUCTION: Final[str] = "POLARIS_EQUITY_USD"


def _read_float_env(name: str) -> float | None:
    """Parse a float env var. Returns ``None`` on missing or non-numeric.

    Codex round 1 nit fix: ``0.0`` is now a valid override (was silently
    treated as unset by the legacy ``or`` short-circuit).
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _split_default_ratio(total: float) -> tuple[float, float]:
    """Split a total across OKX / Capital using the default 79:51 ratio."""
    default_ratio_okx = OKX_DEMO_STARTING_EQUITY_USD / TOTAL_DEMO_STARTING_EQUITY_USD
    okx = total * default_ratio_okx
    cap = total - okx
    return okx, cap


def _resolve_equity_split() -> tuple[float, float, float]:
    """Single-pass resolution of (okx, capital, total) so the sum invariant
    ``okx + capital == total`` always holds.

    Codex F12 round 2 fix: the round-1 patch resolved each helper
    independently, which broke the sum invariant on mixed configurations
    (e.g. ``OKX=100000 + TOTAL=200000 + CAPITAL unset`` produced
    okx=100000, cap=78461, total=151000 — neither matching 200000 nor
    summing back to total). Single-pass resolution + explicit precedence:

      1. **Per-venue env (highest specificity)** wins. If both venues are
         set explicitly, ``total = okx + cap`` and ``TOTAL`` env is ignored
         (operator opted into explicit per-venue control).
      2. **Mixed (one per-venue + TOTAL)**: explicit venue + TOTAL anchor →
         the unset venue fills the gap so the sum holds (``cap = total - okx``).
         If the gap goes negative, the unset side clamps to 0 and the
         explicit venue wins (preserves operator intent).
      3. **TOTAL only**: split by the default 79:51 ratio.
      4. **Per-venue only (no TOTAL)**: unset side falls back to its
         hard-coded constant; ``total = okx + cap``.
      5. **No env**: hard-coded defaults.
    """
    okx_env = _read_float_env(_ENV_OKX)
    cap_env = _read_float_env(_ENV_CAPITAL)
    total_env = _read_float_env(_ENV_TOTAL)

    # Both per-venue set → operator explicit control, TOTAL ignored.
    if okx_env is not None and cap_env is not None:
        return okx_env, cap_env, okx_env + cap_env

    # Mixed: one per-venue + TOTAL → fill the gap from TOTAL.
    if okx_env is not None and total_env is not None:
        cap_value = max(0.0, total_env - okx_env)
        return okx_env, cap_value, okx_env + cap_value
    if cap_env is not None and total_env is not None:
        okx_value = max(0.0, total_env - cap_env)
        return okx_value, cap_env, okx_value + cap_env

    # TOTAL only → default ratio split.
    if total_env is not None:
        okx_value, cap_value = _split_default_ratio(total_env)
        return okx_value, cap_value, total_env

    # Per-venue only (no TOTAL) → explicit + default fallback for the other side.
    if okx_env is not None:
        cap_value = CAPITAL_DEMO_STARTING_EQUITY_USD
        return okx_env, cap_value, okx_env + cap_value
    if cap_env is not None:
        okx_value = OKX_DEMO_STARTING_EQUITY_USD
        return okx_value, cap_env, okx_value + cap_env

    # No env → hard-coded defaults.
    return (
        OKX_DEMO_STARTING_EQUITY_USD,
        CAPITAL_DEMO_STARTING_EQUITY_USD,
        TOTAL_DEMO_STARTING_EQUITY_USD,
    )


def demo_starting_equity_okx() -> float:
    """OKX SPOT demo starting equity (USD)."""
    return _resolve_equity_split()[0]


def demo_starting_equity_capital() -> float:
    """Capital CFD demo starting equity (USD-equivalent)."""
    return _resolve_equity_split()[1]


def demo_starting_equity_alpaca() -> float:
    """Alpaca paper-account starting-equity FALLBACK (USD).

    Deterministic constant for the stream-common R denominator when the live
    ``/v2/account`` probe is unavailable (loop / tests). Env-overridable via
    ``POLARIS_DEMO_STARTING_EQUITY_ALPACA``. NOT part of the OKX/Capital/total
    sum invariant — Alpaca is funded at the venue, so its baseline is the probe
    when present and this constant otherwise.
    """
    env = _read_float_env(_ENV_ALPACA)
    if env is not None:
        return env
    return ALPACA_DEMO_STARTING_EQUITY_USD


def demo_starting_equity_total() -> float:
    """Total demo starting equity (USD).

    Codex F12 round 2 fix: ``demo_starting_equity_total()`` always equals
    ``demo_starting_equity_okx() + demo_starting_equity_capital()`` regardless
    of envvar configuration (sum invariant). Resolution rules in
    ``_resolve_equity_split`` docstring.
    """
    return _resolve_equity_split()[2]


def production_default_equity_usd() -> float:
    """Equity passed into Layer 5 sizing.

    Honours the legacy ``POLARIS_EQUITY_USD`` override (used by smoke +
    historical pipeline tests) before falling back to the OKX SPOT venue
    starting equity. The pipeline is single-venue per call so we expose
    OKX (Track A) as the default — Capital path uses the venue-aware
    starting equity wired into Layer 5 via ``equity_usd_for_venue``.
    """
    legacy = _read_float_env(_ENV_LEGACY_PRODUCTION)
    if legacy is not None:
        return legacy
    return demo_starting_equity_okx()
