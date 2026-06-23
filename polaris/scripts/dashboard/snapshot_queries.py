"""Polaris dashboard v1 — snapshot section queries (DB → typed row lists).

Per-section best-effort read helpers consumed by ``snapshot.collect_snapshot``.
All queries best-effort: missing tables / empty data return zero-defaults so the
dashboard never crashes a paper loop.

This module is now a thin FACADE: the section query functions live in cohesive
``snapshot_q_*`` modules (each ≤500 LOC) and are re-exported here so the original
import path — ``from polaris.scripts.dashboard.snapshot_queries import …`` — stays
stable for ``snapshot.py`` / ``snapshot_sections.py`` / ``snapshot_e2.py`` / the
web server / the test suite. ``snapshot`` re-exports ``_read_positions`` for tests
that reference it via the original path.
"""

from __future__ import annotations

from polaris.scripts.dashboard.snapshot_q_common import (
    DEFAULT_R_USD,
    EQUITY_BUCKET_SEC,
    EQUITY_LOOKBACK_SEC,
    EQUITY_MIN_BUCKET_SEC,
    EQUITY_TARGET_BUCKETS,
    GATE_FUNNEL_LOOKBACK_SEC,
    GPT_LOOKBACK_SEC,
    GPT_PRICE_PER_1K,
    GPT_TOKENS_PER_CALL,
    LEARNER_DELTA_LOOKBACK_SEC,
    MODEL_PRICE_PER_1K,
    SLIPPAGE_BPS_DIVISOR,
    _model_price,
    _now_s,
    _safe_query,
    _session_buckets,
    _session_start_ms,
    _strategy_label,
    _symbol_from_inst,
)
from polaris.scripts.dashboard.snapshot_q_equity import (
    DualEquityCurve,
    _build_dual_equity_curve,
    _build_equity_curve,
    _daily_realised_pnl,
    _drawdown_and_sharpe,
)
from polaris.scripts.dashboard.snapshot_q_gate_feed import (
    RECENT_GATE_EVENTS_N,
    _as_dict,
    _payload_detail,
    _payload_strategy_symbol_reason,
    _recent_gate_events,
)
from polaris.scripts.dashboard.snapshot_q_positions import (
    _cell_mult_lookup,
    _entry_price_lookup,
    _last_prices,
    _read_positions,
)
from polaris.scripts.dashboard.snapshot_q_reset import (
    _cadence_reason_split,
    _closed_since_reset,
    _rollup_metrics,
    _since_reset_rollup,
    _SinceResetRow,
    _strategy_since_reset,
)
from polaris.scripts.dashboard.snapshot_q_strategy import (
    _avg_r_by_strategy,
    _closed_position_r,
    _ClosedR,
    _strategy_stats,
    _ticker_stats,
)
from polaris.scripts.dashboard.snapshot_q_strategy_notes import (
    _one_line_description,
    _strategy_descriptions,
)
from polaris.scripts.dashboard.snapshot_q_streams import (
    ALPACA_EQUITY_PROBE_TTL_SEC,
    RECENT_CLOSED_PER_STREAM,
    _alpaca_account_equity,
    _AlpacaEquity,
    _fetch_alpaca_account,
    _per_stream_summary,
    _recent_closed_by_venue,
)

__all__ = [
    "ALPACA_EQUITY_PROBE_TTL_SEC",
    "DEFAULT_R_USD",
    "DualEquityCurve",
    "EQUITY_BUCKET_SEC",
    "EQUITY_LOOKBACK_SEC",
    "EQUITY_MIN_BUCKET_SEC",
    "EQUITY_TARGET_BUCKETS",
    "GATE_FUNNEL_LOOKBACK_SEC",
    "GPT_LOOKBACK_SEC",
    "GPT_PRICE_PER_1K",
    "GPT_TOKENS_PER_CALL",
    "LEARNER_DELTA_LOOKBACK_SEC",
    "MODEL_PRICE_PER_1K",
    "RECENT_CLOSED_PER_STREAM",
    "RECENT_GATE_EVENTS_N",
    "SLIPPAGE_BPS_DIVISOR",
    "_AlpacaEquity",
    "_ClosedR",
    "_SinceResetRow",
    "_alpaca_account_equity",
    "_as_dict",
    "_avg_r_by_strategy",
    "_build_dual_equity_curve",
    "_build_equity_curve",
    "_cadence_reason_split",
    "_cell_mult_lookup",
    "_closed_position_r",
    "_closed_since_reset",
    "_daily_realised_pnl",
    "_drawdown_and_sharpe",
    "_entry_price_lookup",
    "_fetch_alpaca_account",
    "_last_prices",
    "_model_price",
    "_now_s",
    "_one_line_description",
    "_payload_detail",
    "_payload_strategy_symbol_reason",
    "_per_stream_summary",
    "_read_positions",
    "_recent_closed_by_venue",
    "_recent_gate_events",
    "_rollup_metrics",
    "_safe_query",
    "_session_buckets",
    "_session_start_ms",
    "_since_reset_rollup",
    "_strategy_descriptions",
    "_strategy_label",
    "_strategy_since_reset",
    "_strategy_stats",
    "_symbol_from_inst",
    "_ticker_stats",
]
