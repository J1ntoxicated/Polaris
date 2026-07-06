"""Stop-bleeders (2026-06-27) — KILL spot_donchian + SIP-gate equity strategies.

DEMO/PAPER paper-trading. Two live-bleed strategies are severed at the dispatch
layer — NOT a defensive block / size-cut / dampen:

A. ``spot_donchian`` KILL — OKX 1H Donchian = the fee-fatal intraday class the
   overnight research REJECTed; it kept losing live AFTER the exit fix
   (-$85.66 / 16 closes). Removed from STRATEGY_REGISTRY + the ``_all_strategies``
   dispatch (same pattern as the autopsy 7-strategy KILL). Its module + open-
   position close path stay intact; the dead registry row is swept by the next
   ``learner_prune``.

B. equity SIP-gate — ``equity_vol_expansion_pocket_pivot`` +
   ``equity_52wk_high_breakout``. NOTE (equity-gate-relax 2026-06-27): the
   SIP-feed gate this file originally asserted (inert on IEX) was RELAXED to a
   no-op — the daily-equity strategies derive their entry signal from yfinance
   daily bars (feed-agnostic), so the SIP/IEX realtime distinction never gated a
   1D-close strategy. The bounded-bleed concern moved to the shadow validation
   cap. The remaining Part-B tests here assert only the unchanged invariants
   (non-equity never gated, equity stay registered + dispatched); the relax
   firing behavior is covered in ``tests/test_equity_gate_relax.py``.
"""

from __future__ import annotations

from polaris.core.universe.schema import (
    mark_alpaca_feed_downgraded,
    reset_alpaca_runtime_feed,
)
from polaris.scripts._production_tick import (
    _all_strategies,
    equity_entry_inert_for_feed,
)
from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.equity_52wk_high_breakout import Equity52WkHighBreakoutStrategy
from polaris.strategies.equity_vol_expansion_pocket_pivot import (
    EquityVolExpansionPocketPivotStrategy,
)
from polaris.strategies.okx_donchian_55_breakout import OKXDonchian55BreakoutStrategy
from polaris.strategies.rsi_bb_pullback import RSIBBPullbackStrategy

# ---------------------------------------------------------------------------
# A. spot_donchian KILL — registry + dispatch
# ---------------------------------------------------------------------------


def test_spot_donchian_absent_from_registry() -> None:
    assert "spot_donchian" not in STRATEGY_REGISTRY


def test_spot_donchian_absent_from_dispatch() -> None:
    ids = {s.metadata.strategy_id for s in _all_strategies()}
    assert "spot_donchian" not in ids


def test_registry_drops_to_20() -> None:
    # Was 21 (strategy-wave2); spot_donchian KILL → 20; +1 weekend_thin_book_
    # flush_maker (#77, the single verified crypto maker BUILD) → 21; +1
    # weekend_funding_capitulation_maker (#80, 2nd weekend edge, positioning) → 22.
    # Opus-spec 3-clone build: +silver/us100/uk100_breakout_1h → 25.
    # B1 prune (2026-07-06, live-ledger forensic, -$2,024 book loss):
    # -session_breakout/-donchian_turtle_breakout/-equity_52wk_high_breakout/
    # -equity_vol_expansion_pocket_pivot (100.9% of the loss): 25 → 21.
    assert len(STRATEGY_REGISTRY) == 21


def test_dispatch_is_subset_of_registry_no_zombie() -> None:
    # The live dispatch list (``_all_strategies``) must be a SUBSET of the
    # registry — a KILL in the registry must never leave a zombie still
    # dispatched. (Some registered strategies — supertrend / connors_rsi2 /
    # cci_reversion — are intentionally registered-but-not-dispatched; that
    # pre-existing asymmetry is unrelated to this KILL.)
    dispatch_ids = {s.metadata.strategy_id for s in _all_strategies()}
    assert dispatch_ids <= set(STRATEGY_REGISTRY)
    assert "spot_donchian" not in dispatch_ids
    assert "spot_donchian" not in STRATEGY_REGISTRY


def test_surviving_okx_breakout_still_registered() -> None:
    # The KILL is surgical: the verified OKX 1D breakout survivor is untouched.
    assert "okx_donchian_55_breakout" in STRATEGY_REGISTRY


# ---------------------------------------------------------------------------
# B. equity SIP-gate — fires on SIP, inert on IEX, auto-recovers
# ---------------------------------------------------------------------------


def _equity_strats() -> list[object]:
    return [
        EquityVolExpansionPocketPivotStrategy(),
        Equity52WkHighBreakoutStrategy(),
    ]


def test_equity_fires_on_iex_after_relax() -> None:
    # SUPERSEDED by equity-gate-relax (2026-06-27): the daily-equity strategies
    # derive their signal from yfinance daily bars (feed-agnostic), so a runtime
    # SIP→IEX downgrade no longer makes them inert — they FIRE on iex (flow
    # activated). The prior IEX bleed is now bounded by the shadow validation cap.
    # (Full relax coverage lives in tests/test_equity_gate_relax.py.)
    reset_alpaca_runtime_feed()
    mark_alpaca_feed_downgraded()  # active feed is now iex
    try:
        for s in _equity_strats():
            assert equity_entry_inert_for_feed(s) is False
    finally:
        reset_alpaca_runtime_feed()


def test_equity_armed_when_runtime_feed_sip() -> None:
    # No downgrade → configured default sip is the active feed → the equity
    # strategies fire (auto-recovery the instant a real SIP key is routed).
    reset_alpaca_runtime_feed()
    for s in _equity_strats():
        assert equity_entry_inert_for_feed(s) is False


def test_non_equity_strategies_never_feed_gated() -> None:
    # The gate is Alpaca-equity ONLY: OKX / non-equity strategies are NEVER inert
    # regardless of the Alpaca feed (A/B tracks byte-identical, flow_not_block).
    reset_alpaca_runtime_feed()
    mark_alpaca_feed_downgraded()  # iex
    try:
        assert equity_entry_inert_for_feed(RSIBBPullbackStrategy()) is False
        assert equity_entry_inert_for_feed(OKXDonchian55BreakoutStrategy()) is False
    finally:
        reset_alpaca_runtime_feed()


def test_equity_strategies_remain_registered() -> None:
    # SUPERSEDED by the B1 prune (2026-07-06) — the SIP-gate finding above was
    # about dispatch-time inert-ness, but the live-ledger forensic (-$137.23 /
    # -$431.05, 0% win) then KILLed both at the registry level (100.9% of the
    # -$2,024 book loss alongside session_breakout / donchian_turtle_breakout).
    # Modules preserved read-only; no longer registered or dispatched.
    assert "equity_vol_expansion_pocket_pivot" not in STRATEGY_REGISTRY
    assert "equity_52wk_high_breakout" not in STRATEGY_REGISTRY
    dispatch_ids = {s.metadata.strategy_id for s in _all_strategies()}
    assert "equity_vol_expansion_pocket_pivot" not in dispatch_ids
    assert "equity_52wk_high_breakout" not in dispatch_ids
