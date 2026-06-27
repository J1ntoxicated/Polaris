"""Re-export shim — the US-equity session clock moved to core.

The deterministic RTH/PDT session clock is a pure ``datetime``/``zoneinfo``
primitive (no Alpaca SDK / network), and two ``polaris.core`` modules
(``core.sizing.session`` + ``core.pipeline.agents._stream_guards``) consume it —
so importing it UP from ``polaris.venues`` was a layer inversion. The module now
lives at ``polaris.core.sessions.equity_session_gate``. This shim re-exports the
full public surface so every existing
``from polaris.venues.alpaca.equity_session_gate import ...`` keeps working
byte-for-byte. Behaviour is unchanged — this is a move only. The weekend-aware
``us_equity_session_state`` and the pre-open WARM-lead helpers
(``equity_ws_warm_active`` / ``WS_WARM_LEAD_MINUTES``) live in core too and are
re-exported here so the venue WS gate keeps importing them from this path.
"""

from __future__ import annotations

from polaris.core.sessions.equity_session_gate import (
    PDT_DAYTRADE_THRESHOLD,
    PDT_RANK_PENALTY_STEP,
    RTH_CLOSE_LOCAL_MINUTES,
    RTH_CLOSE_UTC_MINUTES,
    RTH_OPEN_LOCAL_MINUTES,
    RTH_OPEN_UTC_MINUTES,
    US_EQUITY_CALENDAR,
    WS_WARM_LEAD_MINUTES,
    SessionState,
    equity_entry_held_for_session,
    equity_ws_warm_active,
    pdt_rank_penalty,
    stream_session_gate_active,
    us_equity_session_state,
)

__all__ = [
    "PDT_DAYTRADE_THRESHOLD",
    "PDT_RANK_PENALTY_STEP",
    "RTH_CLOSE_LOCAL_MINUTES",
    "RTH_CLOSE_UTC_MINUTES",
    "RTH_OPEN_LOCAL_MINUTES",
    "RTH_OPEN_UTC_MINUTES",
    "US_EQUITY_CALENDAR",
    "WS_WARM_LEAD_MINUTES",
    "SessionState",
    "equity_entry_held_for_session",
    "equity_ws_warm_active",
    "pdt_rank_penalty",
    "stream_session_gate_active",
    "us_equity_session_state",
]
