"""Replay value objects — config in, result out (pure dataclasses, no I/O).

Spec: ``.claude/plans/p1_replay_benchmark_harness_2026-05-31.md`` §Interface.

``ReplayResult`` is the single carrier consumed by the benchmark gate +
dashboard read-model. All fields are deterministic functions of
(``ReplayConfig`` + the seeded live snapshot) so two runs of the same config
produce byte-identical results (TDD #1 determinism).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ReplayConfig",
    "ReplayResult",
    "ReplayTrade",
]


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    """Inputs that fully determine a replay run.

    ``instrument_ids`` selects which ``bars`` rows to replay (``venue:symbol``
    keys, e.g. ``okx:BTC-USDT``). ``start_ts`` / ``end_ts`` (inclusive, seconds)
    bound the bar window; ``None`` = unbounded on that side. ``fee_model`` is
    ``"okx_real"`` (the only schedule the go-live signal evaluates under).
    ``starting_equity`` seeds the equity curve + sizing notional.
    """

    instrument_ids: tuple[str, ...]
    bar_interval: str = "1H"
    start_ts: int | None = None
    end_ts: int | None = None
    fee_model: str = "okx_real"
    starting_equity: float = 10_000.0
    # Sandbox seed comes from this live DB (read-only ATTACH). Kept on the config
    # so a run is fully reproducible from the dataclass alone.
    live_db_path: str = "data/polaris_live.sqlite"


@dataclass(frozen=True, slots=True)
class ReplayTrade:
    """One closed round-trip trade produced by the replay engine."""

    entry_ts: int
    exit_ts: int
    symbol: str
    strategy: str
    regime: str
    side: str  # "long" | "short"
    entry_price: float  # fill price incl. half-spread slippage (next-bar open)
    exit_price: float  # fill price incl. half-spread slippage (close)
    notional: float
    gross_pnl_usd: float
    rt_fee_usd: float  # round-trip (both legs) REAL fee in USD
    net_pnl_usd: float  # gross_pnl_usd - rt_fee_usd
    pnl_r: float  # net pnl in R units (PNL_R_USD_DENOM)
    exit_reason: str


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Output of a replay run — ordered trades + equity curve + metrics.

    ``equity_curve_real_fee_net`` is a list of ``(ts, equity)`` points sharing
    the baseline clock (same start/end/interval). ``metrics`` keys:
    ``net_pnl``, ``sharpe``, ``max_dd``, ``win_rate``, ``profit_factor``,
    ``turnover``, ``fee_drag_real_r``, ``n``.
    """

    trades: tuple[ReplayTrade, ...]
    equity_curve_real_fee_net: tuple[tuple[int, float], ...]
    metrics: dict[str, float] = field(default_factory=dict)
