"""Alpaca ledger-venue re-sync — adopt-by-default (audit1 P0-4 ②).

DEMO/PAPER. 6 Alpaca positions ($73.6k: BB/ABBV/RMBI/WHG/TPCS/SEVN) sat
venue-untracked because the startup ``reconcile_venue_positions`` import was
gated OFF by default behind 3 documented (2026-06-01) bugs. Bug #1 (sync
``_run_coro`` vs a running event loop) is fixed (dedicated
``ThreadPoolExecutor`` thread); bug #2 (insta-close) does not reproduce (the
exit engine reads ``positions``/``fills`` directly, not any position-specific
FSM); bug #3 (Capital ``deal_id=None`` close-error loop) is Capital-specific
and stays OUT OF SCOPE — this flip is ALPACA-ONLY (Capital's own gap is
untouched). ADOPT is the default: a reconciled venue holding is put under
exit-engine management, never liquidated (flow_not_block).
"""

from __future__ import annotations

import pytest

from polaris.scripts.production_paper_loop import alpaca_reconcile_import_enabled


def test_default_unset_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLARIS_RECONCILE_VENUE_IMPORT", raising=False)
    assert alpaca_reconcile_import_enabled() is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, True),  # unset → ADOPT is the default (Jin: adopt 기본)
        ("", True),  # empty env value → treated as unset → ON
        ("1", True),
        ("anything-else", True),  # only an explicit "0" opts out
        ("0", False),  # explicit kill switch
    ],
)
def test_flag_semantics(
    raw: str | None, expected: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARIS_RECONCILE_VENUE_IMPORT", raising=False)
    if raw is not None:
        monkeypatch.setenv("POLARIS_RECONCILE_VENUE_IMPORT", raw)
    assert alpaca_reconcile_import_enabled() is expected
