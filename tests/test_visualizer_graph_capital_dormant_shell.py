"""Capital weekend-dormant instruments must stay VISIBLE on the globe shell.

Regression (Jin 2026-06-28): the Capital galaxy looked empty/off on the globe
while OKX and Alpaca read full. Root cause was NOT a missing emit — every Capital
instrument IS emitted as a mkt node — but a render path: on weekends Capital's
venue marketStatus turns ~167 of 168 instruments ``is_active=0``, so they become
``state="dormant"`` and the client routes them to the cheap dim point-cloud. There
the dot brightness/size came purely from depth and ignored the node's emitted
``intensity``/``size_mul`` — and those were floored at the vol-driven minimum
(0.102 / 0.6) because weekend forex/commodity/index vol is tiny or zero. The
Capital shell rendered as near-invisible grey dust.

Fix (display-only): ``_mkt_nodes`` now stamps a ``shell_floor`` hint on Capital
dormant nodes and lifts their ``intensity``/``size_mul`` to a visible minimum so
the client dim-cloud (which now consults those fields for ``shell_floor`` nodes)
draws the Capital venue shell brighter/larger — "asleep but present". The node
STAYS dormant (no glow, no active spoof), and OKX/Alpaca nodes are untouched.

Display-only — universe ``is_active`` / trading / focus / session gating are NOT
read or mutated here; only the globe node's display weight changes.
"""

from __future__ import annotations

from tools.visualizer.polaris_graph import _mkt_nodes

# Visibility floor constants the fix introduces (display weight, never sizing/risk).
_SHELL_INTENSITY_FLOOR = 0.30
_SHELL_SIZE_FLOOR = 0.85


def _row(exchange: str, ticker: str, asset_group: str, *, is_active: int, n_24h: int) -> dict:
    return {
        "ticker": ticker,
        "exchange": exchange,
        "asset_group": asset_group,
        "n_24h": n_24h,
        "is_active": is_active,
    }


def _capital_weekend_universe() -> list[dict]:
    """1 active (EURUSD_W) + dormant forex / commodity / index — vol tiny or zero."""
    rows = [_row("cap", "EURUSD_W", "forex", is_active=1, n_24h=1)]
    rows += [_row("cap", f"FX{i}", "forex", is_active=0, n_24h=1) for i in range(115)]
    rows += [_row("cap", f"COMM{i}", "commodity", is_active=0, n_24h=1) for i in range(33)]
    rows += [_row("cap", f"IDX{i}", "indices", is_active=0, n_24h=1) for i in range(20)]
    return rows


def test_capital_dormant_instruments_present_in_globe_output() -> None:
    """Every Capital instrument — forex, commodity, index — is an individual node,
    dormant ones included (not folded into haze at this venue's small size)."""
    universe = _capital_weekend_universe()
    nodes, tail = _mkt_nodes(
        universe, signal_counts={}, tier_map={}, node_cap=240, base_i=0
    )
    groups = {n["asset_group"] for n in nodes}
    assert {"forex", "commodity", "indices"} <= groups, f"missing groups: {groups}"
    # 1 + 115 + 33 + 20 = 169 individual nodes, none folded to haze (under cap).
    assert len(nodes) == 169, f"expected all Capital rows individual, got {len(nodes)}"
    assert not any(t["exchange"] == "cap" for t in tail), "Capital must not fold to haze"


def test_capital_dormant_nodes_carry_visible_shell_floor() -> None:
    """Dormant Capital nodes are flagged + floored to a visible display weight so the
    client dim-cloud renders the venue shell, instead of vol-floored grey dust."""
    universe = _capital_weekend_universe()
    nodes, _tail = _mkt_nodes(
        universe, signal_counts={}, tier_map={}, node_cap=240, base_i=0
    )
    dormant = [n for n in nodes if n["state"] == "dormant"]
    assert dormant, "expected dormant Capital nodes"
    for n in dormant:
        assert n.get("shell_floor") is True, f"{n['id']} not shell-floored"
        assert n["intensity"] >= _SHELL_INTENSITY_FLOOR, f"{n['id']} intensity too dim"
        assert n["size_mul"] >= _SHELL_SIZE_FLOOR, f"{n['id']} size too small"
    # The single active Capital node stays a normal lit node (no shell_floor needed).
    lit = [n for n in nodes if n["state"] == "lit"]
    assert lit and all(not node.get("shell_floor") for node in lit)


def test_capital_dormant_stays_dormant_not_active_spoof() -> None:
    """The floor is display-only: dormant nodes keep state=dormant + active=False —
    no glow / lit / firing spoofing (market-closed is the truth, kept honest)."""
    universe = _capital_weekend_universe()
    nodes, _tail = _mkt_nodes(
        universe, signal_counts={}, tier_map={}, node_cap=240, base_i=0
    )
    for n in nodes:
        if n.get("shell_floor"):
            assert n["state"] == "dormant"
            assert n["active"] is False
            assert n["signal_count_30m"] == 0


def test_okx_and_alpaca_dormant_unaffected() -> None:
    """Alpaca/OKX globe untouched: their dormant nodes are NOT shell-floored and keep
    the original vol-driven intensity/size (no cross-venue visibility change)."""
    okx = [_row("okx", f"OKX{i}-USDT", "crypto", is_active=0, n_24h=1) for i in range(10)]
    alpaca = [_row("alp", f"ALP{i}", "equity", is_active=0, n_24h=1) for i in range(10)]
    universe = okx + alpaca
    nodes, _tail = _mkt_nodes(
        universe, signal_counts={}, tier_map={}, node_cap=240, base_i=0
    )
    assert nodes, "expected emitted nodes"
    for n in nodes:
        assert not n.get("shell_floor"), f"{n['id']} unexpectedly shell-floored"
        # original vol-floored values preserved (n_24h=1 → 0.102 / 0.602).
        assert n["intensity"] == round(min(1.0, 0.1 + 1 / 500.0), 4)
        assert n["size_mul"] == round(min(1.0, 0.6 + 1 / 1000.0), 4)


def test_capital_active_node_keeps_vol_driven_values() -> None:
    """An ACTIVE (tradeable) Capital node is a normal lit node — NOT shell-floored,
    its intensity/size come from real vol (the floor is dormant-shell only)."""
    universe = [_row("cap", "EURUSD_W", "forex", is_active=1, n_24h=300)]
    nodes, _tail = _mkt_nodes(
        universe, signal_counts={}, tier_map={}, node_cap=240, base_i=0
    )
    assert len(nodes) == 1
    n = nodes[0]
    assert n["state"] == "lit"
    assert not n.get("shell_floor")
    assert n["intensity"] == round(min(1.0, 0.1 + 300 / 500.0), 4)
