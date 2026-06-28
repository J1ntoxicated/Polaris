"""Dual-SSOT dispatch guard — registry ↔ live dispatch consistency (drift-proof).

DEMO/PAPER only — virtual funds. AGGRESSIVE / flow_not_block: this guards a
STRUCTURE, it blocks nothing live. A ``dispatch_eligible=False`` strategy is a
no-emit KILL (``generate_raw_signal`` never called), NOT a size dampen / halt /
module removal — its open positions still exit via the recalc loop, and it never
cuts another strategy's size.

ROOT CAUSE this seals: the live bar dispatch (``_all_strategies``) used to be a
hand-synced id LITERAL, separate from ``STRATEGY_REGISTRY``. The two DRIFTED — a
registered strategy missing from the literal was silently INERT (registered ≠
dispatched), and a KILLed strategy could linger (kill ≠ removal = zombie). The fix
DERIVES the dispatch set from the registry filtered on ``metadata.dispatch_eligible``,
so the literal is gone and these guards make the drift structurally impossible:

  * SSOT-EQUALITY — the dispatched ids equal exactly the eligible registry ids.
  * INERT-BLOCKED — every eligible id is actually dispatched + reachable on the
    bar path (registered + eligible can never silently fail to fire).
  * ZOMBIE-BLOCKED — every fee-fatal / unvalidated KILL is registered yet absent
    from dispatch (the canonical KILL state = registry presence + flag False).
"""

from __future__ import annotations

from polaris.scripts._production_tick import _all_strategies, keep_on_bar_path
from polaris.strategies import STRATEGY_REGISTRY

# The KILLed / unvalidated strategies: registered (module + close path preserved)
# but dispatch_eligible=False so they emit no NEW entry.
#   rsi_bb_pullback — fee-fatal intraday crypto (no OOS/fee hurdle), confirmed KILL.
#   ema_crossover — fee-fatal 1H crypto trend-template (gross +$0.12 < fee $2.37 per
#     round-trip): the cross edge does not clear the OKX taker fee. KILLed 2026-06-28
#     once the live read landed (the judgement Jin had DEFERRED), same dispatch-level
#     no-emit pattern as rsi_bb_pullback — module + open-position close path preserved.
#   supertrend / connors_rsi2 / cci_reversion — unvalidated (design rationale only),
#     and never in the old dispatch literal (False = unchanged behaviour).
KILLED_IDS = frozenset(
    {
        "rsi_bb_pullback",
        "ema_crossover",
        "supertrend",
        "connors_rsi2",
        "cci_reversion",
    }
)


def _dispatch_ids() -> set[str]:
    return {s.metadata.strategy_id for s in _all_strategies()}


def _eligible_registry_ids() -> set[str]:
    return {
        sid for sid, cls in STRATEGY_REGISTRY.items() if cls.metadata.dispatch_eligible
    }


# ── SSOT EQUALITY: dispatch == eligible registry (drift impossible) ───────────


def test_dispatch_equals_eligible_registry() -> None:
    # The single load-bearing invariant: the live dispatch set IS the registry
    # filtered on dispatch_eligible. A new registered strategy auto-appears iff
    # flagged; no hand-synced literal can drift. If this fails, dispatch and the
    # registry have diverged — the exact INERT/zombie bug this seals.
    assert _dispatch_ids() == _eligible_registry_ids()


def test_dispatch_is_a_subset_of_registry() -> None:
    # Nothing is dispatched that is not registered (no orphan dispatch).
    assert _dispatch_ids() <= set(STRATEGY_REGISTRY)


# ── INERT BLOCKED: every eligible id is dispatched AND reachable ──────────────


def test_every_eligible_strategy_is_dispatched() -> None:
    # registered + eligible ⇒ dispatched. Catches the silent-INERT regression
    # (a strategy added to the registry + flagged eligible but never woken).
    missing = _eligible_registry_ids() - _dispatch_ids()
    assert not missing, f"eligible but NOT dispatched (silent INERT): {missing}"


def test_dispatched_strategies_reachable_on_bar_path() -> None:
    # Reachability: a dispatched strategy must have a routable venue and (for the
    # crypto/spot leg) be kept on the bar fan-out — else it is dispatched yet can
    # never fire (vacated-to-tick-engine INERT). OKX spot/crypto is the only leg
    # ``keep_on_bar_path`` gates unconditionally; Capital/Alpaca are reached via
    # their own venue focus, so we assert the OKX legs explicitly and that every
    # dispatched strategy carries a known venue.
    routable_venues = {"okx", "capital", "alpaca"}
    for s in _all_strategies():
        assert s.metadata.venue in routable_venues, (
            f"{s.metadata.strategy_id} has unroutable venue {s.metadata.venue!r}"
        )
        if s.metadata.venue == "okx":
            assert keep_on_bar_path(
                asset_class=s.metadata.asset_class, symbol="BTC-USDT"
            ), f"{s.metadata.strategy_id} (okx) vacated from the bar path → INERT"


# ── ZOMBIE BLOCKED: KILLed ids are registered but never dispatched ───────────


def test_killed_ids_registered_but_flagged_off() -> None:
    # The canonical KILL state: still in the registry (module + open-position
    # close path preserved) AND dispatch_eligible is False. KILL ≠ module removal.
    for sid in KILLED_IDS:
        assert sid in STRATEGY_REGISTRY, (
            f"{sid} was UN-registered — a KILL must stay registered so its open "
            "positions still exit via the recalc loop (kill != removal)"
        )
        assert STRATEGY_REGISTRY[sid].metadata.dispatch_eligible is False, (
            f"{sid} is a KILL but dispatch_eligible is not False"
        )


def test_killed_ids_absent_from_dispatch() -> None:
    # The zombie guard: a KILLed strategy never reaches generate_raw_signal.
    dispatched = _dispatch_ids()
    for sid in KILLED_IDS:
        assert sid not in dispatched, (
            f"{sid} is fee-fatal/unvalidated but is being dispatched (zombie)"
        )


def test_eligible_and_killed_partition_the_registry() -> None:
    # Exhaustiveness: every registered strategy is EITHER eligible (dispatched)
    # OR a documented KILL — no third, ambiguous state can hide a drift.
    eligible = _eligible_registry_ids()
    ineligible = set(STRATEGY_REGISTRY) - eligible
    assert ineligible == KILLED_IDS, (
        "an unexpected strategy is dispatch_eligible=False (or a KILL became "
        f"eligible): ineligible={ineligible}, expected KILL set={set(KILLED_IDS)}"
    )


# ── TICK-ENGINE PATH: a parallel signal-id table, NOT a registry consumer ─────


def test_tick_engine_signal_table_does_not_dispatch_registry_strategies() -> None:
    # The tick engine is the OTHER emit path. It does NOT read STRATEGY_REGISTRY:
    # it dispatches off ``_SIGNAL_FNS``, keyed by SIGNAL_ID (burst/flow/micro), not
    # strategy_id. Those three generators were KILLed 2026-06-26 (gross-negative
    # expectancy), so the table is EMPTY and the tick engine fires ZERO entries.
    # This guard pins that separation: no STRATEGY_REGISTRY strategy_id leaks into
    # the tick-engine table (the two paths never share keys), so the registry-
    # derived bar dispatch is the SINGLE strategy SSOT. Re-populating _SIGNAL_FNS
    # is then a deliberate, reviewed change — never an accidental second roster.
    from polaris.scripts._production_tick_decision import _SIGNAL_FNS

    assert set(_SIGNAL_FNS) & set(STRATEGY_REGISTRY) == set(), (
        "a STRATEGY_REGISTRY strategy_id leaked into the tick-engine signal table "
        "— the two emit paths must stay disjoint (registry = bar SSOT)"
    )
