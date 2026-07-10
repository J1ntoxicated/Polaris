"""Layer 3 — Symbol-cluster cap (BTC/ETH 40%, XAU+indices 50%, FX majors 60%).

Spec source:
- vault/30_components/layer-3-sizing-risk.md (Q3 cluster_id ≠ underlying_group_id)
- vault/10_decisions/ADR-005-sizing-formula-cell-routing.md (Symbol-Cluster Cap)

P0 = config-first (no learner-tunable cap). Membership maps are explicit:
the resolver maps ``underlying_group_id`` (or asset_class for XAU+indices) to
the cluster_id. Anything outside the configured clusters returns ``None``.
"""

from __future__ import annotations

from typing import Final

from polaris.core.sizing.schema import (
    CLUSTER_BTC_ETH_PCT,
    CLUSTER_EQUITY_BETA_TREND_PCT,
    CLUSTER_EQUITY_MEANREV_PCT,
    CLUSTER_FX_MAJORS_PCT,
    CLUSTER_XAU_INDICES_PCT,
    PositionRiskState,
    cluster_btc_eth_pct,
    cluster_equity_beta_trend_pct,
    cluster_equity_meanrev_pct,
    cluster_fx_majors_pct,
    cluster_xau_indices_pct,
)

__all__ = [
    "CLUSTER_DEFINITIONS",
    "cluster_remaining_pct",
    "cluster_used_pct",
    "resolve_cluster_definitions",
    "resolve_cluster_id",
]


_CRYPTO_BTC_ETH_MEMBERS: Final[frozenset[str]] = frozenset(
    {"crypto:BTC", "crypto:ETH"}
)

_FX_MAJOR_MEMBERS: Final[frozenset[str]] = frozenset(
    {
        "forex:EURUSD",
        "forex:GBPUSD",
        "forex:USDJPY",
        "forex:AUDUSD",
        "forex:USDCAD",
        "forex:USDCHF",
        "forex:NZDUSD",
    }
)

# asset_class membership (for XAU+indices cluster — multi-class pattern).
_XAU_INDICES_CLASSES: Final[frozenset[str]] = frozenset(
    {"metal", "metals", "indices", "index", "commodity"}
)
_XAU_TOKENS: Final[tuple[str, ...]] = ("XAU", "GOLD")

# §0a — equity mean-reversion sleeve membership (Alpaca sleeve, hard dependency
# #1). Any equity strategy_id NOT in this set routes to the beta/trend sleeve
# (the default equity bucket) — every equity signal now clusters to exactly one
# of the two sleeves (never None, unlike the old un-clustered equity behaviour).
_EQUITY_MEANREV_STRATEGY_IDS: Final[frozenset[str]] = frozenset(
    {"equity_bb_meanrev_15m", "connors_rsi2"}
)


CLUSTER_DEFINITIONS: Final[dict[str, float]] = {
    "crypto:BTC+ETH": CLUSTER_BTC_ETH_PCT,
    "cfd:XAU+INDICES": CLUSTER_XAU_INDICES_PCT,
    "cfd:FX_MAJORS": CLUSTER_FX_MAJORS_PCT,
    "equity:beta_trend": CLUSTER_EQUITY_BETA_TREND_PCT,
    "equity:meanrev": CLUSTER_EQUITY_MEANREV_PCT,
}


def resolve_cluster_definitions() -> dict[str, float]:
    """Cluster caps with env overrides applied (read at call time).

    Env-overridable for durability: ``POLARIS_CAP_CLUSTER_{BTC_ETH,XAU_INDICES,
    FX_MAJORS,EQUITY_BETA_TREND,EQUITY_MEANREV}_PCT``. Defaults are the high
    values in :mod:`schema` (DEMO data-collection — caps no longer bind at low
    position counts).
    """
    return {
        "crypto:BTC+ETH": cluster_btc_eth_pct(),
        "cfd:XAU+INDICES": cluster_xau_indices_pct(),
        "cfd:FX_MAJORS": cluster_fx_majors_pct(),
        "equity:beta_trend": cluster_equity_beta_trend_pct(),
        "equity:meanrev": cluster_equity_meanrev_pct(),
    }


def resolve_cluster_id(
    *,
    underlying_group_id: str,
    asset_class: str,
    symbol: str = "",
    strategy_id: str = "",
) -> str | None:
    """Map (underlying_group_id, asset_class, symbol, strategy_id) → cluster_id
    or None.

    P0 deterministic — does not consult any DB; caller supplies metadata.
    ``strategy_id`` defaults to "" so every pre-existing (non-equity) call site
    stays byte-identical (its branch is checked only after the crypto/FX
    early-returns, and non-equity asset classes never reach it either).
    """
    if underlying_group_id in _CRYPTO_BTC_ETH_MEMBERS:
        return "crypto:BTC+ETH"
    if underlying_group_id in _FX_MAJOR_MEMBERS:
        return "cfd:FX_MAJORS"
    cls = (asset_class or "").lower()
    if cls == "equity":
        if strategy_id in _EQUITY_MEANREV_STRATEGY_IDS:
            return "equity:meanrev"
        return "equity:beta_trend"
    sym = (symbol or "").upper()
    is_xau = any(token in sym for token in _XAU_TOKENS) or "metal" in cls or "gold" in cls
    if cls in _XAU_INDICES_CLASSES or is_xau:
        return "cfd:XAU+INDICES"
    return None


def cluster_used_pct(
    *,
    cluster_id: str,
    open_positions: list[PositionRiskState],
) -> float:
    """Sum ``open_risk_pct`` across positions whose ``cluster_id`` matches."""
    if not cluster_id:
        return 0.0
    total = 0.0
    for p in open_positions:
        if p.cluster_id == cluster_id:
            total += float(p.open_risk_pct)
    return total


def cluster_remaining_pct(
    *,
    cluster_id: str | None,
    open_positions: list[PositionRiskState],
) -> float | None:
    """Headroom remaining for the cluster.

    Returns None when ``cluster_id`` is unmapped → no cluster cap binding.
    Returns max(0, cap - used) otherwise.
    """
    if cluster_id is None:
        return None
    cap = resolve_cluster_definitions().get(cluster_id)
    if cap is None:
        return None
    used = cluster_used_pct(cluster_id=cluster_id, open_positions=open_positions)
    return max(0.0, cap - used)
