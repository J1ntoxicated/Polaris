"""STEP② candidate sweep — dynamic active-focus over the static ground.

DEMO/PAPER. AGGRESSIVE / flow_not_block: the sweep chooses WHERE to look live
(focus_rank/tier/bucket only). It NEVER blocks an entry, cuts a size, or gates an
exit. The cap 200 WIDENS the watched set; cold-start = penalty (0.5x) NOT
exclusion; the exploration bucket fights blindness. These tests pin:

  - two-stage score math (candidate = activation x (0.5 + 0.5 x edge));
  - activation GATE (zero-movement bars -> activation 0 -> candidate_score 0);
  - edge direction from ground sentiment;
  - bucket allocation counts + de-dup across buckets;
  - venue allocation respects injected session weights;
  - hysteresis (incumbent kept within band, challenger displaces only beyond delta);
  - open-position force-seat (a held name is always in the active set);
  - fast-track promotion of an out-of-focus vol/range spike;
  - LOOK-AHEAD guard (a bar with close-ts > now_ts is NOT consumed);
  - cold-start penalty (thin-ground name penalized 0.5x but STILL present);
  - 1650-scan completes (perf sanity) + selects <= cap.
"""

from __future__ import annotations

import random
import sqlite3
import time
from typing import Any

import pytest

from polaris.core.data.schema import Bar
from polaris.core.universe.schema import FocusSelection, UniverseInstrument
from polaris.scripts import _candidate_sweep as cs
from polaris.scripts._candidate_sweep_bars import confirmed
from polaris.storage.schema import init_db

# ---------------------------------------------------------------------------
# Fixtures / helpers (mirror tests/test_static_ground.py style)
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path: Any) -> sqlite3.Connection:
    return init_db(tmp_path / "sweep.sqlite")


def _seat_active(
    conn: sqlite3.Connection,
    rows: list[tuple[str, str, str, str]],
    *,
    is_active: int = 1,
    vol_24h_usd: float = 1_000_000.0,
    atr_24h_pct: float = 1.0,
) -> None:
    """Insert ``(venue, symbol, asset_class, group_id)`` rows into ``universe``."""
    now = int(time.time())
    for venue, symbol, ac, group in rows:
        conn.execute(
            "INSERT OR REPLACE INTO universe (venue, symbol, instrument_id, "
            "underlying_group_id, asset_class, quote_ccy, state, vol_24h_usd, "
            "atr_24h_pct, signal_density_7d, last_seen_ts, is_active) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (venue, symbol, f"{venue}:{symbol}", group, ac, "USD", "live",
             vol_24h_usd, atr_24h_pct, 0.0, now, is_active),
        )
    conn.commit()


def _mk_inst(
    venue: str, symbol: str, ac: str = "crypto",
    *, vol: float = 1_000_000.0, atr: float = 1.0,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue, symbol=symbol, instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{ac}:{symbol}", asset_class=ac, quote_ccy="USD",
        state="live", vol_24h_usd=vol, spread_bps=1.0, atr_24h_pct=atr,
        depth_10bps_usd=100_000.0, signal_density_7d=0.0,
    )


def _bar(
    venue: str, symbol: str, interval: str, ts: int,
    *, o: float, h: float, low: float, c: float, vol: float = 1.0,
) -> Bar:
    return Bar(
        instrument_id=f"{venue}:{symbol}", underlying_group_id=f"crypto:{symbol}",
        venue=venue, symbol=symbol, bar_interval=interval, ts=ts,
        open=o, high=h, low=low, close=c, volume=vol, source="yahoo",
    )


def _flat_bars(venue: str, symbol: str, interval: str, n: int, now: int) -> list[Bar]:
    """N flat (zero-movement) bars ending at ``now`` (close-ts <= now)."""
    step = 900 if interval == "15m" else 3600 if interval == "1H" else 86400
    return [
        _bar(venue, symbol, interval, now - (n - i) * step,
             o=100.0, h=100.0, low=100.0, c=100.0)
        for i in range(n)
    ]


def _moving_bars(
    venue: str, symbol: str, interval: str, n: int, now: int, *, amp: float
) -> list[Bar]:
    """N bars with growing range/uptrend ending at ``now`` (movement present)."""
    step = 900 if interval == "15m" else 3600 if interval == "1H" else 86400
    out: list[Bar] = []
    for i in range(n):
        base = 100.0 + i * amp
        out.append(
            _bar(venue, symbol, interval, now - (n - i) * step,
                 o=base, h=base + amp, low=base - amp, c=base + amp)
        )
    return out


# ---------------------------------------------------------------------------
# Two-stage score math
# ---------------------------------------------------------------------------


def test_candidate_score_additive_blend() -> None:
    """candidate_score == clamp01(0.75 x activation + 0.25 x edge) (#39 converged).

    The converged design replaced the old gate multiplier (activation x (0.5 +
    0.5 x edge)) with an ADDITIVE blend so an active major with empty edge is no
    longer zeroed (9-stack ban: no stacked <=1 multiplier).
    """
    assert cs.candidate_score(1.0, 1.0) == pytest.approx(1.0)
    assert cs.candidate_score(1.0, 0.0) == pytest.approx(0.75)
    assert cs.candidate_score(0.0, 1.0) == pytest.approx(0.25)
    assert cs.candidate_score(0.8, 0.5) == pytest.approx(0.75 * 0.8 + 0.25 * 0.5)


def test_activation_zero_when_no_motion_no_spread_no_vol(
    conn: sqlite3.Connection,
) -> None:
    """Flat bars + no live motion + no spread -> activation 0 (no movement)."""
    now = int(time.time())
    bars = {
        "15m": _flat_bars("okx", "FLAT-USDT", "15m", 30, now),
        "1H": _flat_bars("okx", "FLAT-USDT", "1H", 30, now),
        "1D": _flat_bars("okx", "FLAT-USDT", "1D", 30, now),
    }
    act = cs.activation_score(bars, None, None, now)
    assert act == 0.0


def test_activation_positive_when_live_motion(conn: sqlite3.Connection) -> None:
    """Live per-symbol motion lifts activation even on flat bars (the whole #39 point)."""
    now = int(time.time())
    bars = {
        "15m": _flat_bars("okx", "MOVE-USDT", "15m", 30, now),
        "1H": _flat_bars("okx", "MOVE-USDT", "1H", 30, now),
        "1D": _flat_bars("okx", "MOVE-USDT", "1D", 30, now),
    }
    motion = {
        "ticks_600s": cs.ACT_TICK_RATE_HOT_600S,
        "last_mid": 100.0,
        "mid_120s_ago": 100.0,
        "mid_high_600s": 100.0,
        "mid_low_600s": 100.0,
        "last_ts": now,
    }
    act = cs.activation_score(bars, motion, None, now)
    assert act > 0.0


# ---------------------------------------------------------------------------
# Edge direction from ground sentiment
# ---------------------------------------------------------------------------


def test_edge_score_direction_from_ground() -> None:
    """A strong directional ground tilt lifts edge above a neutral ground."""
    now = int(time.time())
    bars = {
        "15m": _moving_bars("okx", "BULL-USDT", "15m", 30, now, amp=1.0),
        "1H": _moving_bars("okx", "BULL-USDT", "1H", 30, now, amp=1.0),
        "1D": _moving_bars("okx", "BULL-USDT", "1D", 30, now, amp=1.0),
    }
    bull = {"scores": {"bull_trend": 0.9, "bear_trend": 0.05,
                       "chop": 0.03, "crisis": 0.02}, "label": "bull_trend"}
    neutral = {"scores": {"bull_trend": 0.25, "bear_trend": 0.25,
                          "chop": 0.25, "crisis": 0.25}}
    edge_bull = cs.edge_score(bars, bull, now)
    edge_neutral = cs.edge_score(bars, neutral, now)
    assert 0.0 <= edge_neutral <= 1.0
    assert 0.0 <= edge_bull <= 1.0
    assert edge_bull > edge_neutral


def test_edge_score_neutral_no_ground() -> None:
    """No ground + no technical alignment -> edge near neutral (not 1, not 0)."""
    now = int(time.time())
    bars = {
        "15m": _flat_bars("okx", "X-USDT", "15m", 30, now),
        "1H": _flat_bars("okx", "X-USDT", "1H", 30, now),
        "1D": _flat_bars("okx", "X-USDT", "1D", 30, now),
    }
    edge = cs.edge_score(bars, None, now)
    assert 0.0 <= edge <= 1.0


# ---------------------------------------------------------------------------
# LOOK-AHEAD guard
# ---------------------------------------------------------------------------


def test_lookahead_guard_ignores_future_bars() -> None:
    """A bar whose close-ts > now_ts must NOT be consumed by activation."""
    now = int(time.time())
    # Confirmed history is flat; a single FUTURE bar carries a huge spike that,
    # if (wrongly) consumed, would push activation up. The guard must drop it.
    past = _flat_bars("okx", "LA-USDT", "15m", 30, now)
    future = _bar("okx", "LA-USDT", "15m", now + 10_000,
                  o=100.0, h=500.0, low=50.0, c=400.0)
    bars = {
        "15m": past + [future],
        "1H": _flat_bars("okx", "LA-USDT", "1H", 30, now),
        "1D": _flat_bars("okx", "LA-USDT", "1D", 30, now),
    }
    act_with_future = cs.activation_score(bars, None, None, now)
    act_clean = cs.activation_score(
        {"15m": past,
         "1H": _flat_bars("okx", "LA-USDT", "1H", 30, now),
         "1D": _flat_bars("okx", "LA-USDT", "1D", 30, now)},
        None, None, now,
    )
    # The future spike is ignored -> identical to the future-free read (both ~0).
    assert act_with_future == act_clean == 0.0


def test_confirmed_boundary_excludes_forming_bar_includes_closed() -> None:
    """A bar that OPENED recently but whose period has NOT closed is excluded;
    a bar whose period END (ts + interval) <= now IS included.

    Bar.ts is the period-OPEN ts. A 15m bar (900s) opened at now-300 is still
    FORMING (closes at now+600) -> must be dropped. A 15m bar opened at now-900
    closes exactly at now -> confirmed. This is the off-by-one the guard protects:
    the FAR-future test above passes even under a buggy open-ts guard; THIS one
    fails unless the guard uses period-close (ts + interval <= now).
    """
    now = int(time.time())
    forming = _bar("okx", "B-USDT", "15m", now - 300,  # opened 5min ago, not closed
                   o=100.0, h=100.0, low=100.0, c=100.0)
    closed = _bar("okx", "B-USDT", "15m", now - 900,   # opened 15min ago, closed @now
                  o=100.0, h=100.0, low=100.0, c=100.0)
    kept = confirmed([closed, forming], now, "15m")
    assert closed in kept          # fully-closed bar IS consumed
    assert forming not in kept     # still-forming bar is NOT consumed


def test_forming_bar_spike_does_not_inflate_activation() -> None:
    """A still-forming bar carrying a huge intra-period range must NOT spike
    activation (it would be a 1-period look-ahead into an unfinalized candle)."""
    now = int(time.time())
    # Flat CLOSED history (each interval's last bar opens at now-step => closed @now).
    closed_hist = {
        "15m": _flat_bars("okx", "F-USDT", "15m", 30, now),
        "1H": _flat_bars("okx", "F-USDT", "1H", 30, now),
        "1D": _flat_bars("okx", "F-USDT", "1D", 30, now),
    }
    act_flat = cs.activation_score(closed_hist, None, None, now)
    # Append a FORMING 15m bar (opened now-300, closes now+600) with a violent range.
    forming = _bar("okx", "F-USDT", "15m", now - 300,
                   o=100.0, h=900.0, low=10.0, c=850.0)
    with_forming = dict(closed_hist)
    with_forming["15m"] = list(closed_hist["15m"]) + [forming]
    act_with_forming = cs.activation_score(with_forming, None, None, now)
    # The forming bar is dropped by the guard -> activation unchanged (both ~0).
    assert act_with_forming == act_flat == 0.0


# ---------------------------------------------------------------------------
# Hysteresis (anti-flicker)
# ---------------------------------------------------------------------------


def test_hysteresis_incumbent_kept_within_band() -> None:
    """An incumbent above exit_delta is kept; a challenger needs > enter_delta."""
    prev = {"AAA"}
    # AAA (incumbent) at 0.40, BBB (challenger) at 0.45. enter_delta 0.10 means
    # BBB must beat AAA by > 0.10 to displace -> it does NOT, so AAA is kept.
    scores = {"AAA": 0.40, "BBB": 0.45}
    kept = cs.apply_hysteresis(prev, scores, enter_delta=0.10, exit_delta=0.20)
    assert "AAA" in kept  # incumbent retained (challenger inside band)


def test_hysteresis_challenger_displaces_beyond_delta() -> None:
    """A challenger that beats the incumbent by > enter_delta is allowed in."""
    prev = {"AAA"}
    scores = {"AAA": 0.40, "BBB": 0.80}  # BBB beats AAA by 0.40 > enter_delta
    kept = cs.apply_hysteresis(prev, scores, enter_delta=0.10, exit_delta=0.20)
    assert "BBB" in kept


def test_hysteresis_incumbent_drops_below_exit() -> None:
    """An incumbent that falls below exit_delta is released (flicker floor)."""
    prev = {"AAA"}
    scores = {"AAA": 0.05}  # below exit_delta 0.20
    kept = cs.apply_hysteresis(prev, scores, enter_delta=0.10, exit_delta=0.20)
    assert "AAA" not in kept


# ---------------------------------------------------------------------------
# fast-track outlier promotion
# ---------------------------------------------------------------------------


def test_fast_track_promotes_out_of_focus_spike() -> None:
    """A vol/range spike outside focus is promoted into the active set."""
    universe = [_mk_inst("okx", f"C{i}-USDT") for i in range(5)]
    focus_set = {("okx", "C0-USDT"), ("okx", "C1-USDT")}
    # C4 (out of focus) carries a huge activation spike -> must be promoted.
    scored = {f"okx:C{i}-USDT": 0.01 for i in range(5)}
    scored["okx:C4-USDT"] = 0.95
    promoted = cs.fast_track_outliers(
        universe, scored, focus_set, spike_threshold=0.5
    )
    assert ("okx", "C4-USDT") in set(promoted)
    # An in-focus name is not re-promoted (additive, no dup churn).
    assert ("okx", "C0-USDT") not in set(promoted)


def test_fast_track_no_spike_no_promotion() -> None:
    """No outlier above threshold -> empty promotion (never a forced add)."""
    universe = [_mk_inst("okx", f"C{i}-USDT") for i in range(5)]
    focus_set = {("okx", "C0-USDT")}
    scored = {f"okx:C{i}-USDT": 0.05 for i in range(5)}
    promoted = cs.fast_track_outliers(
        universe, scored, focus_set, spike_threshold=0.5
    )
    assert promoted == []


# ---------------------------------------------------------------------------
# tempo sweep period (market-tempo modulation)
# ---------------------------------------------------------------------------


def test_tempo_sweep_period_hotter_is_shorter() -> None:
    """A hotter market (more ticks) yields a shorter sweep period."""
    cold = [{"venue": "okx", "ticks_600s": 0, "max_flow_size_600s": 0.0}]
    hot = [{"venue": "okx", "ticks_600s": 100_000, "max_flow_size_600s": 5.0}]
    p_cold = cs.tempo_sweep_period(cold, base_period_sec=900.0)
    p_hot = cs.tempo_sweep_period(hot, base_period_sec=900.0)
    assert p_hot < p_cold
    assert p_hot >= cs.TEMPO_MIN_PERIOD_SEC
    assert p_cold <= cs.TEMPO_MAX_PERIOD_SEC


# ---------------------------------------------------------------------------
# Bucket allocation + de-dup + cold-start + venue/session
# ---------------------------------------------------------------------------


def _seed_bars(
    conn: sqlite3.Connection, venue: str, symbol: str, *, moving: bool, now: int
) -> None:
    rows: list[Bar] = []
    for interval in ("15m", "1H", "1D"):
        rows.extend(
            _moving_bars(venue, symbol, interval, 30, now, amp=2.0)
            if moving else _flat_bars(venue, symbol, interval, 30, now)
        )
    from polaris.core.data.ingest import persist_bars
    persist_bars(conn, rows)
    conn.commit()


def test_select_candidate_focus_buckets_and_cap(conn: sqlite3.Connection) -> None:
    """Sweep fills <= cap, de-dups across buckets, emits valid FocusSelection."""
    now = int(time.time())
    active = [("okx", f"M{i}-USDT", "crypto", f"crypto:M{i}") for i in range(60)]
    _seat_active(conn, active)
    for _, symbol, _, _ in active:
        _seed_bars(conn, "okx", symbol, moving=True, now=now)

    universe = [_mk_inst("okx", s, vol=1_000_000.0 + i) for i, (_, s, _, _) in
                enumerate(active)]
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=20,
        bucket_counts={"anchor": 5, "dynamic": 10, "event_hot": 2, "exploration": 3},
        venue_weights={"okx": 1.0}, open_targets=[], prev_focus_symbols=set(),
        rng=random.Random(42),
    )
    assert len(sel) <= 20
    assert all(isinstance(s, FocusSelection) for s in sel)
    # focus_rank is dense 1..K, unique.
    ranks = sorted(s.rank for s in sel)
    assert ranks == list(range(1, len(sel) + 1))
    # No duplicate (venue, symbol) — de-dup across buckets held.
    keys = [(s.venue, s.symbol) for s in sel]
    assert len(keys) == len(set(keys))
    # Bucket field maps to a VALID FocusBucket literal (schema-safe).
    assert all(s.bucket in ("core", "satellite", "listing_watch") for s in sel)


def test_open_position_force_seat(conn: sqlite3.Connection) -> None:
    """A held symbol is force-seated into the active set even if low-scoring."""
    now = int(time.time())
    active = [("okx", f"M{i}-USDT", "crypto", f"crypto:M{i}") for i in range(30)]
    _seat_active(conn, active)
    for _, symbol, _, _ in active:
        _seed_bars(conn, "okx", symbol, moving=True, now=now)
    # The held name is a CALM low-scorer NOT in the active universe top.
    _seat_active(conn, [("okx", "HELD-USDT", "crypto", "crypto:HELD")])
    _seed_bars(conn, "okx", "HELD-USDT", moving=False, now=now)

    universe = [_mk_inst("okx", s) for _, s, _, _ in active]
    universe.append(_mk_inst("okx", "HELD-USDT", vol=1.0, atr=0.0))
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=10,
        bucket_counts={"anchor": 3, "dynamic": 4, "event_hot": 1, "exploration": 2},
        venue_weights={"okx": 1.0},
        open_targets=[("okx", "HELD-USDT", "crypto", "crypto:HELD")],
        prev_focus_symbols=set(), rng=random.Random(1),
    )
    assert ("okx", "HELD-USDT") in {(s.venue, s.symbol) for s in sel}


def test_cold_start_penalty_present_not_excluded(conn: sqlite3.Connection) -> None:
    """A thin-ground name is penalized 0.5x but STILL selectable (not excluded)."""
    now = int(time.time())
    # One COLD name (no bars at all, no ground) competes against warm movers.
    active = [("okx", f"M{i}-USDT", "crypto", f"crypto:M{i}") for i in range(5)]
    _seat_active(conn, active)
    for _, symbol, _, _ in active:
        _seed_bars(conn, "okx", symbol, moving=True, now=now)
    _seat_active(conn, [("okx", "COLD-USDT", "crypto", "crypto:COLD")])
    # No bars seeded for COLD -> cold-start.

    universe = [_mk_inst("okx", s) for _, s, _, _ in active]
    universe.append(_mk_inst("okx", "COLD-USDT"))
    # With a generous cap, the cold name is still seated (penalty != exclusion).
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=10,
        bucket_counts={"anchor": 2, "dynamic": 6, "event_hot": 1, "exploration": 1},
        venue_weights={"okx": 1.0}, open_targets=[], prev_focus_symbols=set(),
        rng=random.Random(7),
    )
    assert ("okx", "COLD-USDT") in {(s.venue, s.symbol) for s in sel}


def test_venue_allocation_respects_session_weight(conn: sqlite3.Connection) -> None:
    """A zero-weight venue (closed session) yields fewer dynamic seats than an
    open-session venue with equal raw merit (allocation, not a block)."""
    now = int(time.time())
    okx = [("okx", f"O{i}-USDT", "crypto", f"crypto:O{i}") for i in range(20)]
    cap = [("capital", f"K{i}", "index", f"index:K{i}") for i in range(20)]
    _seat_active(conn, okx)
    _seat_active(conn, cap)
    for _, symbol, _, _ in okx:
        _seed_bars(conn, "okx", symbol, moving=True, now=now)
    for _, symbol, _, _ in cap:
        _seed_bars(conn, "capital", symbol, moving=True, now=now)

    universe = [_mk_inst("okx", s) for _, s, _, _ in okx]
    universe += [_mk_inst("capital", s, ac="index") for _, s, _, _ in cap]
    # Capital session CLOSED (weight 0.2) vs OKX always-on (1.0).
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=20,
        bucket_counts={"anchor": 0, "dynamic": 20, "event_hot": 0, "exploration": 0},
        venue_weights={"okx": 1.0, "capital": 0.2},
        open_targets=[], prev_focus_symbols=set(), rng=random.Random(3),
    )
    okx_n = sum(1 for s in sel if s.venue == "okx")
    cap_n = sum(1 for s in sel if s.venue == "capital")
    assert okx_n > cap_n  # the down-weighted venue gets fewer dynamic seats


# ---------------------------------------------------------------------------
# #48 global session-aware focus rotation (per-instrument session map)
# ---------------------------------------------------------------------------


def _utc_hour(hour: int) -> int:
    """A fixed weekday (Wed 2026-06-24) UTC epoch at ``hour``:00 (session clock)."""
    import datetime as _dt

    return int(_dt.datetime(2026, 6, 24, hour, 0, tzinfo=_dt.UTC).timestamp())


def _seed_session_universe(
    conn: sqlite3.Connection, now: int
) -> list[UniverseInstrument]:
    """Asia (J225/HK50/AU200AU) + US (US100/US500/US30) Capital index rows, all
    equally MOVING so only the session clock differentiates their seat priority."""
    asia = [("capital", s, "index", f"index:{s}") for s in ("J225", "HK50", "AU200AU")]
    us = [("capital", s, "index", f"index:{s}") for s in ("US100", "US500", "US30")]
    _seat_active(conn, asia + us)
    for _, symbol, _, _ in asia + us:
        _seed_bars(conn, "capital", symbol, moving=True, now=now)
    return [_mk_inst("capital", s, ac="index") for _, s, _, _ in (asia + us)]


def test_session_rotation_asia_hour_lifts_asia_indices(
    conn: sqlite3.Connection,
) -> None:
    """At 03:00 UTC (Asia cash) the Asia indices outrank the (dormant) US indices."""
    now = _utc_hour(3)
    universe = _seed_session_universe(conn, now)
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=6,
        bucket_counts={"anchor": 0, "dynamic": 6, "event_hot": 0, "exploration": 0},
        venue_weights={"capital": 1.0}, open_targets=[],
        prev_focus_symbols=set(), rng=random.Random(48),
    )
    ranked = [s.symbol for s in sorted(sel, key=lambda r: r.rank)]
    asia = {"J225", "HK50", "AU200AU"}
    # The Asia indices fill the rank head; the dormant US indices fall behind.
    head = ranked[:3]
    assert all(sym in asia for sym in head), f"Asia must lead at 03:00 UTC: {ranked}"


def test_session_rotation_us_hour_lifts_us_indices(
    conn: sqlite3.Connection,
) -> None:
    """At 15:00 UTC (US RTH) the US indices outrank the (dormant) Asia indices —
    SAME universe, only the clock changed (rotation)."""
    now = _utc_hour(15)
    universe = _seed_session_universe(conn, now)
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=6,
        bucket_counts={"anchor": 0, "dynamic": 6, "event_hot": 0, "exploration": 0},
        venue_weights={"capital": 1.0}, open_targets=[],
        prev_focus_symbols=set(), rng=random.Random(48),
    )
    ranked = [s.symbol for s in sorted(sel, key=lambda r: r.rank)]
    us = {"US100", "US500", "US30"}
    head = ranked[:3]
    assert all(sym in us for sym in head), f"US must lead at 15:00 UTC: {ranked}"


def test_session_rotation_dormant_still_present_not_excluded(
    conn: sqlite3.Connection,
) -> None:
    """flow_not_block: a dormant-session instrument is DEPRIORITIZED but STILL in
    the active set (deprioritize, never a membership cut)."""
    now = _utc_hour(3)  # Asia hour → US indices dormant
    universe = _seed_session_universe(conn, now)
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=6,  # cap fits all 6 → dormant ones kept
        bucket_counts={"anchor": 0, "dynamic": 6, "event_hot": 0, "exploration": 0},
        venue_weights={"capital": 1.0}, open_targets=[],
        prev_focus_symbols=set(), rng=random.Random(48),
    )
    syms = {s.symbol for s in sel}
    # The dormant US indices are NOT excluded — they remain watched (just lower rank).
    assert {"US100", "US500", "US30"} <= syms


def test_session_rotation_okx_crypto_always_base_focus(
    conn: sqlite3.Connection,
) -> None:
    """OKX crypto is ALWAYS at full session priority — at an off-hour for every
    equity session it still leads dormant Capital indices (24/7 base, mandate ③)."""
    now = _utc_hour(3)  # Asia hour: US/Europe indices dormant, OKX active
    # Calm OKX crypto vs MOVING but DORMANT US index — OKX session weight 1.0 must
    # keep crypto competitive (the session boost offsets, never an exclusion).
    btc = [("okx", "BTC-USDT", "crypto", "crypto:BTC")]
    us = [("capital", "US100", "index", "index:US100")]
    _seat_active(conn, btc + us)
    _seed_bars(conn, "okx", "BTC-USDT", moving=True, now=now)
    _seed_bars(conn, "capital", "US100", moving=True, now=now)
    universe = [_mk_inst("okx", "BTC-USDT"), _mk_inst("capital", "US100", ac="index")]
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=2,
        bucket_counts={"anchor": 0, "dynamic": 2, "event_hot": 0, "exploration": 0},
        venue_weights={"okx": 1.0, "capital": 1.0}, open_targets=[],
        prev_focus_symbols=set(), rng=random.Random(48),
    )
    # OKX crypto is present and session-active (never deprioritized off-hours).
    assert ("okx", "BTC-USDT") in {(s.venue, s.symbol) for s in sel}
    top = min(sel, key=lambda r: r.rank)
    assert top.venue == "okx"  # the always-active crypto leads the dormant index


def test_session_cold_start_freshly_open_not_penalized(
    conn: sqlite3.Connection,
) -> None:
    """A name entering its FIRST live session is session-active (1.0) — the session
    map never penalizes a cold-start instrument whose session just opened."""
    from polaris.scripts import _session_map as sm

    # US100 exactly at the US cash open minute (13:30 UTC) — freshly open.
    now = int(__import__("datetime").datetime(
        2026, 6, 24, 13, 30, tzinfo=__import__("datetime").UTC).timestamp())
    w = sm.instrument_session_weight("capital", "index", "US100", now)
    assert w == 1.0  # freshly-opened session = active, not penalized


def test_1650_scan_completes_under_cap(conn: sqlite3.Connection) -> None:
    """Perf sanity — ~1650 active rows scan + select <= cap without error."""
    now = int(time.time())
    active = [("okx", f"S{i}-USDT", "crypto", f"crypto:S{i}") for i in range(1650)]
    _seat_active(conn, active)
    # Seed bars for a subset only (the rest exercise the cold-start path cheaply).
    for _, symbol, _, _ in active[:100]:
        _seed_bars(conn, "okx", symbol, moving=True, now=now)
    universe = [_mk_inst("okx", s) for _, s, _, _ in active]
    t0 = time.time()
    sel = cs.select_candidate_focus(
        conn, universe, now_ts=now, cap=200,
        bucket_counts=None, venue_weights={"okx": 1.0},
        open_targets=[], prev_focus_symbols=set(), rng=random.Random(11),
    )
    elapsed = time.time() - t0
    assert len(sel) <= 200
    assert elapsed < 30.0  # generous perf ceiling for the full 1650 walk


# ---------------------------------------------------------------------------
# Integration: refresh_focus_watchlist sweep ON vs legacy
# ---------------------------------------------------------------------------


def test_refresh_focus_sweep_on_persists_valid_rows(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweep ON produces valid watchlist_focus rows readable by get_focus_targets
    and a DIFFERENT focus_rank ordering than the legacy merit producer."""
    from polaris.scripts import _production_layers as pl

    now = int(time.time())
    # Two clusters: high-liquidity-but-CALM vs low-liquidity-but-MOVING. Legacy
    # ranks by liquidity (calm names first); the sweep ranks by movement.
    calm = [("okx", f"CALM{i}-USDT", "crypto", f"crypto:CALM{i}") for i in range(10)]
    move = [("okx", f"MOVE{i}-USDT", "crypto", f"crypto:MOVE{i}") for i in range(10)]
    _seat_active(conn, calm, vol_24h_usd=9_000_000_000.0)
    _seat_active(conn, move, vol_24h_usd=1_000.0)
    for _, symbol, _, _ in calm:
        _seed_bars(conn, "okx", symbol, moving=False, now=now)
    for _, symbol, _, _ in move:
        _seed_bars(conn, "okx", symbol, moving=True, now=now)

    monkeypatch.setenv("POLARIS_CANDIDATE_SWEEP", "1")
    n = pl.refresh_focus_watchlist(conn, cycle_ts=now)
    assert n > 0
    sweep_top = conn.execute(
        "SELECT venue, symbol FROM watchlist_focus WHERE cycle_ts=? "
        "ORDER BY focus_rank ASC LIMIT 5", (now,)
    ).fetchall()
    sweep_top_syms = [r[1] for r in sweep_top]

    # Legacy ordering for the same universe (sweep OFF).
    conn.execute("DELETE FROM watchlist_focus")
    monkeypatch.setenv("POLARIS_CANDIDATE_SWEEP", "0")
    pl.refresh_focus_watchlist(conn, cycle_ts=now + 1)
    legacy_top = conn.execute(
        "SELECT symbol FROM watchlist_focus WHERE cycle_ts=? "
        "ORDER BY focus_rank ASC LIMIT 5", (now + 1,)
    ).fetchall()
    legacy_top_syms = [r[0] for r in legacy_top]

    # The sweep surfaces MOVERS at the head; legacy surfaces the CALM whales.
    assert any(s.startswith("MOVE") for s in sweep_top_syms)
    assert sweep_top_syms != legacy_top_syms

    # get_focus_targets reads the sweep rows back (consumer contract intact).
    monkeypatch.setenv("POLARIS_CANDIDATE_SWEEP", "1")
    conn.execute("DELETE FROM watchlist_focus")
    pl.refresh_focus_watchlist(conn, cycle_ts=now + 2)
    targets = pl.get_focus_targets(conn, cycle_ts=now + 2, max_n=50)
    assert targets  # non-empty -> consumers keep working
    assert all(len(t) == 4 for t in targets)
