"""Day 6 — P1.0 ignition entry point sanity tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from polaris.scripts.ignite_p1 import IgnitionReport, ignite, main
from polaris.storage.schema import init_db


def test_ignite_p1_imports_main_callable() -> None:
    assert callable(main)


@pytest.mark.asyncio
async def test_ignite_p1_dry_run_bootstrap_only(tmp_path: Path) -> None:
    """``paper=False`` must bootstrap + return a non-empty report."""
    db_path = tmp_path / "polaris.sqlite"
    # Pre-init so the DB exists for the layer 0 read-only probe.
    conn = init_db(db_path)
    conn.close()
    report = await ignite(
        duration_sec=1.0,
        tick_sec=0.5,
        db_path=db_path,
        paper=False,
        full_pipeline=True,
    )
    assert isinstance(report, IgnitionReport)
    assert report.db_path == db_path
    assert report.learner_count == 3
    # Smoke not started in dry-run.
    assert report.smoke_exit is None


@pytest.mark.asyncio
async def test_ignite_p1_dry_run_creates_db_if_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.sqlite"
    report = await ignite(db_path=db_path, paper=False)
    assert db_path.exists()
    assert report.universe_focus_count == 0  # empty fresh DB


@pytest.mark.asyncio
async def test_ignite_p1_paper_mode_short_run(tmp_path: Path) -> None:
    """Paper mode with a tiny duration runs the loop once and exits."""
    db_path = tmp_path / "polaris.sqlite"
    init_db(db_path).close()

    # Patch the smoke loop's bar fetch so it doesn't hit the real OKX endpoint.
    import polaris.scripts.smoke_paper_loop as mod
    from polaris.scripts.smoke_paper_loop import FocusEntry, _stub_bars

    async def _fake_fetch(entry: FocusEntry, *, limit: int = 200):
        return _stub_bars(60)

    original = mod._fetch_bars_for_symbol
    mod._fetch_bars_for_symbol = _fake_fetch  # type: ignore[assignment]
    try:
        report = await ignite(
            duration_sec=0.5,
            tick_sec=0.1,
            db_path=db_path,
            paper=True,
            full_pipeline=True,
        )
        assert isinstance(report, IgnitionReport)
        # smoke_exit either 0 or 1 — both are valid (1 = no closed_trades).
        assert report.smoke_exit in (0, 1)
    finally:
        mod._fetch_bars_for_symbol = original  # type: ignore[assignment]


def test_ignite_main_dry_run_exits_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "polaris.sqlite"
    init_db(db_path).close()
    rc = main(["--db", str(db_path)])
    assert rc == 0
