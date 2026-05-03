from __future__ import annotations

import time

from invasion.ops import northstar_counter as ns


def test_record_and_count_basic():
    c = ns.NorthstarCounter()
    assert c.count_1h("dampen") == 0
    assert c.count_1h("block") == 0
    c.record("dampen", "composer/regime_mult")
    c.record("dampen", "composer/provider_mult")
    c.record("block", "engine/score_below_min")
    assert c.count_1h("dampen") == 2
    assert c.count_1h("block") == 1
    assert c.last_where("dampen") == "composer/provider_mult"
    assert c.last_where("block") == "engine/score_below_min"


def test_unknown_kind_ignored():
    c = ns.NorthstarCounter()
    c.record("wat", "x")
    assert c.count_1h("wat") == 0
    assert c.count_1h("dampen") == 0


def test_rolling_window_expiry(monkeypatch):
    c = ns.NorthstarCounter()
    fake = {"now": 1_000_000.0}
    monkeypatch.setattr(time, "time", lambda: fake["now"])
    c.record("dampen", "composer/regime_mult")
    # 59 min later → still in window
    fake["now"] += 59 * 60
    assert c.count_1h("dampen") == 1
    # 61 min after the original record → bucket expired
    fake["now"] = 1_000_000.0 + 61 * 60
    assert c.count_1h("dampen") == 0


def test_module_global_noop_when_unset():
    ns.set_global(None)
    ns.record("dampen", "x")  # should not raise


def test_module_global_routes_to_installed_counter():
    c = ns.NorthstarCounter()
    ns.set_global(c)
    try:
        ns.record("dampen", "composer/regime_mult")
        assert c.count_1h("dampen") == 1
    finally:
        ns.set_global(None)


def test_snapshot():
    c = ns.NorthstarCounter()
    c.record("dampen")
    c.record("block")
    c.record("block")
    snap = c.snapshot()
    assert snap == {"dampen": 1, "block": 2}
