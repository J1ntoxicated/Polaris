import time
from invasion.spot import exit_spot


def _pos(entry_px=100.0, qty=1.0, entry_ts=None, peak_px=None):
    entry_ts = entry_ts or int(time.time())
    return {
        "id": 1, "ticker": "BTC", "entry_px": entry_px, "qty": qty,
        "entry_ts": entry_ts, "peak_px": peak_px or entry_px,
        "active_signals_at_entry": ["bb", "micro", "queue"],
    }


def test_tp_triggers_on_tp_pct_threshold():
    pos = _pos(entry_px=100.0)
    r = exit_spot.evaluate_exit(
        position=pos, current_px=100.15,  # +0.15%
        thresholds={"optimal_tp_pct": 0.001}, current_signals=set(),
        max_hold_sec=300)
    assert r == "TP"


def test_hard_stop_triggers_below_threshold():
    pos = _pos(entry_px=100.0)
    r = exit_spot.evaluate_exit(
        position=pos, current_px=99.6,  # -0.4%
        thresholds={"optimal_hard_stop_pct": -0.003},
        current_signals=set(), max_hold_sec=300)
    assert r == "HARD_STOP"


def test_time_triggers_after_max_hold():
    pos = _pos(entry_px=100.0, entry_ts=int(time.time()) - 400)
    r = exit_spot.evaluate_exit(
        position=pos, current_px=100.05,
        thresholds={"optimal_max_hold_sec": 300},
        current_signals=set(), max_hold_sec=300)
    assert r == "TIME"


def test_trail_triggers_after_peak_giveback():
    pos = _pos(entry_px=100.0, peak_px=100.20)
    r = exit_spot.evaluate_exit(
        position=pos, current_px=100.10,  # gave back 50% of peak gain
        thresholds={"optimal_trail_giveback_pct": 0.0005},
        current_signals=set(), max_hold_sec=300)
    assert r == "TRAIL"


def test_signal_fade_triggers_when_majority_gone():
    pos = _pos(entry_px=100.0)
    r = exit_spot.evaluate_exit(
        position=pos, current_px=100.05,
        thresholds={}, current_signals={"bb"},  # 1 of 3 left
        max_hold_sec=300)
    assert r == "SIGNAL_FADE"


def test_no_exit_when_in_range():
    pos = _pos(entry_px=100.0)
    r = exit_spot.evaluate_exit(
        position=pos, current_px=100.05,
        thresholds={"optimal_tp_pct": 0.001},
        current_signals={"bb", "micro", "queue"},
        max_hold_sec=300)
    assert r is None


def test_cell_threshold_overrides_default():
    pos = _pos(entry_px=100.0)
    # Cell threshold 0.0005 (tighter than default 0.001)
    r = exit_spot.evaluate_exit(
        position=pos, current_px=100.06,  # +0.06% > 0.05%
        thresholds={"optimal_tp_pct": 0.0005},
        current_signals={"bb", "micro"}, max_hold_sec=300)
    assert r == "TP"


def test_priority_tp_over_trail():
    """If TP and TRAIL both triggered, TP wins (higher priority)."""
    pos = _pos(entry_px=100.0, peak_px=100.30)
    r = exit_spot.evaluate_exit(
        position=pos, current_px=100.20,  # TP at 0.001 → 100.10 hit;
                                            # TRAIL: peak 100.30, gave back 0.10
        thresholds={"optimal_tp_pct": 0.001,
                     "optimal_trail_giveback_pct": 0.0005},
        current_signals={"bb"}, max_hold_sec=300)
    assert r == "TP"
