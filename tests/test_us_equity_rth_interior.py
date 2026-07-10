"""§0b — us_equity_rth_interior (SSOT reinforcement, hard dependency #2).

DEMO/PAPER virtual. NY-local zoneinfo path only (no fixed-UTC hardcode — the
RTH window shifts UTC-wise with DST, same requirement as
``us_equity_session_state``). This is an INTEGRITY-style entry-window
narrowing for strategy #4 (equity_bb_meanrev_15m, Wave 1b — not built by this
infra wave): interior excludes the first/last ``edge_minutes`` of RTH (open
gap volatility / closing-auction noise), never a P&L throttle, never touches
an existing position.
"""

from __future__ import annotations

import datetime as dt

from polaris.core.sessions.equity_session_gate import (
    RTH_CLOSE_LOCAL_MINUTES,
    RTH_EDGE_EXCLUSION_MINUTES,
    RTH_OPEN_LOCAL_MINUTES,
    us_equity_rth_interior,
)


def _et_ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    """A unix ts at a fixed America/New_York LOCAL wall-clock instant."""
    from zoneinfo import ZoneInfo

    local = dt.datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    return int(local.timestamp())


# ---------------------------------------------------------------------------
# A — constant + default edge
# ---------------------------------------------------------------------------


def test_edge_exclusion_default_is_30_minutes() -> None:
    assert RTH_EDGE_EXCLUSION_MINUTES == 30


# ---------------------------------------------------------------------------
# B — interior window boundaries (default 30min edge): [10:00, 15:30) ET
# ---------------------------------------------------------------------------


def test_interior_false_at_rth_open_930() -> None:
    """09:30 ET is RTH open but inside the excluded first-30min edge."""
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 9, 30)) is False


def test_interior_false_at_959() -> None:
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 9, 59)) is False


def test_interior_true_at_1000_open_edge_boundary() -> None:
    """10:00 ET == 09:30 + 30min edge — interior open boundary INCLUSIVE."""
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 10, 0)) is True


def test_interior_true_midday() -> None:
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 12, 30)) is True


def test_interior_true_at_1529_just_before_close_edge() -> None:
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 15, 29)) is True


def test_interior_false_at_1530_close_edge_boundary() -> None:
    """15:30 ET == 16:00 - 30min edge — interior close boundary EXCLUSIVE."""
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 15, 30)) is False


def test_interior_false_at_1550_still_rth_but_excluded() -> None:
    """15:50 ET is still official RTH (< 16:00) but inside the excluded
    closing-edge window — proves interior is STRICTLY narrower than RTH."""
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 15, 50)) is False


# ---------------------------------------------------------------------------
# C — outside RTH entirely -> False (delegates to us_equity_session_state)
# ---------------------------------------------------------------------------


def test_interior_false_pre_market() -> None:
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 8, 0)) is False


def test_interior_false_after_hours() -> None:
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 17, 0)) is False


def test_interior_false_deep_overnight() -> None:
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 2, 0)) is False


def test_interior_false_on_weekend() -> None:
    """Saturday ET-RTH-hours — the existing weekend-aware SSOT already reports
    'closed', so interior must also be False (no separate weekend check needed
    here — it flows through us_equity_session_state)."""
    assert us_equity_rth_interior(_et_ts(2026, 6, 6, 12, 0)) is False  # Sat


# ---------------------------------------------------------------------------
# D — custom edge_minutes (caller override, e.g. 0 == full RTH)
# ---------------------------------------------------------------------------


def test_interior_zero_edge_equals_full_rth() -> None:
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 9, 30), edge_minutes=0) is True
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 15, 59), edge_minutes=0) is True


def test_interior_custom_edge_narrows_further() -> None:
    # edge_minutes=60 -> interior [10:30, 15:00) ET.
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 10, 29), edge_minutes=60) is False
    assert us_equity_rth_interior(_et_ts(2026, 6, 3, 11, 0), edge_minutes=60) is True


# ---------------------------------------------------------------------------
# E — DST correctness (NY-local zoneinfo, not a fixed-UTC hardcode). Same
#     discriminating instants the RTH-window test suite uses.
# ---------------------------------------------------------------------------


def test_interior_dst_correct_january_est() -> None:
    # 10:00 ET in January (EST) == 15:00 UTC.
    assert us_equity_rth_interior(_et_ts(2026, 1, 15, 10, 0)) is True
    assert us_equity_rth_interior(_et_ts(2026, 1, 15, 9, 45)) is False


def test_interior_dst_correct_july_edt() -> None:
    # 10:00 ET in July (EDT) == 14:00 UTC — different UTC instant, same
    # NY-local interior boundary.
    assert us_equity_rth_interior(_et_ts(2026, 7, 15, 10, 0)) is True
    assert us_equity_rth_interior(_et_ts(2026, 7, 15, 9, 45)) is False


def test_interior_no_fixed_utc_hardcode_source_lint() -> None:
    """Source-level guard: the function must live in the NY-zoneinfo section of
    the module (not reuse the DST-naive RTH_OPEN_UTC_MINUTES/RTH_CLOSE_UTC_MINUTES
    constants, which are explicitly documented as offline-fallback-only)."""
    from pathlib import Path

    src = Path("polaris/core/sessions/equity_session_gate.py").read_text()
    fn_start = src.index("def us_equity_rth_interior")
    fn_body = src[fn_start : fn_start + 900]
    assert "RTH_OPEN_UTC_MINUTES" not in fn_body
    assert "RTH_CLOSE_UTC_MINUTES" not in fn_body


# ---------------------------------------------------------------------------
# F — module surface / re-export hygiene
# ---------------------------------------------------------------------------


def test_new_names_exported_in_all() -> None:
    import polaris.core.sessions.equity_session_gate as mod

    assert "RTH_EDGE_EXCLUSION_MINUTES" in mod.__all__
    assert "us_equity_rth_interior" in mod.__all__


def test_window_constants_unchanged() -> None:
    # Additive-only guard: the existing RTH open/close local-minute constants
    # (consumed elsewhere) must be untouched.
    assert RTH_OPEN_LOCAL_MINUTES == 9 * 60 + 30
    assert RTH_CLOSE_LOCAL_MINUTES == 16 * 60
