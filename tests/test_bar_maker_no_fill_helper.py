"""Bar entry maker no-fill mode helper (#77).

DEMO/PAPER only. ``_bar_maker_no_fill`` resolves a registered strategy's
no-fill behaviour from its metadata: a strategy that opts into
``maker_no_fill_cancel`` (the weekend thin-book maker) returns ``"cancel"`` (a
missed deep-bid fill is a 0-cost skip); every other strategy — and any
unregistered id — returns ``"market"`` (the legacy taker-fallback, byte-identical
so no existing entry is ever blocked).
"""

from __future__ import annotations

from polaris.scripts._run_signal_helpers import _bar_maker_no_fill


def test_weekend_strategy_is_cancel() -> None:
    assert _bar_maker_no_fill("weekend_thin_book_flush_maker") == "cancel"


def test_other_registered_strategy_is_market() -> None:
    # A normal prefer-maker reversion strategy still falls back to taker.
    assert _bar_maker_no_fill("rsi_bb_pullback") == "market"


def test_unregistered_id_is_market() -> None:
    # Unknown id → market (never block an entry — flow_not_block).
    assert _bar_maker_no_fill("does_not_exist") == "market"
