"""Wire test — silver/us100/uk100_breakout_1h full dispatch chain reachability.

DEMO/PAPER paper-trading. Mirrors ``tests/test_inert_strategy_reachability.py``:
wave2 5-strategy union was previously silently INERT (registered but never
dispatched) because the routing carve-out omitted their SUPPORTED_SYMBOLS. This
pins EVERY stage of the chain for the 3 new per-symbol clones so the same
silent-INERT class cannot recur: registry -> dispatch_eligible -> bar-path
reachability (CAPITAL_BAR_STRATEGY_SYMBOLS union) -> session-gate wiring for the
two session-scoped clones (US100/UK100).
"""

from __future__ import annotations

from polaris.scripts._production_tick import (
    CAPITAL_BAR_STRATEGY_SYMBOLS,
    _all_strategies,
    keep_on_bar_path,
)
from polaris.scripts._session_map import entry_fanout_active, session_group
from polaris.strategies import STRATEGY_REGISTRY
from polaris.strategies.silver_breakout_1h import (
    SUPPORTED_SYMBOLS as SILVER_SYMBOLS,
)
from polaris.strategies.uk100_breakout_1h import (
    SUPPORTED_SYMBOLS as UK100_SYMBOLS,
)
from polaris.strategies.us100_breakout_1h import (
    SUPPORTED_SYMBOLS as US100_SYMBOLS,
)

_NEW_IDS = ("silver_breakout_1h", "us100_breakout_1h", "uk100_breakout_1h")


# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------


def test_all_three_registered() -> None:
    for sid in _NEW_IDS:
        assert sid in STRATEGY_REGISTRY, f"{sid} not registered"


# ---------------------------------------------------------------------------
# 2. dispatch_eligible (SSOT flag — default True, not overridden)
# ---------------------------------------------------------------------------


def test_all_three_dispatch_eligible_default_true() -> None:
    for sid in _NEW_IDS:
        assert STRATEGY_REGISTRY[sid].metadata.dispatch_eligible is True


def test_all_three_reachable_via_all_strategies() -> None:
    # _all_strategies() is the LIVE bar-pipeline dispatch set, derived from the
    # registry filtered on dispatch_eligible. Each new clone must appear.
    live_ids = {s.metadata.strategy_id for s in _all_strategies()}
    for sid in _NEW_IDS:
        assert sid in live_ids, f"{sid} excluded from live dispatch set"


# ---------------------------------------------------------------------------
# 3. Bar-path reachability (CAPITAL_BAR_STRATEGY_SYMBOLS union) — the exact
#    silent-INERT failure mode wave2 hit: registered + dispatch_eligible but
#    vacated from the bar fan-out before generate_raw_signal is ever called.
# ---------------------------------------------------------------------------


def test_silver_symbols_in_union() -> None:
    for sym in SILVER_SYMBOLS:
        assert sym in CAPITAL_BAR_STRATEGY_SYMBOLS
        assert keep_on_bar_path(asset_class="commodity", symbol=sym) is True


def test_us100_symbol_in_union() -> None:
    for sym in US100_SYMBOLS:
        assert sym in CAPITAL_BAR_STRATEGY_SYMBOLS
        assert keep_on_bar_path(asset_class="indices", symbol=sym) is True
        assert keep_on_bar_path(asset_class="index", symbol=sym) is True


def test_uk100_symbol_in_union() -> None:
    for sym in UK100_SYMBOLS:
        assert sym in CAPITAL_BAR_STRATEGY_SYMBOLS
        assert keep_on_bar_path(asset_class="indices", symbol=sym) is True
        assert keep_on_bar_path(asset_class="index", symbol=sym) is True


def test_union_is_exact_superset_no_hand_roll() -> None:
    # The union must actually CONTAIN the three new SUPPORTED_SYMBOLS sets (not
    # a hand-typed duplicate literal that could drift).
    assert SILVER_SYMBOLS <= CAPITAL_BAR_STRATEGY_SYMBOLS
    assert US100_SYMBOLS <= CAPITAL_BAR_STRATEGY_SYMBOLS
    assert UK100_SYMBOLS <= CAPITAL_BAR_STRATEGY_SYMBOLS


def test_prior_carveout_not_regressed() -> None:
    # Purely additive — GOLD / US500 / the wave2 index symbols still reachable.
    assert keep_on_bar_path(asset_class="commodity", symbol="GOLD") is True
    assert "GOLD" in CAPITAL_BAR_STRATEGY_SYMBOLS


# ---------------------------------------------------------------------------
# 4. Session gate wiring (weekday+holiday-aware SSOT clock mandate) — US100/
#    UK100 fire only inside their cached cash-session window; SILVER (no
#    regional group, GOLD-type commodity) is always-active on weekdays.
# ---------------------------------------------------------------------------


def test_us100_session_group_is_us() -> None:
    assert session_group("US100") == "us"


def test_uk100_session_group_is_europe() -> None:
    assert session_group("UK100") == "europe"


def test_silver_has_no_regional_session_group() -> None:
    # GOLD-type commodity — global 24/5, no discrete regional cash window.
    assert session_group("SILVER") is None


def test_us100_entry_fanout_gated_by_session() -> None:
    # Wednesday 14:00 UTC — inside the 'us' window (13:30-20:00 UTC).
    inside = 1_700_056_800  # 2023-11-15 14:00 UTC (Wednesday)
    # Same day, 06:00 UTC — before the US cash open.
    outside = 1_700_028_000  # 2023-11-15 06:00 UTC
    assert entry_fanout_active("capital", "indices", "US100", inside) is True
    assert entry_fanout_active("capital", "indices", "US100", outside) is False


def test_uk100_entry_fanout_gated_by_session() -> None:
    # Wednesday 10:00 UTC — inside the 'europe' window (07:00-16:00 UTC).
    inside = 1_700_042_400  # 2023-11-15 10:00 UTC
    # Same day, 22:00 UTC — after the Europe cash close.
    outside = 1_700_085_600  # 2023-11-15 22:00 UTC
    assert entry_fanout_active("capital", "indices", "UK100", inside) is True
    assert entry_fanout_active("capital", "indices", "UK100", outside) is False


def test_us100_uk100_weekend_gated_off() -> None:
    # Saturday — both cash books shut.
    saturday = 1_700_308_800  # 2023-11-18 12:00 UTC (Saturday)
    assert entry_fanout_active("capital", "indices", "US100", saturday) is False
    assert entry_fanout_active("capital", "indices", "UK100", saturday) is False


def test_silver_entry_fanout_always_active_weekday() -> None:
    # No regional group -> weekday-only gate (unmapped commodity = always active).
    weekday = 1_700_056_800  # Wednesday 14:00 UTC
    saturday = 1_700_308_800  # Saturday
    assert entry_fanout_active("capital", "commodity", "SILVER", weekday) is True
    assert entry_fanout_active("capital", "commodity", "SILVER", saturday) is False
