"""P0a variant generator — bounded-numeric config-variants of strategies.

OFFLINE ONLY. A variant is a DYNAMIC SUBCLASS of an existing
:class:`~polaris.strategies.base.BaseStrategy` that overrides only the
design-verified VARYABLE class attributes (entry-trigger thresholds + exit-
timing TTL). Because the strategies read those knobs via ``self.<attr>`` (with
the class default == the frozen module constant), an EMPTY override reproduces
byte-identical baseline behavior (behavior-0).

This is the ONLY new behavior. It never touches the live loop, sizing / T4
chain, gate thresholds, or any LOCKED (precomputed fixed-window) param. The
``strategies=`` seam on :class:`ReplayEngine` (STEP 1) carries these instances.
"""

from __future__ import annotations

import itertools
from typing import Any, Protocol, cast, runtime_checkable

from polaris.core.evolve.param_bounds import PARAM_BOUNDS
from polaris.strategies.base import BaseStrategy

# Total variant cap per grid enumeration (multiple-testing honesty: a 2-3 knob
# x 3-point grid stays well under this; the cap guards pathological products).
GRID_TOTAL_CAP = 200


@runtime_checkable
class StrategyVariant(Protocol):
    """A :class:`BaseStrategy` instance tagged with its variant identity.

    ``variant_id`` is a stable hash-free human-readable id
    (``"<strategy_id>|k=v,..."`` sorted by key); ``param_overrides`` is the
    exact override dict that produced it (the varied knobs only).
    """

    variant_id: str
    param_overrides: dict[str, float]

    def generate_raw_signal(self, market_view: Any) -> Any: ...


def _format_value(value: float) -> str:
    """Render a grid value stably (ints without a trailing ``.0``)."""
    if isinstance(value, bool):  # defensive: bool is an int subclass
        return str(value)
    if isinstance(value, int):
        return str(value)
    # float: rstrip a pure-integer float to "N" else keep repr.
    if value == int(value):
        return f"{value:.1f}"  # e.g. 2.0 -> "2.0" (stable, distinguishes int 2)
    return repr(value)


def _build_variant_id(strategy_id: str, overrides: dict[str, float]) -> str:
    """Order-independent, deterministic id for an override dict."""
    parts = [f"{k}={_format_value(overrides[k])}" for k in sorted(overrides)]
    return f"{strategy_id}|{','.join(parts)}"


def make_variant(
    base_cls: type[BaseStrategy], param_overrides: dict[str, float]
) -> StrategyVariant:
    """Build a variant instance of ``base_cls`` with the given overrides.

    ``param_overrides`` keys MUST be VARYABLE class attributes registered in
    ``PARAM_BOUNDS`` for this strategy (raises ``ValueError`` otherwise — a
    typo or a LOCKED param must never silently no-op). An empty dict yields a
    behavior-0 default instance.
    """
    strategy_id = base_cls.metadata.strategy_id
    allowed = set(PARAM_BOUNDS.get(strategy_id, {}))
    unknown = set(param_overrides) - allowed
    if unknown:
        raise ValueError(
            f"{strategy_id}: unknown/locked variant param(s) {sorted(unknown)}; "
            f"allowed={sorted(allowed)}"
        )
    for name in param_overrides:
        if not hasattr(base_cls, name):
            raise ValueError(
                f"{strategy_id}: '{name}' is not a class attribute on "
                f"{base_cls.__name__} (refactor missing)"
            )

    variant_id = _build_variant_id(strategy_id, param_overrides)
    # Dynamic subclass carrying the overrides as class attributes + identity.
    namespace: dict[str, Any] = dict(param_overrides)
    namespace["variant_id"] = variant_id
    namespace["param_overrides"] = dict(param_overrides)
    subclass = type(f"{base_cls.__name__}Variant", (base_cls,), namespace)
    instance = subclass()
    # subclass instance IS-A BaseStrategy AND satisfies the StrategyVariant
    # Protocol (variant_id / param_overrides / generate_raw_signal present).
    return cast(StrategyVariant, instance)


def enumerate_grid(
    base_cls: type[BaseStrategy], *, cap: int = GRID_TOTAL_CAP
) -> tuple[list[StrategyVariant], bool]:
    """Enumerate the full PARAM_BOUNDS grid for ``base_cls`` (cartesian product).

    Returns ``(variants, truncated)``. ``truncated`` is True iff the full grid
    exceeded ``cap`` and was cut (NO silent cap — the caller logs / persists
    ``trials_searched`` honestly). Per-param point count is already <=3 by the
    PARAM_BOUNDS contract.
    """
    strategy_id = base_cls.metadata.strategy_id
    grid = PARAM_BOUNDS.get(strategy_id, {})
    if not grid:
        return ([], False)
    names = list(grid.keys())
    point_lists = [grid[n] for n in names]
    combos = itertools.product(*point_lists)
    variants: list[StrategyVariant] = []
    truncated = False
    for combo in combos:
        if len(variants) >= cap:
            truncated = True
            break
        overrides = dict(zip(names, combo, strict=True))
        variants.append(make_variant(base_cls, overrides))
    return (variants, truncated)


__all__ = [
    "GRID_TOTAL_CAP",
    "StrategyVariant",
    "enumerate_grid",
    "make_variant",
]
