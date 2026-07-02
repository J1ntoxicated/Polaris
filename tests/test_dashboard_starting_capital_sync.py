"""Day 9 F12 — Dashboard STARTING_CAPITAL SSOT sync tests.

Spec source: vault/30_components/dashboard.md + Jin 2026-05-07 audit.

Before this fix the dashboard hard-coded ``STARTING_CAPITAL = 10_000`` while
``_production_pipeline.EQUITY_USD_DEMO_DEFAULT = 79_000``. That mismatch
produced bogus DD / exposure ratios. After the fix:

* SSOT lives at ``polaris.core.sizing.constants``.
* Total demo equity = OKX SPOT $79K + Capital CFD $51K = $130K.
* Per-venue values exposed on ``DashboardSnapshot`` for the renderer split.
* Env vars override (POLARIS_DEMO_STARTING_EQUITY_OKX/CAPITAL/TOTAL).

P0-2 (Jin 2026-07-02): ``demo_starting_equity_total()`` now sums all 3 venues
(OKX + Capital + Alpaca display-baseline $30,181.75) = $160,181.75. The
OKX+Capital-only ``TOTAL_DEMO_STARTING_EQUITY_USD`` module constant is
unchanged ($130K, still the sizing-relevant OKX:Capital ratio anchor) —
``_ALPACA_TOTAL`` below is the derived 3-venue expectation these tests assert.
"""

from __future__ import annotations

import importlib
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from polaris.core.sizing.constants import (
    ALPACA_DISPLAY_STARTING_EQUITY_USD,
    CAPITAL_DEMO_STARTING_EQUITY_USD,
    OKX_DEMO_STARTING_EQUITY_USD,
    TOTAL_DEMO_STARTING_EQUITY_USD,
    demo_starting_equity_alpaca_display,
    demo_starting_equity_capital,
    demo_starting_equity_okx,
    demo_starting_equity_total,
    production_default_equity_usd,
)

# 3-venue header total (OKX $79K + Capital $51K + Alpaca display $30,181.75).
_ALPACA_TOTAL: float = TOTAL_DEMO_STARTING_EQUITY_USD + ALPACA_DISPLAY_STARTING_EQUITY_USD

# ---------------------------------------------------------------------------
# SSOT constants
# ---------------------------------------------------------------------------


def test_okx_demo_starting_equity_79k() -> None:
    assert OKX_DEMO_STARTING_EQUITY_USD == 79_000.0


def test_capital_demo_starting_equity_51k() -> None:
    """A$78K @ ~0.654 ≈ USD $51K (per CLAUDE.md + 2026-05-07 audit)."""
    assert CAPITAL_DEMO_STARTING_EQUITY_USD == 51_000.0


def test_total_demo_equity_130000() -> None:
    """OKX+Capital constant = 130K (still the sizing-relevant ratio anchor)."""
    assert TOTAL_DEMO_STARTING_EQUITY_USD == 130_000.0


def test_total_demo_equity_160181_75_with_alpaca() -> None:
    """P0-2: 3-venue header total = OKX 79K + Capital 51K + Alpaca 30,181.75."""
    assert demo_starting_equity_total() == 160_181.75
    assert demo_starting_equity_total() == _ALPACA_TOTAL


# ---------------------------------------------------------------------------
# Env override resolution
# ---------------------------------------------------------------------------


def test_env_override_okx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "100000")
    assert demo_starting_equity_okx() == 100_000.0
    # Total picks up the override too (+ the Alpaca display leg).
    assert demo_starting_equity_total() == (
        100_000.0 + CAPITAL_DEMO_STARTING_EQUITY_USD + ALPACA_DISPLAY_STARTING_EQUITY_USD
    )


def test_env_override_total(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-venue env unset → POLARIS_DEMO_STARTING_EQUITY_TOTAL wins for the
    OKX/Capital split; the Alpaca display leg is always additive on top."""
    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_OKX", raising=False)
    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_CAPITAL", raising=False)
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_TOTAL", "200000")
    assert demo_starting_equity_total() == 200_000.0 + ALPACA_DISPLAY_STARTING_EQUITY_USD


def test_env_override_total_splits_venues_proportionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex F12 round 1 fix: POLARIS_DEMO_STARTING_EQUITY_TOTAL must
    propagate into the per-venue helpers so OKX + CAP == TOTAL.

    Before the fix, setting only TOTAL=200K produced total=200K but the
    renderer split label still showed OKX $79K + CAP $51K = $130K (the
    hard-coded defaults), leaving the dashboard inconsistent.
    """
    import math

    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_OKX", raising=False)
    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_CAPITAL", raising=False)
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_TOTAL", "200000")
    okx = demo_starting_equity_okx()
    cap = demo_starting_equity_capital()
    total = demo_starting_equity_total()
    # Sum invariant — the source of truth for the F12 bug (+Alpaca display leg).
    assert math.isclose(okx + cap + ALPACA_DISPLAY_STARTING_EQUITY_USD, total, abs_tol=1e-6)
    assert math.isclose(total, 200_000.0 + ALPACA_DISPLAY_STARTING_EQUITY_USD)
    # Default ratio (79:51) is preserved so the split is recognisable.
    expected_okx = 200_000.0 * (
        OKX_DEMO_STARTING_EQUITY_USD / TOTAL_DEMO_STARTING_EQUITY_USD
    )
    assert math.isclose(okx, expected_okx, abs_tol=1e-6)


def test_env_override_okx_alone_does_not_mutate_capital(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-venue env independence: setting only OKX env keeps the Capital
    starting equity at its hard-coded default ($51K)."""
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "100000")
    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_CAPITAL", raising=False)
    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_TOTAL", raising=False)
    assert demo_starting_equity_okx() == 100_000.0
    assert demo_starting_equity_capital() == CAPITAL_DEMO_STARTING_EQUITY_USD


def test_env_override_zero_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex F12 round 1 nit fix: ``0.0`` is a valid override (was silently
    treated as unset by the legacy ``or`` short-circuit)."""
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "0")
    assert demo_starting_equity_okx() == 0.0


# ---------------------------------------------------------------------------
# Codex F12 round 2 — sum invariant across mixed envvar configurations
# ---------------------------------------------------------------------------


def test_env_override_okx_plus_total_capital_fills_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex F12 round 2: OKX + TOTAL set, CAPITAL unset → CAP fills the gap.

    Before the round 2 fix:
      OKX=100000, TOTAL=200000, CAP unset → okx=100000, cap=78461 (wrong,
      from default-ratio split), total=151000 (wrong, from okx+default).
    After fix: okx=100000, cap=100000 (gap), total=200000. Sum invariant.
    """
    import math

    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "100000")
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_TOTAL", "200000")
    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_CAPITAL", raising=False)
    okx = demo_starting_equity_okx()
    cap = demo_starting_equity_capital()
    total = demo_starting_equity_total()
    assert math.isclose(okx, 100_000.0)
    assert math.isclose(cap, 100_000.0)
    assert math.isclose(total, 200_000.0 + ALPACA_DISPLAY_STARTING_EQUITY_USD)
    assert math.isclose(okx + cap + ALPACA_DISPLAY_STARTING_EQUITY_USD, total, abs_tol=1e-6)


def test_env_override_capital_plus_total_okx_fills_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex F12 round 2: CAPITAL + TOTAL set, OKX unset → OKX fills the gap."""
    import math

    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_CAPITAL", "60000")
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_TOTAL", "150000")
    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_OKX", raising=False)
    okx = demo_starting_equity_okx()
    cap = demo_starting_equity_capital()
    total = demo_starting_equity_total()
    assert math.isclose(okx, 90_000.0)
    assert math.isclose(cap, 60_000.0)
    assert math.isclose(total, 150_000.0 + ALPACA_DISPLAY_STARTING_EQUITY_USD)
    assert math.isclose(okx + cap + ALPACA_DISPLAY_STARTING_EQUITY_USD, total, abs_tol=1e-6)


def test_env_override_all_three_set_per_venue_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex F12 round 2: when both per-venue envs are set, TOTAL is ignored
    (operator opted into explicit control). ``total = okx + cap``."""
    import math

    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "70000")
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_CAPITAL", "30000")
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_TOTAL", "999999")
    okx = demo_starting_equity_okx()
    cap = demo_starting_equity_capital()
    total = demo_starting_equity_total()
    # TOTAL ignored — per-venue explicit overrides win.
    assert math.isclose(okx, 70_000.0)
    assert math.isclose(cap, 30_000.0)
    assert math.isclose(total, 100_000.0 + ALPACA_DISPLAY_STARTING_EQUITY_USD)
    assert math.isclose(okx + cap + ALPACA_DISPLAY_STARTING_EQUITY_USD, total, abs_tol=1e-6)


def test_sum_invariant_holds_in_every_envvar_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Property: ``okx + capital + alpaca_display == total`` for every env config.

    This is the codex F12 round 2 acceptance criterion — the bug class
    we're closing is "renderer split mismatches dashboard total" — extended by
    P0-2 to include the additive Alpaca display leg."""
    import math

    cases = [
        # (okx_env, cap_env, total_env, label)
        (None, None, None, "no env"),
        ("100000", None, None, "okx only"),
        (None, "60000", None, "capital only"),
        (None, None, "200000", "total only"),
        ("100000", None, "200000", "okx + total"),
        (None, "60000", "150000", "capital + total"),
        ("100000", "60000", None, "okx + capital"),
        ("70000", "30000", "999999", "all three"),
        ("0", "0", None, "both zero"),
    ]
    for okx_env, cap_env, total_env, label in cases:
        for k in (
            "POLARIS_DEMO_STARTING_EQUITY_OKX",
            "POLARIS_DEMO_STARTING_EQUITY_CAPITAL",
            "POLARIS_DEMO_STARTING_EQUITY_TOTAL",
        ):
            monkeypatch.delenv(k, raising=False)
        if okx_env is not None:
            monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", okx_env)
        if cap_env is not None:
            monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_CAPITAL", cap_env)
        if total_env is not None:
            monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_TOTAL", total_env)
        okx = demo_starting_equity_okx()
        cap = demo_starting_equity_capital()
        alpaca_display = demo_starting_equity_alpaca_display()
        total = demo_starting_equity_total()
        assert math.isclose(okx + cap + alpaca_display, total, abs_tol=1e-6), (
            f"sum invariant failed for {label}: okx={okx} cap={cap} "
            f"alpaca_display={alpaca_display} total={total}"
        )


def test_env_override_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLARIS_DEMO_STARTING_EQUITY_OKX", "not-a-number")
    assert demo_starting_equity_okx() == OKX_DEMO_STARTING_EQUITY_USD


def test_production_default_honours_legacy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``POLARIS_EQUITY_USD`` (legacy) still wins for production sizing path."""
    monkeypatch.setenv("POLARIS_EQUITY_USD", "12345")
    assert production_default_equity_usd() == 12_345.0


def test_production_default_falls_back_to_okx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_EQUITY_USD", raising=False)
    monkeypatch.delenv("POLARIS_DEMO_STARTING_EQUITY_OKX", raising=False)
    assert production_default_equity_usd() == OKX_DEMO_STARTING_EQUITY_USD


# ---------------------------------------------------------------------------
# Dashboard snapshot module — SSOT integration
# ---------------------------------------------------------------------------


def test_no_hardcoded_10000_in_snapshot() -> None:
    """Source-level guard: snapshot.py must not contain a stand-alone 10_000
    starting-capital literal (the hardcoded value F12 fixes)."""
    src_path = Path("polaris/scripts/dashboard/snapshot.py")
    text = src_path.read_text()
    # The literal '10_000.0' as a starting-capital constant must not exist.
    # Other 10_000 mentions (e.g. bps math) live in different modules; the
    # snapshot module had only the bad starting-capital constant historically.
    assert "STARTING_CAPITAL: Final[float] = 10_000.0" not in text
    assert "STARTING_CAPITAL: Final[float] = 10000" not in text


def test_starting_capital_resolves_from_constants() -> None:
    """``STARTING_CAPITAL`` (module constant) must equal the 3-venue SSOT total."""
    from polaris.scripts.dashboard import snapshot as snap_mod

    assert snap_mod.STARTING_CAPITAL == _ALPACA_TOTAL


def test_dashboard_snapshot_zero_db_uses_total_base(tmp_path: Path) -> None:
    """A missing DB still returns a snapshot with the 3-venue SSOT total as base."""
    from polaris.scripts.dashboard.snapshot import collect_snapshot

    snap = collect_snapshot(db_path=tmp_path / "absent.sqlite")
    assert snap.starting_capital == _ALPACA_TOTAL
    assert snap.starting_capital_okx == OKX_DEMO_STARTING_EQUITY_USD
    assert snap.starting_capital_capital == CAPITAL_DEMO_STARTING_EQUITY_USD
    assert snap.starting_capital_alpaca == ALPACA_DISPLAY_STARTING_EQUITY_USD
    assert snap.equity_now == _ALPACA_TOTAL
    # Header-venue-sum identity (P0-2): the header total must equal the sum of
    # the 3 venue legs exactly (no fee gap possible on a zero-DB snapshot).
    assert snap.starting_capital == (
        snap.starting_capital_okx + snap.starting_capital_capital + snap.starting_capital_alpaca
    )


def test_dashboard_snapshot_dd_calculated_from_correct_base(
    memdb: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close fill of -$3,500 → DD% computed off $130K base, not $10K.

    Before the F12 fix a -$3,500 close on a $10K base = 35% DD; on the
    real $130K base = 2.69% DD. The dashboard must compute the latter.
    """
    db_path = tmp_path / "polaris.sqlite"
    # Populate a minimal schema with one closed fill of -3,500 USD.
    from polaris.storage.schema import ALL_DDL

    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    now_ms = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f1', 'okx', 'okx:BTC-USDT', 'volume_burst', 'sell', "
        "        100.0, 60000.0, 0.1, 1.0, ?, 'o1', NULL, -3500.0, 1, "
        "        0.001666, 100.0, 'filled')",
        (now_ms,),
    )
    conn.close()

    from polaris.scripts.dashboard.snapshot import collect_snapshot

    snap = collect_snapshot(db_path=db_path)
    # Equity ~ 160.18K - 3.5K = ~156.68K
    assert snap.starting_capital == _ALPACA_TOTAL
    # DD must be small (-3.5K against ~160K base, NOT 35% on 10K base).
    assert snap.drawdown_pct < 5.0, (
        f"DD% must be computed off $130K base; got {snap.drawdown_pct:.2f}% "
        f"(would be ~35% on the legacy hardcoded $10K base)"
    )
    _ = monkeypatch
    _ = importlib  # silence unused-import lint
    _ = Any  # type-import keeper


def test_production_pipeline_default_equals_okx_ssot() -> None:
    """``EQUITY_USD_DEMO_DEFAULT`` must be sourced from sizing.constants."""
    from polaris.scripts._production_pipeline import EQUITY_USD_DEMO_DEFAULT

    assert EQUITY_USD_DEMO_DEFAULT == OKX_DEMO_STARTING_EQUITY_USD


# ---------------------------------------------------------------------------
# P0-2 — header-venue-sum identity (Jin 2026-07-02)
# ---------------------------------------------------------------------------


def test_header_starting_capital_equals_sum_of_venue_legs(tmp_path: Path) -> None:
    """``snap.starting_capital`` must equal OKX + Capital + Alpaca legs exactly.

    ``starting_capital`` is a pre-trade baseline (no fills factored in), so this
    identity holds with zero tolerance regardless of DB activity — the |diff| ≤
    demo/real fee-gap tolerance applies only to the EQUITY identity below (which
    nets in realised pnl/fees), not the static starting-capital header."""
    from polaris.scripts.dashboard.snapshot import collect_snapshot
    from polaris.storage.schema import ALL_DDL

    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    conn.close()

    snap = collect_snapshot(db_path=db_path)
    venue_sum = (
        snap.starting_capital_okx
        + snap.starting_capital_capital
        + snap.starting_capital_alpaca
    )
    assert snap.starting_capital == pytest.approx(venue_sum, abs=1e-6)
    assert snap.starting_capital == pytest.approx(_ALPACA_TOTAL, abs=1e-6)


def test_header_equity_reconciles_to_stream_equity_within_fee_gap(
    tmp_path: Path,
) -> None:
    """Header ``equity_now`` vs Σ per-stream ``equity_usd`` must agree within the
    demo/real fee-schedule gap — the header curve nets demo-actual fees
    (``_build_dual_equity_curve`` demo leg) while the per-stream lane (via
    ``_per_stream_summary``) nets the SAME stored ``fills.fee_usd``, so on a
    single-venue (OKX) fill set both sides use the identical fee and the gap is
    ~0; a wider tolerance (the OKX demo-vs-real 70bps/10bps spread on the fill's
    notional) is asserted so this stays robust to future multi-schedule fills."""
    from polaris.core.economics.fees import demo_fee_usd, real_fee_usd
    from polaris.scripts.dashboard.snapshot import collect_snapshot
    from polaris.storage.schema import ALL_DDL

    db_path = tmp_path / "polaris.sqlite"
    conn = sqlite3.connect(db_path, isolation_level=None)
    for stmt in ALL_DDL:
        conn.execute(stmt)
    now_ms = int(time.time() * 1000)
    size_usd = 1000.0
    conn.execute(
        "INSERT INTO fills (fill_id, venue, instrument_id, strategy_id, side, "
        " size_usd, fill_price, fee_usd, slippage_bps, ts_ms, order_id, "
        " contribution_id, pnl_usd, is_close, base_qty, quote_qty, state) "
        "VALUES ('f1', 'okx', 'okx:BTC-USDT', 'volume_burst', 'sell', "
        f"        {size_usd}, 60000.0, {demo_fee_usd('okx', size_usd)}, 1.0, ?, "
        "        'o1', NULL, 25.0, 1, 0.001666, 100.0, 'filled')",
        (now_ms,),
    )
    conn.close()

    snap = collect_snapshot(db_path=db_path)
    stream_equity_sum = sum(s.equity_usd for s in snap.streams)
    fee_gap = abs(demo_fee_usd("okx", size_usd) - real_fee_usd("okx", size_usd))
    assert abs(snap.equity_now - stream_equity_sum) <= fee_gap + 1e-6, (
        f"header equity_now={snap.equity_now} vs Σstreams={stream_equity_sum} "
        f"diverges by more than the demo/real fee gap ({fee_gap})"
    )
