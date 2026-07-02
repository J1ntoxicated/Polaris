"""Read-only derivation of the timeframe-scaled thesis-drift materiality floor.

[[1d_exit_horizon_fix_2026-07-02]] — P0-2 follow-up. Computes the 10-bar
``|drift| = |last_close - first_close| / first_close`` distribution per
``bar_interval`` from the live ``bars`` table (OKX + Capital only — excludes
the Alpaca penny-stock long tail so the estimate matches the venue mix the
exit engine actually trades) using the SAME rolling-window estimator
``_recent_tick_drift`` (bar recalc) / ``bars_atr_pct`` (ATR ruler) use
elsewhere, then prints the q65 ratio-to-1m used to seed
``exit_params.EXIT_THESIS_DRIFT_FLOOR_RATIO``.

Usage (read-only — never writes):
    python3 tools/research/drift_floor_quantiles.py [path/to/polaris_live.sqlite]

Measurement / calibration only — this script is NOT imported by any runtime
path; it exists so the hardcoded ``EXIT_THESIS_DRIFT_FLOOR_RATIO`` constants in
``exit_params.py`` have a reproducible, evidence-based derivation.
"""

from __future__ import annotations

import sqlite3
import sys

WINDOW = 10
QUANTILE = 0.65
TIMEFRAMES = ("1m", "5m", "15m", "1H", "1D")


def rolling_abs_drift(conn: sqlite3.Connection, timeframe: str) -> list[float]:
    """10-bar |drift| samples for every non-Alpaca instrument on ``timeframe``."""
    rows = conn.execute(
        """
        SELECT instrument_id, ts, close FROM bars
        WHERE bar_interval = ? AND instrument_id NOT LIKE 'alpaca:%'
        ORDER BY instrument_id, ts ASC
        """,
        (timeframe,),
    ).fetchall()
    drifts: list[float] = []
    cur_instrument: str | None = None
    window: list[float] = []
    for instrument_id, _ts, close in rows:
        if instrument_id != cur_instrument:
            cur_instrument = instrument_id
            window = []
        window.append(float(close))
        if len(window) > WINDOW:
            window.pop(0)
        if len(window) == WINDOW and window[0] > 0.0:
            drifts.append(abs((window[-1] - window[0]) / window[0]))
    return drifts


def quantile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(p * len(sorted_values)))
    return sorted_values[idx]


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/polaris_live.sqlite"
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    q65_by_tf: dict[str, float] = {}
    for tf in TIMEFRAMES:
        samples = sorted(rolling_abs_drift(conn, tf))
        q65_by_tf[tf] = quantile(samples, QUANTILE)
        print(f"{tf}: n={len(samples)} q65={q65_by_tf[tf]:.5f}")
    base = q65_by_tf.get("1m", 0.0)
    if base <= 0.0:
        print("1m sample empty — cannot derive ratios")
        return
    print("\nEXIT_THESIS_DRIFT_FLOOR_RATIO (ratio to 1m q65):")
    for tf in TIMEFRAMES:
        print(f'    "{tf}": {q65_by_tf[tf] / base:.3f},')


if __name__ == "__main__":
    main()
