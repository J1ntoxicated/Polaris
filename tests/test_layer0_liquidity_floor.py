"""Layer 0 — universe-eligibility liquidity floor (Jin-approved 2026-06-22).

A per-venue *instrument-quality* eligibility floor: an instrument reaches the
active set only if it clears its venue's liquidity floor (OKX: max-spread + min
$vol + min depth; Alpaca: min price + min $vol; Capital: max-spread only). This
is a membership test on WHICH instruments are tradeable-quality — the SAME KIND
of hard keep as the existing ``state=='live'`` / OKX-USDT gates — NOT a per-signal
block, size-cut, or entry-veto. The ATR-z signal-richness rank still orders the
survivors (flow_not_block preserved at the signal/entry level).

DEMO/PAPER only. Aggressive bias preserved: on every ELIGIBLE name the bot trades
as hard as before; the floor only excludes loss-certain junk (175bp spread >
~0.3R edge = negative-expectancy round-trip). NOT a regulatory/defensive throttle.
"""

from __future__ import annotations

import inspect
import re

import pytest

from polaris.core.universe._ranking import rank_active_universe
from polaris.core.universe.schema import (
    LIQFLOOR_ENV_PREFIX,
    UniverseInstrument,
    liquidity_floor_for_venue,
    passes_liquidity_floor,
)

NOW = 1_780_000_000


def _inst(
    symbol: str,
    *,
    venue: str = "okx",
    asset_class: str = "crypto",
    quote_ccy: str = "USDT",
    state: str = "live",
    vol: float = 5e8,
    spread_bps: float = 2.0,
    atr_pct: float = 4.0,
    depth: float = 200_000.0,
    last_price: float = 0.0,
) -> UniverseInstrument:
    return UniverseInstrument(
        venue=venue,
        symbol=symbol,
        instrument_id=f"{venue}:{symbol}",
        underlying_group_id=f"{asset_class}:{symbol.split('-')[0]}",
        asset_class=asset_class,
        quote_ccy=quote_ccy,
        state=state,
        vol_24h_usd=vol,
        spread_bps=spread_bps,
        atr_24h_pct=atr_pct,
        depth_10bps_usd=depth,
        last_price=last_price,
    )


# ---------------------------------------------------------------------------
# predicate — per-venue eligibility
# ---------------------------------------------------------------------------


def test_okx_wide_spread_junk_is_ineligible() -> None:
    """BNT-class 175bp spread (and 19912bp NC-USDT) fail the OKX spread floor."""
    bnt = _inst("BNT-USDT", spread_bps=175.0)
    nc = _inst("NC-USDT", spread_bps=19912.0)
    assert passes_liquidity_floor(bnt) is False
    assert passes_liquidity_floor(nc) is False


def test_okx_micro_volume_junk_is_ineligible() -> None:
    """A vol≈$0 name (DOGS/GEAR/ID live) fails the OKX min-$vol floor."""
    micro = _inst("GEAR-USDT", vol=50_000.0, spread_bps=5.0)
    assert passes_liquidity_floor(micro) is False


def test_okx_no_depth_junk_is_ineligible() -> None:
    """A name with a real book but $1-50 top-of-book depth fails the depth floor."""
    thin = _inst("ID-USDT", vol=5e8, spread_bps=5.0, depth=50.0)
    assert passes_liquidity_floor(thin) is False


def test_okx_liquid_major_passes() -> None:
    """A tight-spread, deep, high-$vol crypto major clears the OKX floor."""
    btc = _inst("BTC-USDT", vol=8e8, spread_bps=1.0, depth=400_000.0)
    assert passes_liquidity_floor(btc) is True


def test_alpaca_penny_is_ineligible() -> None:
    """Sub-$1 gappers (TNON $0.59, ADTX $0.017) fail the Alpaca min-price floor."""
    tnon = _inst("TNON", venue="alpaca", asset_class="equity", quote_ccy="USD",
                 vol=654_000.0, spread_bps=2.0, last_price=0.59)
    adtx = _inst("ADTX", venue="alpaca", asset_class="equity", quote_ccy="USD",
                 vol=37_000.0, spread_bps=2.0, last_price=0.017)
    assert passes_liquidity_floor(tnon) is False
    assert passes_liquidity_floor(adtx) is False


def test_alpaca_micro_dollar_volume_is_ineligible() -> None:
    """A high-ATR penny with $26k-$54k $-volume (ABVE/INLF) fails the $vol floor."""
    abve = _inst("ABVE", venue="alpaca", asset_class="equity", quote_ccy="USD",
                 vol=54_000.0, atr_pct=377.0, last_price=4.0)
    assert passes_liquidity_floor(abve) is False


def test_alpaca_large_cap_passes() -> None:
    """A real megacap (AAPL: high $vol, >$1 price) clears the Alpaca floor."""
    aapl = _inst("AAPL", venue="alpaca", asset_class="equity", quote_ccy="USD",
                 vol=5e9, spread_bps=2.0, last_price=190.0)
    assert passes_liquidity_floor(aapl) is True


def test_alpaca_unknown_price_not_dropped() -> None:
    """last_price==0 (un-enriched) is NOT a drop — flow_not_block on missing data."""
    unenriched = _inst("WIDE", venue="alpaca", asset_class="equity", quote_ccy="USD",
                       vol=5e7, last_price=0.0)
    assert passes_liquidity_floor(unenriched) is True


def test_alpaca_spread_not_floored() -> None:
    """Alpaca spread is a placeholder (2.0) → the spread axis is OFF for Alpaca."""
    # A would-be-wide spread on Alpaca must NOT exclude (floor.max_spread_bps==0).
    row = _inst("MSFT", venue="alpaca", asset_class="equity", quote_ccy="USD",
                vol=5e9, spread_bps=999.0, last_price=400.0)
    assert passes_liquidity_floor(row) is True


def test_capital_spread_floor_only() -> None:
    """Capital floors on SPREAD only; native vol/depth==0 must NOT zero the venue."""
    wide = _inst("EXOTIC", venue="capital", asset_class="forex", quote_ccy="USD",
                 vol=0.0, spread_bps=120.0, depth=0.0)
    major = _inst("EURUSD", venue="capital", asset_class="forex", quote_ccy="USD",
                  vol=0.0, spread_bps=8.0, depth=0.0)
    assert passes_liquidity_floor(wide) is False
    assert passes_liquidity_floor(major) is True  # vol/depth==0 not floored


def test_unknown_venue_no_floor() -> None:
    """An unregistered venue gets an all-zero (permissive) floor — smoke-safe."""
    row = _inst("X", venue="smokevenue", spread_bps=9999.0, vol=1.0, depth=1.0)
    assert passes_liquidity_floor(row) is True


# ---------------------------------------------------------------------------
# integration — floor applied at the active-set chokepoint (rank_active_universe)
# ---------------------------------------------------------------------------


def test_rank_excludes_junk_keeps_majors() -> None:
    """A 175bp / $0.017-penny / sub-$vol name never reaches the active set."""
    btc = _inst("BTC-USDT", vol=8e8, spread_bps=1.0, depth=400_000.0)
    eth = _inst("ETH-USDT", vol=6e8, spread_bps=1.5, depth=300_000.0)
    bnt = _inst("BNT-USDT", vol=8e8, spread_bps=175.0, depth=400_000.0)  # wide spread
    gear = _inst("GEAR-USDT", vol=50_000.0, spread_bps=5.0)  # micro vol
    out = rank_active_universe([btc, eth, bnt, gear], top_n=10)
    syms = {i.symbol for i in out}
    assert "BTC-USDT" in syms and "ETH-USDT" in syms
    assert "BNT-USDT" not in syms and "GEAR-USDT" not in syms


def test_rank_atr_ordering_preserved_within_eligible_set() -> None:
    """ATR-z signal-richness still orders the SURVIVORS (volatility-seeking kept).

    Two eligible majors with identical vol/depth but different ATR: the higher-ATR
    one must rank ABOVE the lower-ATR one — the floor doesn't kill the ATR rank.
    """
    hi = _inst("HI-USDT", vol=5e8, spread_bps=2.0, depth=200_000.0, atr_pct=9.0)
    lo = _inst("LO-USDT", vol=5e8, spread_bps=2.0, depth=200_000.0, atr_pct=2.0)
    out = rank_active_universe([lo, hi], top_n=2)
    assert [i.symbol for i in out] == ["HI-USDT", "LO-USDT"]


def test_eligible_instrument_unchanged_signal_byte_identical() -> None:
    """An eligible row passes through rank UNMODIFIED (no size/signal mutation)."""
    btc = _inst("BTC-USDT", vol=8e8, spread_bps=1.0, depth=400_000.0, atr_pct=5.0)
    out = rank_active_universe([btc], top_n=5)
    assert out == [btc]  # frozen dataclass identity → no field touched


def test_env_override_relaxes_spread_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """POLARIS_LIQFLOOR_OKX_MAX_SPREAD_BPS widens the cap (/debate-tunable)."""
    bnt = _inst("BNT-USDT", vol=8e8, spread_bps=175.0, depth=400_000.0)
    assert passes_liquidity_floor(bnt) is False  # default 30bp excludes
    monkeypatch.setenv(f"{LIQFLOOR_ENV_PREFIX}OKX_MAX_SPREAD_BPS", "200")
    assert passes_liquidity_floor(bnt) is True  # relaxed → eligible


def test_env_override_zero_disables_axis(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 0 override disables an axis entirely (no min-vol floor on OKX)."""
    micro = _inst("GEAR-USDT", vol=50_000.0, spread_bps=5.0)
    assert passes_liquidity_floor(micro) is False
    monkeypatch.setenv(f"{LIQFLOOR_ENV_PREFIX}OKX_MIN_VOL_24H_USD", "0")
    assert passes_liquidity_floor(micro) is True


def test_floor_for_venue_defaults() -> None:
    """Per-venue defaults match the measured-junk calibration."""
    okx = liquidity_floor_for_venue("okx")
    assert okx.max_spread_bps == 30.0 and okx.min_vol_24h_usd == 20_000_000.0
    alpaca = liquidity_floor_for_venue("alpaca")
    assert alpaca.min_price == 1.0 and alpaca.max_spread_bps == 0.0
    capital = liquidity_floor_for_venue("capital")
    assert capital.max_spread_bps == 40.0 and capital.min_vol_24h_usd == 0.0


# ---------------------------------------------------------------------------
# boundary pin — the floor is a pre-signal-membership *quality* test ONLY.
#
# It must read ONLY UniverseInstrument microstructure-quality fields (the venue
# key + spread_bps / vol_24h_usd / depth_10bps_usd / last_price) and NEVER any
# signal / size / strength / expectancy / conviction input. These guards pin
# that boundary so a future edit cannot smuggle expectancy-coupling (a per-
# signal block / size-cut / entry-veto) into what is meant to be a universe-
# membership (is-it-tradeable) gate. flow_not_block: the floor decides WHICH
# instruments are tradeable-quality, never how a signal on an eligible name is
# sized or whether it fires.
# ---------------------------------------------------------------------------

# The ONLY instrument attributes the floor is permitted to consult.
_ALLOWED_FLOOR_FIELDS = frozenset(
    {"venue", "spread_bps", "vol_24h_usd", "depth_10bps_usd", "last_price"}
)
# Attributes that, if ever referenced, would mean expectancy/signal coupling
# leaked into the pre-signal-membership boundary.
_FORBIDDEN_FLOOR_TOKENS = frozenset(
    {
        "signal_density_7d",
        "signal_strength",
        "strength",
        "conviction",
        "size",
        "notional",
        "expectancy",
        "edge",
        "pnl",
        "risk_pct",
    }
)


def test_floor_takes_only_a_universe_instrument() -> None:
    """The predicate's signature accepts ONLY a UniverseInstrument — there is no
    signal/size/strength parameter for a future edit to thread expectancy in."""
    sig = inspect.signature(passes_liquidity_floor)
    params = list(sig.parameters.values())
    assert len(params) == 1, "the floor must take exactly one argument (the row)"
    (only,) = params
    assert only.annotation in ("UniverseInstrument", UniverseInstrument), (
        "the sole argument must be a UniverseInstrument — never a signal/size input"
    )


def test_floor_source_reads_only_quality_fields() -> None:
    """The predicate body references ONLY whitelisted quality attributes of the
    row, and none of the forbidden signal/size/strength/expectancy tokens — a
    static pin against smuggling expectancy-coupling into the membership gate."""
    src = inspect.getsource(passes_liquidity_floor)
    # No forbidden expectancy/signal/size token may appear anywhere in the body.
    for tok in _FORBIDDEN_FLOOR_TOKENS:
        assert tok not in src, (
            f"passes_liquidity_floor must not reference {tok!r} — that would "
            "couple the pre-signal-membership floor to expectancy/signal/size "
            "(a block/size-cut/entry-veto), violating the boundary"
        )
    # Every `ins.<attr>` access in the body must be a whitelisted quality field.
    accessed = set(re.findall(r"\bins\.(\w+)", src))
    leaked = accessed - _ALLOWED_FLOOR_FIELDS
    assert not leaked, (
        f"passes_liquidity_floor reads non-quality instrument field(s) {leaked} "
        "— the floor may consult ONLY venue + spread/vol/depth/price"
    )


def test_floor_verdict_independent_of_signal_density() -> None:
    """Behavioural pin: varying a NON-quality field (signal_density_7d) never
    flips the verdict — the floor judges tradeability, not signal richness."""
    base = _inst("BTC-USDT", vol=8e8, spread_bps=1.0, depth=400_000.0)
    rich = _inst("BTC-USDT", vol=8e8, spread_bps=1.0, depth=400_000.0)
    object.__setattr__(rich, "signal_density_7d", 999.0)  # frozen dataclass
    assert passes_liquidity_floor(base) == passes_liquidity_floor(rich) is True
    # And on a junk row: signal richness cannot rescue an un-tradeable spread.
    junk = _inst("BNT-USDT", vol=8e8, spread_bps=175.0, depth=400_000.0)
    junk_rich = _inst("BNT-USDT", vol=8e8, spread_bps=175.0, depth=400_000.0)
    object.__setattr__(junk_rich, "signal_density_7d", 999.0)
    assert passes_liquidity_floor(junk) == passes_liquidity_floor(junk_rich) is False
