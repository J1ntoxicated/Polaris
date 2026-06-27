"""Strategy ABC + RawSignal + StrategyMetadata + MarketView.

Spec source: vault/10_decisions/ADR-008-7-strategies-signal-generator-role.md.

Each strategy in this package is a **signal generator only** — it returns
``RawSignal | None`` from ``generate_raw_signal(market_view)``. Lifecycle
decisions (entry / size / exit / swap) belong to the AI gate pipeline
(Layer 2) and the live-recalc engine (Layer 6).

Constructors:
  - ``Strategy()`` — caller passes an instance to the worker.
  - ``Strategy.metadata`` — class attribute (frozen dataclass).
  - ``generate_raw_signal(market_view)`` — pure function; no side effects.

P0 = frozen v1 params (per ADR-008). Learner network (Layer 5) will tune
these in P1+ via ``cell_routing_mult`` and behavioural overlay.
"""

from __future__ import annotations

import math
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Final, Literal

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    """Immutable strategy descriptor (ADR-008)."""

    strategy_id: str
    timeframe: str  # "1m" / "5m" / "15m" / "1H"
    warmup_bars: int
    max_positions: int
    gross_cap: float
    per_symbol_cap: float
    expected_holding_bars: int
    asset_class: str  # "spot" / "fx" / "index" / "commodity"
    venue: str  # "okx" / "capital"
    correlation_group_id: str
    # product_class is the venue's instrument family (spot | cfd | equity). It
    # is the 1st-class routing dimension the StreamConfig SSOT keys on. Defaults
    # to "" so existing constructors stay source-compatible; ``asset_class`` is
    # preserved unchanged (it currently doubles as product_class — design §2.2).
    product_class: str = ""
    # profit_target_r (R-units): when set, the precise-exit engine HARVESTS the
    # position the moment unrealised PnL reaches +this many R — a take-profit for
    # mean-reversion strategies whose edge is a bounded revert-to-mean (a BB fade
    # enters at the band extreme and targets the middle ≈ 2σ ≈ 2 ATR ≈ 1 R), NOT
    # let-winners-run. None (default) keeps the trend exit (ATR-trail + MFE harvest
    # FSM, no fixed target) — all existing strategies are unchanged. EXPECTANCY: a
    # per-position close target, never a size dampen / entry block / halt.
    profit_target_r: float | None = None
    # hold_overnight: EOD-flatten opt-OUT. The DEFAULT (False) flattens every
    # position this strategy opens before its venue's market close (Jin's
    # '청산이 기본 베이스' — liquidation is the base case). A strategy whose edge is
    # genuinely multi-day (swing) sets True to KEEP its positions overnight. NOT
    # ``expected_holding_bars`` (an advisory exit-timing hint, not a keep contract).
    # Position lifecycle, never an entry block / size dampen (flow_not_block). OKX
    # crypto is 24/7 and EOD-exempt regardless of this flag.
    hold_overnight: bool = False
    # maker_no_fill_cancel: when True, a post-only entry that exhausts the bounded
    # reprice/repost loop unfilled is CANCELLED/skipped instead of falling back to
    # a taker market order (#77 weekend thin-book maker). The strategy's edge IS
    # the passive deep-bid fill — a missed fill is 0 realised cost, never a forced
    # taker. The DEFAULT (False) keeps the legacy taker-fallback so no existing
    # entry is ever blocked (flow_not_block). Routing only — never a size/entry
    # block; applies solely to the OKX prefer-maker post-only path.
    maker_no_fill_cancel: bool = False
    # dispatch_eligible: the SINGLE source of truth for whether the LIVE bar
    # pipeline invokes ``generate_raw_signal`` on this strategy. The dispatch set
    # is DERIVED from ``STRATEGY_REGISTRY`` filtered on this flag (see
    # ``_production_tick._all_strategies``), so a registered strategy dispatches
    # iff its flag is True — the prior hand-synced 19-id literal could (and did)
    # DRIFT from the registry (silent INERT: registered ≠ dispatched). The DEFAULT
    # (True) keeps every validated strategy dispatching unchanged. Set False to
    # KILL a strategy from NEW-ENTRY dispatch (fee-fatal / unvalidated): it stays
    # REGISTERED (module + open-position close path preserved — KILL is no-emit,
    # NOT module removal; the recalc/exit loop still closes its open positions) but
    # ``generate_raw_signal`` is never called, so it opens no new position. NOT a
    # size dampen / halt / block (flow_not_block) — an excluded strategy simply
    # does not emit; it never cuts another strategy's size.
    dispatch_eligible: bool = True


@dataclass(frozen=True, slots=True)
class RawSignal:
    """Pre-validation signal emitted by a strategy.

    Spec source: ADR-008 §RawSignal Schema.
    """

    signal_id: str
    strategy_id: str
    symbol: str
    side: Literal["long", "short"]
    strength: float
    sizing_hint: float
    ttl_bars: int
    thesis_tag: str
    correlation_group: str
    venue_constraints: dict[str, Any] = field(default_factory=dict)
    created_at_bar: int = 0
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BarView:
    """Lightweight bar projection used by strategies (subset of canonical Bar)."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    notional_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class AltDataView:
    """STRATEGY-VISIBLE alt-data numerics (additive SIGNAL context).

    The load-bearing numeric alt-data fields the vault flags as edge-relevant,
    mapped from the ``AltDataCache`` snapshot for the symbol's underlying group
    by ``build_real_market_view``. Every field defaults to ``None`` = NEUTRAL
    no-op, so a stale / absent / keyless cache reads as neutral and NEVER drives
    a decision. These are SIGNAL fields for new-edge discovery + the entry probe
    ensemble (ADR-013) — never a block / throttle / size-cut. No strategy reads
    them yet, so populating them is byte-identical for every existing strategy.
    """

    funding_rate: float | None = None  # mean OKX perp funding (longs pay shorts >0)
    open_interest: float | None = None  # summed OKX perp open interest (contracts)
    oi_change_24h: float | None = None  # 24h OI delta (None — OKX public has none)
    cot_net_spec_pctile: float | None = None  # CFTC large-spec net pctile (0..1)
    vix: float | None = None  # CBOE VIX level (macro risk-off)
    crypto_fear_greed: float | None = None  # 0=extreme fear .. 100=extreme greed
    hy_spread: float | None = None  # ICE BofA US HY OAS (bps)
    # PER-SYMBOL funding (weekend_funding_capitulation_maker): the funding rate
    # for THIS symbol's perp (not the group mean above), plus that perp's p10
    # threshold from funding-rate-history. A current funding <= p10 (and < 0) =
    # extreme-negative crowded-short capitulation. Both default None = NEUTRAL
    # (a symbol with no funding row reads neutral → the strategy no-emits).
    funding_rate_symbol: float | None = None  # THIS symbol's perp funding rate
    funding_rate_p10: float | None = None  # THIS symbol's perp funding p10 (history)

    def is_neutral(self) -> bool:
        """True when no field carries a value (a no-op view)."""
        return (
            self.funding_rate is None
            and self.open_interest is None
            and self.oi_change_24h is None
            and self.cot_net_spec_pctile is None
            and self.vix is None
            and self.crypto_fear_greed is None
            and self.hy_spread is None
            and self.funding_rate_symbol is None
            and self.funding_rate_p10 is None
        )


@dataclass(frozen=True, slots=True)
class MarketView:
    """Bundle of indicators a strategy needs for ``generate_raw_signal``.

    Strategies pull from this read-only snapshot — no DB calls. The
    ``running-paper-loop`` orchestrator (Day 5) builds the view per tick.
    """

    symbol: str
    venue: str
    timeframe: str
    bars: list[BarView]  # most recent last
    last_price: float
    spread_bps: float
    atr_pct: float = 0.0  # 24h ATR % (for liquidity floor / signal sizing)
    volume_z: float = 0.0  # rolling vol z-score (volume_burst)
    rsi_14: float | None = None
    bb_lower: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    ma_200: float | None = None
    adx_14: float | None = None
    momentum_20bar: float | None = None  # tsmom
    donchian_high_40: float | None = None
    donchian_low_40: float | None = None
    donchian_high_30: float | None = None
    donchian_low_30: float | None = None
    session_open_price: float | None = None  # session_breakout
    session_open_ts: int | None = None
    session_atr: float | None = None
    is_session_open_window: bool = False
    prev_close: float | None = None  # equity gap_go (prior session close)
    gap_pct: float | None = None  # equity gap_go ((open - prev_close)/prev_close)
    # asset_class-aware emphasis (additive context; default None so the 7
    # strategies + every existing caller are byte-identical). Populated per
    # asset_class by build_real_market_view: ema20/ema50/ema_cross for FX & index
    # (trend emphasis); trend_efficiency (Kaufman ER) for commodity. crypto keeps
    # its momentum_20bar emphasis and leaves these None. Read by the regime layer
    # + future per-ticker logic; no strategy reads them (no behavior change).
    ema_20: float | None = None
    ema_50: float | None = None
    ema_cross: float | None = None  # +1 fast>slow / -1 fast<slow / 0 equal
    trend_efficiency: float | None = None  # Kaufman ER over the window (0..1)
    # STRATEGY-VISIBLE alt-data numerics (funding / OI / COT / VIX / fear-greed /
    # HY). Additive SIGNAL context — default NEUTRAL (all None), so every existing
    # strategy + caller is byte-identical. Populated per group by
    # build_real_market_view from the AltDataCache snapshot; stale/absent/keyless
    # → neutral no-op. No strategy reads it yet (probe-ensemble consumer is later).
    altdata: AltDataView = field(default_factory=AltDataView)
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers (shared by strategies)
# ---------------------------------------------------------------------------


COLD_START_NEUTRAL_STRENGTH: Final[float] = 0.5


def make_signal_id() -> str:
    return uuid.uuid4().hex


def is_finite(value: float | None) -> bool:
    if value is None:
        return False
    return math.isfinite(value)


# ---------------------------------------------------------------------------
# BaseStrategy ABC
# ---------------------------------------------------------------------------


class BaseStrategy(ABC):
    """Signal generator interface (ADR-008)."""

    metadata: StrategyMetadata  # subclass class attribute

    @abstractmethod
    def generate_raw_signal(self, market_view: MarketView) -> RawSignal | None:
        """Return a ``RawSignal`` or ``None`` if no trigger this bar."""

    # default convenience: subclasses can override for special venues.
    def warmup_ok(self, market_view: MarketView) -> bool:
        return len(market_view.bars) >= self.metadata.warmup_bars


__all__ = [
    "AltDataView",
    "BarView",
    "BaseStrategy",
    "COLD_START_NEUTRAL_STRENGTH",
    "MarketView",
    "RawSignal",
    "StrategyMetadata",
    "is_finite",
    "make_signal_id",
]
