"""tsmom_literature_shadow — G2 behavior-0 shadow tagger (frontgate item #2).

Computes a LITERATURE-FIXED 12-1 time-series momentum sign/strength
(Moskowitz/Ooi/Pedersen convention: 252-bar lookback skipping the most recent
21 bars, vol-normalized) for every 1D bar sequence handed to it, INDEPENDENT
of the live ``tsmom_12_1_multiasset`` strategy's own VIRTUAL-loosened
constants and its in-strategy monthly-rebalance emit-gate. Logs ONE
``gate_shadow_events`` row per (venue, symbol) evaluation — sign/strength
only, never sizing/entry/exit (behavior-0). Frontgate
``integration-blueprint.md`` G2 / ``experiment-roadmap.md`` item #2.

Wired at ``polaris/scripts/_production_tick.py`` immediately after
``build_real_market_view`` (mv construction) and BEFORE the per-strategy
dispatch loop, gated to ``timeframe == "1D"``.

``gate_shadow_events`` write is a RAW INSERT (bypasses
``shadow_log.log_shadow_event``'s ``ShadowDecision``/``GateDecision`` typing):
``technical_decision`` carries the LITERATURE sign ("long"/"short"/"flat"),
``gpt_decision`` carries the EXISTING (deployed, possibly VIRTUAL-loosened)
``tsmom_12_1_multiasset`` sign computed on the SAME bars — this is NOT a GPT
call, the column is reused as a plain-text comparator slot (0 new GPT calls,
per the roadmap's execution-subject table). ``mismatch`` is a direct
string-inequality of the two signs, not a ``GateDecision`` comparison.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import uuid
from typing import Final

from polaris.core.pipeline.gate_state import GATE_STRATEGY_SIGNAL
from polaris.strategies.base import BarView
from polaris.strategies.tsmom_12_1_multiasset import MOM_LOOKBACK as _EXISTING_MOM_LOOKBACK
from polaris.strategies.tsmom_12_1_multiasset import MOM_SKIP as _EXISTING_MOM_SKIP
from polaris.strategies.tsmom_12_1_multiasset import VOL_LOOKBACK

logger = logging.getLogger(__name__)

__all__ = [
    "LITERATURE_MOM_LOOKBACK",
    "LITERATURE_MOM_SKIP",
    "literature_mom_12_1",
    "log_tsmom_literature_shadow",
]

# Literature-fixed 12-1 params (Moskowitz/Ooi/Pedersen convention) — hard
# Final ints, deliberately NOT routed through virtual_loosen(): the roadmap
# requires this calc stay pinned to the literature spec in EVERY mode
# (VIRTUAL included), so the shadow delta against the live (loosened)
# strategy stays an honest literature-vs-deployed comparison.
LITERATURE_MOM_LOOKBACK: Final[int] = 252
LITERATURE_MOM_SKIP: Final[int] = 21


def _mom_12_1(closes: list[float], lookback: int, skip: int) -> float | None:
    """Skip-adjusted trailing return; ``None`` if warmup unmet / degenerate base."""
    if len(closes) < lookback + 1:
        return None
    base_close = closes[-(lookback + 1)]
    if base_close <= 0.0:
        return None
    anchor_close = closes[-(skip + 1)]
    return anchor_close / base_close - 1.0


def _sign(mom: float | None) -> str:
    if mom is None:
        return ""
    if mom > 0.0:
        return "long"
    if mom < 0.0:
        return "short"
    return "flat"


def _realized_vol(closes: list[float], window: int) -> float:
    """Std-dev of the trailing ``window`` daily simple returns (pure, total).

    Mirrors ``tsmom_12_1_multiasset._realized_vol``'s formula — duplicated
    (not imported) so this shadow module never depends on that strategy's
    private (underscore-prefixed) symbol.
    """
    if len(closes) <= window:
        return 0.0
    rets: list[float] = []
    for i in range(len(closes) - window, len(closes)):
        prev = closes[i - 1]
        if prev <= 0.0:
            continue
        rets.append(closes[i] / prev - 1.0)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def literature_mom_12_1(closes: list[float]) -> float | None:
    """Public literature 12-1 momentum (252/21, hard-fixed) — test/reuse seam."""
    return _mom_12_1(closes, LITERATURE_MOM_LOOKBACK, LITERATURE_MOM_SKIP)


def log_tsmom_literature_shadow(
    conn: sqlite3.Connection | None,
    *,
    run_id: str,
    signal_id: str | None,
    venue: str,
    symbol: str,
    regime: str,
    bars: list[BarView],
    now_ts: int,
) -> None:
    """Append ONE ``gate_shadow_events`` row: literature sign vs existing-deployed sign.

    No-op (fail-open) on: ``conn`` is None, literature warmup unmet
    (< ``LITERATURE_MOM_LOOKBACK`` + 1 bars), or a degenerate base close. A
    write fault is swallowed + logged at WARNING (mirrors ``shadow_log``'s
    contract) — this NEVER blocks the tick.
    """
    if conn is None:
        return
    closes = [b.close for b in bars]
    literature_mom = _mom_12_1(closes, LITERATURE_MOM_LOOKBACK, LITERATURE_MOM_SKIP)
    if literature_mom is None:
        return
    existing_mom = _mom_12_1(closes, _EXISTING_MOM_LOOKBACK, _EXISTING_MOM_SKIP)
    literature_sign = _sign(literature_mom)
    existing_sign = _sign(existing_mom)
    realized_vol = _realized_vol(closes, VOL_LOOKBACK)
    mismatch = 1 if (existing_sign and literature_sign != existing_sign) else 0
    try:
        conn.execute(
            """
            INSERT INTO gate_shadow_events
                (event_id, run_id, signal_id, gate_id, venue, symbol, regime,
                 technical_decision, technical_scalar, technical_reason,
                 technical_flags, gpt_decision, mismatch, cell_warm, created_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex, run_id, signal_id, int(GATE_STRATEGY_SIGNAL),
                venue, symbol, regime, literature_sign, float(realized_vol),
                f"tsmom_literature_12_1={literature_mom:.4f}",
                "tsmom_shadow_literature", existing_sign, mismatch, 0, int(now_ts),
            ),
        )
    except sqlite3.Error as exc:
        logger.warning(
            "[gate_shadow_events] tsmom literature shadow dropped %s:%s: %r",
            venue, symbol, exc,
        )
