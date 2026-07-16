"""capital_macro_riskoff_catalyst — shadow-first emit tagger (P3 promotion).

DEMO/PAPER · aggressive · flow_not_block. ``capital_macro_riskoff_catalyst`` is a
NEW-FIRING strategy (vix/hy_spread macro EVENT entry — see the strategy module
docstring): it is registered in ``STRATEGY_REGISTRY`` but stays
``dispatch_eligible=False``, so ``_all_strategies()`` never calls its
``generate_raw_signal`` from the live dispatch loop and it can never open a
position. This module is the SHADOW OBSERVATION path — it calls the SAME
``generate_raw_signal`` on the SAME ``MarketView`` the live GOLD/1H dispatch
already builds, and logs ONE ``gate_shadow_events`` row per (venue, symbol,
bar) — sign/strength only, never sizing/entry/exit (behavior-0, mirrors
``tsmom_literature_shadow.py``'s pattern/table/wiring shape).

This is pure EVIDENCE ACCUMULATION toward a future promotion decision
(dispatch_eligible flip), never a live trading signal.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from polaris.core.pipeline.gate_state import GATE_STRATEGY_SIGNAL
from polaris.storage.db_writer import DBWriter, dbwriter_enabled
from polaris.strategies.base import MarketView
from polaris.strategies.capital_macro_riskoff_catalyst import (
    CapitalMacroRiskoffCatalystStrategy,
)
from polaris.strategies.gold_breakout_1h import SUPPORTED_SYMBOLS, _norm_symbol

logger = logging.getLogger(__name__)

__all__ = ["log_capital_macro_riskoff_shadow"]

# Shared by both the direct-conn and db_writer-submitted paths so the two
# branches are STRUCTURALLY guaranteed to issue the identical statement
# (mirrors tsmom_literature_shadow's SQL constant).
_GATE_SHADOW_EVENTS_SQL = (
    "INSERT INTO gate_shadow_events "
    "(event_id, run_id, signal_id, gate_id, venue, symbol, regime, "
    " technical_decision, technical_scalar, technical_reason, "
    " technical_flags, gpt_decision, mismatch, cell_warm, created_ts) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_SHADOW_STRATEGY = CapitalMacroRiskoffCatalystStrategy()


def log_capital_macro_riskoff_shadow(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    signal_id: str | None,
    venue: str,
    symbol: str,
    regime: str,
    market_view: MarketView,
    now_ts: int,
    db_writer: DBWriter | None = None,
) -> None:
    """Append ONE ``gate_shadow_events`` row for the shadow-only would-be signal.

    No-op (fail-open) on: ``conn`` is None, the venue/symbol is not the GOLD
    instrument this strategy trades (``gold_breakout_1h.SUPPORTED_SYMBOLS`` —
    the SAME allowlist ``generate_raw_signal`` itself checks; filtered HERE
    too so a non-GOLD capital symbol never logs an irrelevant "flat" row and
    floods the shared shadow table). A write fault is swallowed + logged at
    WARNING (mirrors ``tsmom_literature_shadow``'s contract) — this NEVER
    blocks the tick.

    ``technical_decision`` carries the would-be RawSignal side ("long") or
    "flat" (no trigger this bar); ``technical_scalar`` carries its strength
    (0.0 when flat); ``technical_reason`` carries the thesis tag.
    ``gpt_decision``/``mismatch`` are unused here (no comparator strategy —
    unlike tsmom's literature-vs-deployed delta) and stay ``""``/``0``
    (``gate_shadow_events.gpt_decision`` is ``NOT NULL DEFAULT ''``).

    ``db_writer``: same opt-in fire-and-forget submit as
    ``tsmom_literature_shadow`` — safe to defer (no same-tick reader).
    """
    if conn is None:
        return
    if venue != "capital" or _norm_symbol(symbol) not in SUPPORTED_SYMBOLS:
        return
    signal = _SHADOW_STRATEGY.generate_raw_signal(market_view)
    decision: str
    if signal is not None:
        decision, scalar, reason = signal.side, float(signal.strength), signal.thesis_tag
    else:
        decision, scalar, reason = "flat", 0.0, "no_trigger"
    args = (
        uuid.uuid4().hex, run_id, signal_id, int(GATE_STRATEGY_SIGNAL),
        venue, symbol, regime, decision, scalar, reason,
        "capital_macro_riskoff_catalyst_shadow", "", 0, 0, int(now_ts),
    )
    try:
        if db_writer is not None and dbwriter_enabled():
            def _job(
                c: sqlite3.Connection,
                sql: str = _GATE_SHADOW_EVENTS_SQL, a: tuple[object, ...] = args,
            ) -> None:
                c.execute(sql, a)

            db_writer.submit(_job, label="capital_macro_riskoff_shadow")
        else:
            conn.execute(_GATE_SHADOW_EVENTS_SQL, args)
    except sqlite3.Error as exc:
        logger.warning(
            "[gate_shadow_events] capital_macro_riskoff shadow dropped %s:%s: %r",
            venue, symbol, exc,
        )
