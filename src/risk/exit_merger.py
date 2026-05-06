"""ExitMerger — unified exit derivation from multiple contributions (P6 pure).

When multiple strategies enter same ticker, the position is no longer "vb's
slice + grid's slice" — it's a unified BTC long with combined conviction.
Exit logic should be REDEFINED based on the combination, not appended.

Conservative merge policy:
- TakeProfit: max (capture biggest target — multi-strategy = high conviction)
- StopLoss: min (tightest — protective at strongest individual stop)
- TimeBasedHold: max (longest patience — give time for biggest TP)
- TrailingStop: enabled if any contribution has trailing
- SignalReversal: keep distinct per strategy (each can still flip independently)
- PartialTP: keep first contribution's (rare to merge partials)

This is "OR" for protection + "MAX" for aspiration: combined position aims
for the biggest move while protecting at the tightest stop.
"""
from __future__ import annotations

from src.exec.exit_strategies import (
    ExitStrategy,
    PartialTP,
    SignalReversal,
    StopLoss,
    TakeProfit,
    TimeBasedHold,
    TrailingStop,
)


def merge_exits(
    contributions_exits: list[tuple[ExitStrategy, ...]],
) -> tuple[ExitStrategy, ...]:
    """Compute unified exit list from per-contribution exit_strategies lists.

    Pure: deterministic, no I/O.
    """
    if not contributions_exits:
        return ()
    if len(contributions_exits) == 1:
        return contributions_exits[0]

    # Collect by type
    tps: list[float] = []
    sls: list[float] = []
    holds: list[float] = []
    trailings: list[TrailingStop] = []
    sig_reversals: list[SignalReversal] = []
    partials: list[PartialTP] = []

    for exits in contributions_exits:
        for ex in exits:
            if isinstance(ex, TakeProfit):
                tps.append(ex.pct)
            elif isinstance(ex, StopLoss):
                sls.append(ex.pct)
            elif isinstance(ex, TimeBasedHold):
                holds.append(ex.max_hours)
            elif isinstance(ex, TrailingStop):
                trailings.append(ex)
            elif isinstance(ex, SignalReversal):
                sig_reversals.append(ex)
            elif isinstance(ex, PartialTP):
                partials.append(ex)

    merged: list[ExitStrategy] = []
    if tps:
        merged.append(TakeProfit(max(tps)))
    if sls:
        merged.append(StopLoss(min(sls)))
    if holds:
        merged.append(TimeBasedHold(max(holds)))
    if trailings:
        # Most aggressive trailing: tightest activation, tightest trail
        best = min(
            trailings,
            key=lambda t: (t.activation_pct, t.trail_pct),
        )
        merged.append(best)
    # Keep all signal-reversal exits (per-strategy)
    merged.extend(sig_reversals)
    # Keep first partial TP if any (rarely merged)
    if partials:
        merged.append(partials[0])

    return tuple(merged)
