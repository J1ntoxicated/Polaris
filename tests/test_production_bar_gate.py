"""Bar-advance dispatch gate + fanout idle backoff — pure predicate tests.

DEMO/PAPER · AGGRESSIVE bias preserved · flow_not_block · 9-stack ban untouched.
These are COMPUTE-SCHEDULING predicates, never a trade throttle: a close-only
(non-``evaluates_in_progress_bar``) strategy's ``generate_raw_signal`` is a pure
function of ``MarketView.bars`` (ADR-008 signal-generator-only), so re-invoking
it when the newest bar ts has NOT advanced is an identical recomputation — the
gate below skips that redundant work with a PROVEN same-info->same-decision
invariant (no signal opportunity is ever lost). ``evaluates_in_progress_bar``
strategies (session/orderbook/funding/VIX-driven) are exempt and always
re-evaluate. Idle backoff (spec C) only defers the FANOUT scheduling cadence
when a venue produced zero new bars AND zero signals this tick; the moment a
new bar lands (ingest, same tick) or a session flips OPEN, backoff resets to 0.
"""

from __future__ import annotations

from polaris.scripts._production_bar_gate import (
    bar_advance_due,
    idle_backoff_next_due,
    idle_backoff_reset,
    newest_bar_ts_by_venue,
    venues_with_new_bars,
)

# ---------------------------------------------------------------------------
# spec_a — bar-advance gate
# ---------------------------------------------------------------------------


def test_bar_advance_due_first_observation_always_true() -> None:
    """A key never seen before (no prior last_eval) always evaluates once —
    covers the reboot-catchup: a fresh ``last_eval_bar_ts_by_key`` dict must
    not suppress the very first tick after (re)start."""
    assert bar_advance_due(last_eval_ts=None, latest_bar_ts=1_000) is True


def test_bar_advance_due_same_ts_is_false() -> None:
    """Same bar ts twice in a row (bar has not advanced) → skip re-eval."""
    assert bar_advance_due(last_eval_ts=1_000, latest_bar_ts=1_000) is False


def test_bar_advance_due_new_ts_is_true() -> None:
    """A newer bar ts than the last recorded eval → advance → re-eval."""
    assert bar_advance_due(last_eval_ts=1_000, latest_bar_ts=1_060) is True


def test_bar_advance_due_ts_regression_is_false() -> None:
    """A ts that somehow went backward (e.g. a corrected/replaced bar with an
    older ts) must NOT trigger a re-eval — only a genuine advance does."""
    assert bar_advance_due(last_eval_ts=1_060, latest_bar_ts=1_000) is False


def test_bar_advance_due_in_progress_ohlc_update_same_ts_is_false() -> None:
    """Risk #2 (design doc): an exchange updates an IN-PROGRESS bar's OHLC via
    INSERT OR REPLACE keyed on (instrument_id, bar_interval, ts) — the ts is
    UNCHANGED for a partial-bar refresh. A close-only strategy must NOT
    re-fire on that refresh (its bar has not truly CLOSED/advanced yet) —
    this is the intended semantics, not a bug: the strategy only cares about
    the bar's ts-identity, and generate_raw_signal is re-run once that
    identity changes (a new, distinct ts = a closed bar)."""
    # Same ts observed twice (an in-place OHLC update) → still no re-eval.
    assert bar_advance_due(last_eval_ts=1_000, latest_bar_ts=1_000) is False
    # The NEXT bar (a genuinely new, distinct ts) → re-eval fires exactly once.
    assert bar_advance_due(last_eval_ts=1_000, latest_bar_ts=1_060) is True


# ---------------------------------------------------------------------------
# spec_c — idle fanout backoff
# ---------------------------------------------------------------------------


def test_idle_backoff_next_due_streak_zero_returns_base_interval() -> None:
    assert idle_backoff_next_due(
        now_mono=100.0, streak=0, base_sec=5.0, factor=2.0, max_sec=30.0
    ) == 105.0


def test_idle_backoff_next_due_escalates_5_10_20_30() -> None:
    expected_deltas = [5.0, 10.0, 20.0, 30.0, 30.0]  # capped at max_sec
    for streak, delta in enumerate(expected_deltas):
        due = idle_backoff_next_due(
            now_mono=0.0, streak=streak, base_sec=5.0, factor=2.0, max_sec=30.0
        )
        assert due == delta, f"streak={streak} expected delta={delta} got={due}"


def test_idle_backoff_next_due_invalid_env_values_fall_back_to_base() -> None:
    """A non-positive base/factor/max never produces a zero or negative
    interval (which would busy-loop) — degrades to the raw base_sec step."""
    due = idle_backoff_next_due(
        now_mono=0.0, streak=3, base_sec=5.0, factor=0.0, max_sec=30.0
    )
    assert due >= 5.0


def test_idle_backoff_reset_on_activity() -> None:
    """Any of: new bars persisted, a bar-advance, or a session OPEN edge resets
    the streak to 0 (immediate 5s-cadence resume next tick)."""
    assert idle_backoff_reset(bars_persisted=1, had_signal=False, session_opened=False)
    assert idle_backoff_reset(bars_persisted=0, had_signal=True, session_opened=False)
    assert idle_backoff_reset(bars_persisted=0, had_signal=False, session_opened=True)
    assert not idle_backoff_reset(
        bars_persisted=0, had_signal=False, session_opened=False
    )


# ---------------------------------------------------------------------------
# spec_c — per-venue new-bar activity probe (idle-reset input)
# ---------------------------------------------------------------------------


def _conn_with_bars(rows: list[tuple[str, str, str, int]]) -> object:
    """rows: (venue, symbol, bar_interval, ts). Minimal in-memory bars table."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE bars (instrument_id TEXT, underlying_group_id TEXT, "
        "venue TEXT, symbol TEXT, bar_interval TEXT, ts INTEGER, open REAL, "
        "high REAL, low REAL, close REAL, volume REAL)"
    )
    for venue, symbol, bar_interval, ts in rows:
        conn.execute(
            "INSERT INTO bars (instrument_id, underlying_group_id, venue, "
            "symbol, bar_interval, ts, open, high, low, close, volume) "
            "VALUES (?,?,?,?,?,?,1,1,1,1,1)",
            (f"{venue}:{symbol}", "g", venue, symbol, bar_interval, ts),
        )
    conn.commit()
    return conn


def test_newest_bar_ts_by_venue_groups_by_venue() -> None:
    conn = _conn_with_bars(
        [
            ("okx", "BTC-USDT", "1m", 100),
            ("okx", "ETH-USDT", "1m", 160),
            ("capital", "GOLD", "1m", 90),
        ]
    )
    snapshot = newest_bar_ts_by_venue(conn, bar_interval="1m")  # type: ignore[arg-type]
    assert snapshot == {"okx": 160, "capital": 90}


def test_newest_bar_ts_by_venue_filters_by_interval() -> None:
    conn = _conn_with_bars(
        [("okx", "BTC-USDT", "1m", 100), ("okx", "BTC-USDT", "1H", 999_999)]
    )
    snapshot = newest_bar_ts_by_venue(conn, bar_interval="1m")  # type: ignore[arg-type]
    assert snapshot == {"okx": 100}


def test_venues_with_new_bars_detects_advance() -> None:
    before = {"okx": 100, "capital": 90}
    after = {"okx": 160, "capital": 90}
    assert venues_with_new_bars(before, after) == {"okx"}


def test_venues_with_new_bars_first_observation_counts_as_new() -> None:
    before: dict[str, int] = {}
    after = {"okx": 100}
    assert venues_with_new_bars(before, after) == {"okx"}


def test_venues_with_new_bars_no_change_is_empty() -> None:
    before = {"okx": 100}
    after = {"okx": 100}
    assert venues_with_new_bars(before, after) == set()
