"""P0a grid conformance — BG3 parallel manual archetype track (2026-07-10).

Thesis-first base classes for the 3 structural gaps (short/carry/event) MUST
be P0a-searchable (``enumerate_grid`` / ``make_variant`` from a
``PARAM_BOUNDS`` entry), exactly like every other strategy the evolve engine
already searches — no blind grid sweep, no manual eyeball parameter picking.
This mirrors ``test_p0a_variants.py``'s test plan for the 3 NEW ids:

  (a) behavior-0: default instance == frozen module constants.
  (b) a variant with an overridden threshold changes emission on a crafted
      view (the entry-set proof at the MarketView layer; the trade-SET-level
      proof through the real engine lives in ``test_p0a_no_inert_knob.py``).
  (c) grid respects the <=3-point-per-param / total-cap contract.
  (d) every PARAM_BOUNDS knob is a real class attribute (make_variant seam).

NOT registered in ``STRATEGY_REGISTRY`` (see each module's docstring) --
these tests import the strategy classes directly, matching the existing
precedent for other un-registered-but-preserved modules (``spot_donchian`` /
``volume_burst`` in ``test_p0a_variants.py``).
"""

from __future__ import annotations

import pytest

from polaris.core.evolve.param_bounds import PARAM_BOUNDS, varyable_params
from polaris.core.evolve.variants import GRID_TOTAL_CAP, enumerate_grid, make_variant
from polaris.strategies.base import AltDataView, BarView, MarketView
from polaris.strategies.capital_macro_riskoff_catalyst import (
    CapitalMacroRiskoffCatalystStrategy,
)
from polaris.strategies.cfd_fx_range_fade_short import CFDFXRangeFadeShortStrategy
from polaris.strategies.okx_funding_carry_persist import OKXFundingCarryPersistStrategy

_NEW_IDS = (
    "cfd_fx_range_fade_short",
    "okx_funding_carry_persist",
    "capital_macro_riskoff_catalyst",
)


def _bars(n: int, close: float = 1.1000, last_close: float | None = None) -> list[BarView]:
    out = [
        BarView(
            ts=1_700_000_000 + i * 3600,
            open=close, high=close * 1.001, low=close * 0.999, close=close,
            volume=1000.0,
        )
        for i in range(n)
    ]
    if last_close is not None:
        b = out[-1]
        out[-1] = BarView(
            ts=b.ts, open=last_close, high=last_close * 1.001,
            low=last_close * 0.999, close=last_close, volume=b.volume,
        )
    return out


# ---------------------------------------------------------------------------
# (a) behavior-0 — default instance == frozen module constants
# ---------------------------------------------------------------------------


def test_behavior0_short_default_unchanged() -> None:
    mv = MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=_bars(30, last_close=1.15), last_price=1.15, spread_bps=2.0,
        adx_14=15.0, bb_upper=1.14, bb_middle=1.10, bb_lower=1.06,
    )
    base_sig = CFDFXRangeFadeShortStrategy().generate_raw_signal(mv)
    var_sig = make_variant(CFDFXRangeFadeShortStrategy, {}).generate_raw_signal(mv)
    assert base_sig is not None
    assert var_sig is not None
    assert base_sig.side == var_sig.side == "short"
    assert base_sig.strength == var_sig.strength


def test_behavior0_carry_default_unchanged() -> None:
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=_bars(25, close=100.0), last_price=100.0, spread_bps=2.0,
        altdata=AltDataView(funding_rate_symbol=-0.0006),
    )
    base_sig = OKXFundingCarryPersistStrategy().generate_raw_signal(mv)
    var_sig = make_variant(OKXFundingCarryPersistStrategy, {}).generate_raw_signal(mv)
    assert base_sig is not None
    assert var_sig is not None
    assert base_sig.side == var_sig.side == "long"
    assert base_sig.strength == var_sig.strength


def test_behavior0_event_default_unchanged() -> None:
    mv = MarketView(
        symbol="GOLD", venue="capital", timeframe="1H",
        bars=_bars(6, close=2000.0), last_price=2000.0, spread_bps=2.0,
        altdata=AltDataView(vix=30.0, hy_spread=550.0),
    )
    base_sig = CapitalMacroRiskoffCatalystStrategy().generate_raw_signal(mv)
    var_sig = make_variant(CapitalMacroRiskoffCatalystStrategy, {}).generate_raw_signal(mv)
    assert base_sig is not None
    assert var_sig is not None
    assert base_sig.side == var_sig.side == "long"
    assert base_sig.strength == var_sig.strength


# ---------------------------------------------------------------------------
# (b) overridden threshold changes emission (MarketView-layer entry-set proof)
# ---------------------------------------------------------------------------


def test_short_variant_threshold_changes_entry_set() -> None:
    # adx=22 is BELOW the default 25.0 range ceiling (default -> fires) but AT/
    # ABOVE a stricter variant threshold of 20.0 (variant -> blocked).
    mv = MarketView(
        symbol="EURUSD", venue="capital", timeframe="1H",
        bars=_bars(30, last_close=1.15), last_price=1.15, spread_bps=2.0,
        adx_14=22.0, bb_upper=1.14, bb_middle=1.10, bb_lower=1.06,
    )
    assert CFDFXRangeFadeShortStrategy().generate_raw_signal(mv) is not None
    strict = make_variant(CFDFXRangeFadeShortStrategy, {"adx_range_max": 20.0})
    assert strict.generate_raw_signal(mv) is None


def test_carry_variant_threshold_changes_entry_set() -> None:
    # funding=-0.0002 is ABOVE (shallower than) the default -0.0003 threshold
    # (default -> no emit) but BELOW a looser variant threshold of -0.0001
    # (variant -> emits).
    mv = MarketView(
        symbol="BTC-USDT", venue="okx", timeframe="1H",
        bars=_bars(25, close=100.0), last_price=100.0, spread_bps=2.0,
        altdata=AltDataView(funding_rate_symbol=-0.0002),
    )
    assert OKXFundingCarryPersistStrategy().generate_raw_signal(mv) is None
    loose = make_variant(OKXFundingCarryPersistStrategy, {"funding_threshold": -0.0001})
    assert loose.generate_raw_signal(mv) is not None


def test_event_vix_variant_threshold_changes_entry_set() -> None:
    # vix=25 is BELOW the default 26.0 threshold (default -> no emit) but
    # ABOVE a looser variant threshold of 24.0 (variant -> emits), hy fixed
    # above the default hy threshold throughout.
    mv = MarketView(
        symbol="GOLD", venue="capital", timeframe="1H",
        bars=_bars(6, close=2000.0), last_price=2000.0, spread_bps=2.0,
        altdata=AltDataView(vix=25.0, hy_spread=550.0),
    )
    assert CapitalMacroRiskoffCatalystStrategy().generate_raw_signal(mv) is None
    loose = make_variant(CapitalMacroRiskoffCatalystStrategy, {"vix_threshold": 24.0})
    assert loose.generate_raw_signal(mv) is not None


def test_event_hy_variant_threshold_changes_entry_set() -> None:
    # hy=480 is BELOW the default 500.0 threshold (default -> no emit) but
    # ABOVE a looser variant threshold of 450.0 (variant -> emits), vix fixed
    # above the default vix threshold throughout.
    mv = MarketView(
        symbol="GOLD", venue="capital", timeframe="1H",
        bars=_bars(6, close=2000.0), last_price=2000.0, spread_bps=2.0,
        altdata=AltDataView(vix=30.0, hy_spread=480.0),
    )
    assert CapitalMacroRiskoffCatalystStrategy().generate_raw_signal(mv) is None
    loose = make_variant(CapitalMacroRiskoffCatalystStrategy, {"hy_spread_threshold": 450.0})
    assert loose.generate_raw_signal(mv) is not None


# ---------------------------------------------------------------------------
# (c) grid respects the <=3-point-per-param / total-cap contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strat_id", _NEW_IDS)
def test_new_ids_grid_per_param_at_most_3_points(strat_id: str) -> None:
    for name, grid in PARAM_BOUNDS[strat_id].items():
        assert 1 <= len(grid) <= 3, f"{strat_id}.{name} has >3 or 0 grid points"


def test_capital_macro_riskoff_grid_full_when_under_cap() -> None:
    variants, truncated = enumerate_grid(
        CapitalMacroRiskoffCatalystStrategy, cap=GRID_TOTAL_CAP
    )
    # vix_threshold(3) x hy_spread_threshold(3) = 9 <= cap -> full, no truncation.
    assert truncated is False
    assert len(variants) == 9
    ids = [v.variant_id for v in variants]
    assert len(set(ids)) == len(ids)


def test_new_ids_default_variant_is_behavior0() -> None:
    mv = MarketView(
        symbol="GOLD", venue="capital", timeframe="1H",
        bars=_bars(6, close=2000.0), last_price=2000.0, spread_bps=2.0,
        altdata=AltDataView(vix=30.0, hy_spread=550.0),
    )
    base = CapitalMacroRiskoffCatalystStrategy().generate_raw_signal(mv)
    var = make_variant(CapitalMacroRiskoffCatalystStrategy, {}).generate_raw_signal(mv)
    assert base is not None and var is not None
    assert (base.strength, base.side, base.thesis_tag) == (var.strength, var.side, var.thesis_tag)


# ---------------------------------------------------------------------------
# (d) every PARAM_BOUNDS knob is a real class attribute (make_variant seam)
# ---------------------------------------------------------------------------


def test_new_ids_varyable_params_match_class_attrs() -> None:
    name_to_cls = {
        "cfd_fx_range_fade_short": CFDFXRangeFadeShortStrategy,
        "okx_funding_carry_persist": OKXFundingCarryPersistStrategy,
        "capital_macro_riskoff_catalyst": CapitalMacroRiskoffCatalystStrategy,
    }
    for strat_id, cls in name_to_cls.items():
        for name in varyable_params(strat_id):
            assert hasattr(cls, name), f"{cls.__name__} missing attr {name}"


def test_new_ids_make_variant_rejects_unknown_param() -> None:
    with pytest.raises(ValueError):
        make_variant(CFDFXRangeFadeShortStrategy, {"not_a_real_param": 1.0})
    with pytest.raises(ValueError):
        make_variant(OKXFundingCarryPersistStrategy, {"ttl_bars": 1.0})


def test_new_ids_present_in_param_bounds() -> None:
    for strat_id in _NEW_IDS:
        assert strat_id in PARAM_BOUNDS
