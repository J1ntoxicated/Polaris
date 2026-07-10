"""Alpaca equity sleeve Wave 1b + 1.5 — 발화 경로 적대검증 (spec §5, static/offline).

DEMO/PAPER virtual sleeve only — no real capital/fees. Proves the two new
strategies (``equity_bb_meanrev_15m`` / ``equity_opening_range_breakout``,
§1 #4-#5) are not silently INERT:

  (a) VIRTUAL dispatches both ids; REAL (env unset) dispatches neither
      (byte-identical off) — same dual-mode subprocess pattern as
      ``test_virtual_loosen_rsi_bb_pullback.py`` (module constants are
      evaluated once at import, so each mode needs a FRESH interpreter, not
      an in-process reload of a package with a module-level registry dict).
  (b) ``correlation_group_id`` is globally unique across the registry.
  (c) ``strategies_by_tf`` gains an "alpaca" venue at the "15m" bucket — the
      FIRST Alpaca 15m strategy (the max-INERT-risk seam per the build spec:
      every prior Alpaca strategy was 1D).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

_NEW_IDS = ("equity_bb_meanrev_15m", "equity_opening_range_breakout")

_CHECK_SCRIPT = """
import json
from polaris.scripts._production_tick import _all_strategies, _strategies_by_timeframe
from polaris.strategies import STRATEGY_REGISTRY

dispatched = sorted(s.metadata.strategy_id for s in _all_strategies())
registered = sorted(STRATEGY_REGISTRY)
by_tf = _strategies_by_timeframe(_all_strategies())
venues_15m = sorted({s.metadata.venue for s in by_tf.get("15m", [])})
print(json.dumps({
    "dispatched": dispatched,
    "registered": registered,
    "venues_15m": venues_15m,
}))
"""


def _run_with_env(virtual: bool) -> dict[str, list[str]]:
    env = dict(os.environ)
    if virtual:
        env["POLARIS_VIRTUAL_ACCOUNT"] = "1"
    else:
        env.pop("POLARIS_VIRTUAL_ACCOUNT", None)
    result = subprocess.run(
        [sys.executable, "-c", _CHECK_SCRIPT],
        cwd=os.getcwd(), env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    parsed: dict[str, list[str]] = json.loads(result.stdout.strip().splitlines()[-1])
    return parsed


# ---------------------------------------------------------------------------
# (a) dispatch SSOT: VIRTUAL dispatches, REAL does not — both registered.
# ---------------------------------------------------------------------------


def test_virtual_mode_dispatches_both_new_strategies() -> None:
    state = _run_with_env(virtual=True)
    for sid in _NEW_IDS:
        assert sid in state["registered"], f"{sid} missing from STRATEGY_REGISTRY"
        assert sid in state["dispatched"], (
            f"{sid} eligible in VIRTUAL but not dispatched (silent INERT)"
        )


def test_real_mode_never_dispatches_new_strategies() -> None:
    state = _run_with_env(virtual=False)
    for sid in _NEW_IDS:
        assert sid in state["registered"], f"{sid} missing from STRATEGY_REGISTRY"
        assert sid not in state["dispatched"], (
            f"{sid} dispatched in REAL — must stay byte-identical off"
        )


# ---------------------------------------------------------------------------
# (b) corr_group_id global uniqueness (in-process — no env dependency).
# ---------------------------------------------------------------------------


def test_new_correlation_group_ids_unique_across_registry() -> None:
    from polaris.strategies import STRATEGY_REGISTRY

    ids = [cls.metadata.correlation_group_id for cls in STRATEGY_REGISTRY.values()]
    assert len(ids) == len(set(ids)), "correlation_group_id collision in registry"
    assert "equity_bb_mean_reversion_15m" in ids
    assert "equity_orb_open" in ids


# ---------------------------------------------------------------------------
# (c) tf-bucket routing: "15m" gains an "alpaca" venue (max INERT risk seam).
# ---------------------------------------------------------------------------


def test_15m_bucket_gains_alpaca_venue_in_virtual_mode() -> None:
    state = _run_with_env(virtual=True)
    assert "alpaca" in state["venues_15m"], (
        "15m bucket must include 'alpaca' once equity_bb_meanrev_15m / "
        "equity_opening_range_breakout are VIRTUAL-dispatched — else the "
        "15m fan-out never reaches the Alpaca leg (silent INERT)"
    )
    # OKX rsi_bb_pullback is also 15m — the bucket must be MULTI-venue, not a
    # replacement (additive widen only).
    assert "okx" in state["venues_15m"]


def test_15m_bucket_has_no_alpaca_venue_in_real_mode() -> None:
    # REAL byte-identical: the two new 15m strategies are dispatch_eligible=
    # False there, so the 15m bucket stays OKX-only (unchanged from pre-Wave1b).
    state = _run_with_env(virtual=False)
    assert "alpaca" not in state["venues_15m"]
