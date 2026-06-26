"""Layer 0 — venue liquidity floor (Jin 2026-06-22; WATCH/TRADE decouple 2026-06-24).

A per-venue *instrument-quality* liquidity floor (OKX: max-spread + min $vol + min
depth; Alpaca: min price + min $vol; Capital: max-spread only). WATCH/TRADE
DECOUPLE (2026-06-24): the floor moved OFF the active/WATCH gate (it no longer
pre-cuts the candidate set — that strangled breadth to OKX 2) and ONTO the curator
TRADE gate (``EntranceJudge`` floor-aware ``trade_eligible``). A sub-floor name is
now WATCHED / streamed / dashboarded (flow_not_block, breadth unlock) but its
order-open is deferred (slippage protection at the single ``_run_entries`` seam).
The predicate ``passes_liquidity_floor`` itself is UNCHANGED — only its CONSUMER
moved. NOT a per-signal block, size-cut, or entry-veto; the ATR-z rank still orders
every watched name.

DEMO/PAPER only. Aggressive bias preserved: on every TRADE-eligible name the bot
trades as hard as before; the floor only gates ORDER-OPEN on loss-certain junk
(175bp spread > ~0.3R edge = negative-expectancy round-trip), never observation.
NOT a regulatory/defensive throttle.
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
    """A REAL wide spread (BNT-class 175bp) fails the OKX spread floor. The
    degenerate ~19912bp NC-USDT value is a top-of-book DATA ERROR (above the
    abnormal sentinel) → UNKNOWN, the spread axis is skipped (flow_not_block:
    never block on a broken datum); it is covered by
    ``test_okx_abnormal_spread_is_unknown_not_blocked``."""
    bnt = _inst("BNT-USDT", spread_bps=175.0)
    assert passes_liquidity_floor(bnt) is False


def test_okx_micro_volume_junk_is_ineligible() -> None:
    """A vol≈$0 name (DOGS/GEAR/ID live) fails the OKX min-$vol floor."""
    micro = _inst("GEAR-USDT", vol=50_000.0, spread_bps=5.0)
    assert passes_liquidity_floor(micro) is False


def test_okx_depth_floor_disabled_broken_metric_never_blocks() -> None:
    """The OKX depth axis is DISABLED (default floor 0) because ``depth_10bps_usd``
    is a BROKEN top-of-book single-quote proxy ($6-19 for BTC/ETH/SOL — the world's
    deepest books), not a real 10bps depth. A live-DB measurement showed every OKX
    major (BTC=$14k, ETH=$6.8k, SOL=$9.9k) under the old $25k floor → 0 trade-
    eligible. With the axis off, a thin top-of-book number can no longer drop a
    liquid name (flow_not_block: never block on a broken metric — vol/spread carry
    the OKX quality gate; real L2 depth arrives in P1)."""
    # ID-USDT live top-of-book proxy ($50) used to FAIL the old $25k floor.
    thin = _inst("ID-USDT", vol=5e8, spread_bps=5.0, depth=50.0)
    assert passes_liquidity_floor(thin) is True
    # The world's deepest book (BTC $14k live proxy) is now eligible (was blocked).
    btc_live = _inst("BTC-USDT", vol=8e8, spread_bps=1.0, depth=14_398.0)
    assert passes_liquidity_floor(btc_live) is True


def test_okx_depth_floor_default_is_zero() -> None:
    """The OKX venue depth floor default is 0 (axis disabled) — the broken-metric
    block is gone. vol + spread remain the OKX quality gate."""
    assert liquidity_floor_for_venue("okx").min_depth_10bps_usd == 0.0


def test_okx_abnormal_spread_is_unknown_not_blocked() -> None:
    """A degenerate ~19900bps spread (NC-USDT live: 19904bps) is a top-of-book DATA
    ERROR (bid≈0 → (ask-bid)/mid≈20000bps), not a real round-trip cost. It is
    treated as UNKNOWN — the spread axis is SKIPPED (flow_not_block: never block on
    a broken datum), so a name with a real $vol still flows. A REAL wide spread
    (175bps BNT) below the abnormal sentinel still blocks (genuine quality cut)."""
    nc = _inst("NC-USDT", vol=3.1e7, spread_bps=19904.0, depth=200_000.0)
    assert passes_liquidity_floor(nc) is True  # data error → unknown, flows
    bnt = _inst("BNT-USDT", vol=8e8, spread_bps=175.0, depth=400_000.0)
    assert passes_liquidity_floor(bnt) is False  # real wide spread still blocked


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


def test_alpaca_real_wide_spread_is_ineligible() -> None:
    """Alpaca spread is now REAL (plumbed from snapshot latestQuote) → the spread
    axis is ON. Measured untradeable junk (MGN 8800bps=88% round-trip, WHLR 3310,
    ARQQ 2913) physically exceeds any possible edge — it fails the Alpaca spread
    floor (100bps cut). flow_not_block-coherent: a known-bad spread is excluded as
    a QUALITY membership test, never a loss/risk-based block."""
    mgn = _inst("MGN", venue="alpaca", asset_class="equity", quote_ccy="USD",
                vol=5e7, spread_bps=8800.0, last_price=4.0)
    whlr = _inst("WHLR", venue="alpaca", asset_class="equity", quote_ccy="USD",
                 vol=5e7, spread_bps=3310.0, last_price=4.0)
    assert passes_liquidity_floor(mgn) is False
    assert passes_liquidity_floor(whlr) is False


def test_alpaca_tight_real_spread_passes() -> None:
    """A liquid Alpaca name with a real tight spread (~5bps) clears the floor."""
    row = _inst("MSFT", venue="alpaca", asset_class="equity", quote_ccy="USD",
                vol=5e9, spread_bps=5.0, last_price=400.0)
    assert passes_liquidity_floor(row) is True


def test_alpaca_placeholder_spread_passes() -> None:
    """The no-real-quote placeholder (2.0) is far below the 100bps cut → an
    un-enriched row still passes (flow_not_block: never drop on a missing datum)."""
    row = _inst("UNQUOTED", venue="alpaca", asset_class="equity", quote_ccy="USD",
                vol=5e7, spread_bps=2.0, last_price=4.0)
    assert passes_liquidity_floor(row) is True


def test_alpaca_spread_env_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    """POLARIS_LIQFLOOR_ALPACA_MAX_SPREAD_BPS moves the cut (/debate-tunable)."""
    junk = _inst("ARQQ", venue="alpaca", asset_class="equity", quote_ccy="USD",
                 vol=5e7, spread_bps=2913.0, last_price=4.0)
    assert passes_liquidity_floor(junk) is False  # default 100bps excludes
    monkeypatch.setenv(f"{LIQFLOOR_ENV_PREFIX}ALPACA_MAX_SPREAD_BPS", "5000")
    assert passes_liquidity_floor(junk) is True  # relaxed → eligible


def test_okx_capital_floors_unchanged_by_alpaca_spread_change() -> None:
    """The Alpaca spread change must NOT leak into OKX/Capital — their spread
    floors stay 30/40bps (a 50bps OKX row still excluded, a 20bps OKX row passes;
    a 50bps Capital row still excluded, a 20bps Capital row passes)."""
    okx_wide = _inst("WIDE-USDT", spread_bps=50.0)
    okx_tight = _inst("TIGHT-USDT", spread_bps=20.0)
    assert passes_liquidity_floor(okx_wide) is False  # 50 > 30
    assert passes_liquidity_floor(okx_tight) is True   # 20 < 30
    cap_wide = _inst("EXOTIC", venue="capital", asset_class="forex",
                     quote_ccy="USD", vol=0.0, spread_bps=50.0, depth=0.0)
    cap_tight = _inst("EURUSD", venue="capital", asset_class="forex",
                      quote_ccy="USD", vol=0.0, spread_bps=20.0, depth=0.0)
    assert passes_liquidity_floor(cap_wide) is False  # 50 > 40
    assert passes_liquidity_floor(cap_tight) is True   # 20 < 40


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


def test_rank_watches_junk_floor_now_trade_gate_not_watch_gate() -> None:
    """WATCH/TRADE decouple (Jin 2026-06-24): the floor moved OFF the active/watch
    gate and ONTO the curator TRADE gate. A 175bp / sub-$vol name now REACHES the
    active set (WATCHED — breadth unlock, flow_not_block), but the floor predicate
    still marks it trade-ineligible (the curator forces ``trade_eligible=False``,
    so its order-open is deferred at ``_run_entries`` — slippage protection kept).
    """
    btc = _inst("BTC-USDT", vol=8e8, spread_bps=1.0, depth=400_000.0)
    eth = _inst("ETH-USDT", vol=6e8, spread_bps=1.5, depth=300_000.0)
    bnt = _inst("BNT-USDT", vol=8e8, spread_bps=175.0, depth=400_000.0)  # wide spread
    gear = _inst("GEAR-USDT", vol=50_000.0, spread_bps=5.0)  # micro vol
    out = rank_active_universe([btc, eth, bnt, gear], top_n=10)
    syms = {i.symbol for i in out}
    # All four are WATCHED now (the floor no longer pre-cuts breadth).
    assert syms == {"BTC-USDT", "ETH-USDT", "BNT-USDT", "GEAR-USDT"}
    # But the sub-floor names are still NOT trade-eligible (curator trade-gate).
    assert passes_liquidity_floor(bnt) is False
    assert passes_liquidity_floor(gear) is False
    assert passes_liquidity_floor(btc) is True and passes_liquidity_floor(eth) is True


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
    assert alpaca.min_price == 1.0 and alpaca.max_spread_bps == 100.0
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
