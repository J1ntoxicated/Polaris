"""Fill normalizer — venue payload → unified Fill dataclass."""

from __future__ import annotations

import pytest

from polaris.core.data.fill_normalizer import (
    Fill,
    FillNormalizationError,
    normalize_capital_confirm,
    normalize_okx_fill,
)


def test_okx_fill_to_unified_quote_ccy() -> None:
    # A $10-notional market buy (request sz=10 USDT, tgtCcy=quote_ccy) fills
    # ~0.00016667 BTC @ 60000. OKX reports accFillSz in BASE ccy regardless of
    # the request's quote sz, so size_usd = accFillSz * avgPx = $10.
    payload = {
        "ordId": "999",
        "clOrdId": "polarisvb",
        "instId": "BTC-USDT",
        "tdMode": "cash",
        "side": "buy",
        "ordType": "market",
        "tgtCcy": "quote_ccy",
        "sz": "10",
        "accFillSz": "0.00016667",  # base ccy (BTC) — the filled quantity
        "avgPx": "60000",
        "fee": "-0.0035",
        "feeCcy": "USDT",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="volume_burst", expected_price=60_000.0)
    assert isinstance(f, Fill)
    assert f.venue == "okx"
    assert f.instrument_id == "okx:BTC-USDT"
    assert f.side == "buy"
    assert f.base_qty == pytest.approx(0.00016667)
    assert f.size_usd == pytest.approx(10.0, rel=1e-3)  # base * avgPx
    assert f.fill_price == 60_000.0
    # fee_usd is the REAL OKX taker fee (10 bps of the $10 notional = $0.01),
    # NOT the raw demo 'fee' field (-0.0035 here). The system measures REAL-venue
    # viability (replay gate / dashboard real_fee_total all use real_fee_usd); the
    # 70bps demo charge is sandbox overhead and must not train the NIG posterior.
    assert f.fee_usd == pytest.approx(f.quote_qty * 0.001, rel=1e-9)  # 10 bps real taker
    assert f.fee_usd == pytest.approx(0.01, rel=1e-3)  # ≈ 10 bps of ~$10
    assert f.slippage_bps == pytest.approx(0.0)
    assert f.ts_ms == 1_762_476_225_678
    assert f.order_id == "999"


def test_okx_fill_base_ccy_units_recovered() -> None:
    """When tgtCcy=base_ccy, accFillSz is base; quote_qty = base × avgPx.

    The base-ccy taker fee (feeCcy=BTC) is deducted from the tracked holding so
    base_qty mirrors the wallet credit (0.001 − 0.000001); cost basis (quote_qty
    / size_usd) is keyed off the GROSS filled base × avgPx.
    """
    payload = {
        "ordId": "1",
        "instId": "BTC-USDT",
        "side": "buy",
        "tgtCcy": "base_ccy",
        "accFillSz": "0.001",
        "avgPx": "60000",
        "fee": "-0.000001",
        "feeCcy": "BTC",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="tsmom")
    assert f.base_qty == pytest.approx(0.000999)  # net of base fee
    assert f.quote_qty == pytest.approx(60.0)  # gross fill × avgPx (cost basis)
    assert f.size_usd == pytest.approx(60.0)


def test_okx_fill_quote_ccy_accfillsz_is_base() -> None:
    """OKX accFillSz is ALWAYS base ccy, even for a tgtCcy=quote_ccy market
    buy (the *request* sz is quote, but the *fill* qty is base). A real
    0.618-ETH fill (~$1240 @ 2007) must record size_usd~1240 and base_qty
    0.618 — NOT $0.62 (the pre-2026-05-29 flip bug that mis-treated base
    accFillSz as quote, under-recording the position and orphaning the rest)."""
    payload = {
        "ordId": "7",
        "instId": "ETH-USDT",
        "side": "buy",
        "tgtCcy": "quote_ccy",
        "accFillSz": "0.618",  # base ccy (ETH) — the actual filled quantity
        "avgPx": "2007.0",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="tsmom")
    assert f.base_qty == pytest.approx(0.618)
    assert f.size_usd == pytest.approx(0.618 * 2007.0, rel=1e-6)
    assert f.fill_price == pytest.approx(2007.0)


def test_okx_buy_base_fee_deducted_from_tracked_qty() -> None:
    """OKX SPOT market BUY charges the taker fee in the BASE ccy: the wallet
    receives ``accFillSz - |fee|``, not the gross ``accFillSz``. The tracked
    ``base_qty`` MUST be the net wallet credit so the close sells exactly what
    the wallet holds — else every position over-counts by the fee, the close
    clamps to ``available < base_qty`` (a fee-sized dust remainder), and the
    whole position orphans to status='reconciled' with PnL discarded (the live
    235-reconciled tracking-failure class, 0.7000% demo-fee gap).

    Cost basis (``size_usd`` / ``quote_qty``) stays GROSS — the full USDT spent
    is ``accFillSz × avgPx`` regardless of the base fee. Only the holding shrinks.
    """
    # 1000 XRP @ 1.0, 0.7% demo taker fee charged in XRP (base ccy).
    payload = {
        "ordId": "42",
        "instId": "XRP-USDT",
        "side": "buy",
        "tgtCcy": "quote_ccy",
        "accFillSz": "1000.0",
        "avgPx": "1.0",
        "fee": "-7.0",  # 0.7% of 1000 XRP, deducted from the base wallet credit
        "feeCcy": "XRP",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="flow_pressure")
    # Tracked holding = net wallet credit (gross − base fee).
    assert f.base_qty == pytest.approx(993.0)
    # Cost basis unchanged — the full notional was still spent.
    assert f.size_usd == pytest.approx(1000.0)
    assert f.quote_qty == pytest.approx(1000.0)


def test_okx_sell_quote_fee_leaves_base_qty_gross() -> None:
    """A SELL charges the fee in the QUOTE ccy (USDT) — the base sold is the
    full ``accFillSz``, so ``base_qty`` is NOT reduced (the deduction lands on
    the USDT proceeds, captured separately as fee_usd)."""
    payload = {
        "ordId": "43",
        "instId": "XRP-USDT",
        "side": "sell",
        "accFillSz": "993.0",
        "avgPx": "1.0",
        "fee": "-0.6951",  # quote-ccy fee
        "feeCcy": "USDT",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="flow_pressure")
    assert f.base_qty == pytest.approx(993.0)  # base sold unaffected by quote fee


def test_okx_maker_fill_stamps_real_maker_fee() -> None:
    """A post-only (maker) fill carries ``execType=='M'`` in the OKX order
    payload — fee_usd MUST be the REAL maker schedule (8 bps), NOT the taker
    default (10 bps). The maker edge (2 bps/leg) is otherwise invisible: a maker
    fill stamped as taker over-charges every NIG/cell observation and hides the
    weekend-maker thesis. The fee is stamped per-fill at the truth boundary so
    every fills.fee_usd consumer (posterior / cell / dashboard net) reflects the
    real maker tier without re-deriving it."""
    payload = {
        "ordId": "100",
        "instId": "BTC-USDT",
        "side": "buy",
        "tgtCcy": "quote_ccy",
        "accFillSz": "0.001",
        "avgPx": "60000",
        "execType": "M",  # maker (post-only rested + filled passively)
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="weekend_thin_book_flush_maker")
    # 8 bps real maker of the $60 notional = $0.048 (NOT 10 bps taker = $0.06).
    assert f.fee_usd == pytest.approx(f.quote_qty * 0.0008, rel=1e-9)
    assert f.fee_usd == pytest.approx(60.0 * 0.0008, rel=1e-6)


def test_okx_taker_fill_stamps_real_taker_fee() -> None:
    """An explicit ``execType=='T'`` (taker / market cross) stamps the REAL
    taker schedule (10 bps) — byte-identical to the no-execType default so the
    maker wiring never changes a taker fill."""
    payload = {
        "ordId": "101",
        "instId": "BTC-USDT",
        "side": "buy",
        "tgtCcy": "quote_ccy",
        "accFillSz": "0.001",
        "avgPx": "60000",
        "execType": "T",  # taker (market / marketable-limit cross)
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="tsmom")
    assert f.fee_usd == pytest.approx(f.quote_qty * 0.001, rel=1e-9)  # 10 bps taker
    assert f.fee_usd == pytest.approx(60.0 * 0.001, rel=1e-6)


def test_okx_missing_exectype_defaults_taker() -> None:
    """No ``execType`` in the payload (legacy / unknown) falls back to TAKER —
    degrade-safe: the maker tier is never silently assumed, so a fill whose
    aggressor side is unknown is conservatively the more expensive taker (no
    edge over-statement). Byte-identical to the pre-wiring behaviour."""
    payload = {
        "ordId": "102",
        "instId": "BTC-USDT",
        "side": "buy",
        "tgtCcy": "quote_ccy",
        "accFillSz": "0.001",
        "avgPx": "60000",
        "state": "filled",  # no execType
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="tsmom")
    assert f.fee_usd == pytest.approx(f.quote_qty * 0.001, rel=1e-9)  # taker default


def test_okx_fill_rejects_non_filled() -> None:
    payload = {"state": "live", "instId": "BTC-USDT", "side": "buy"}
    with pytest.raises(FillNormalizationError):
        normalize_okx_fill(payload, strategy_id="volume_burst")


def test_capital_fill_to_unified() -> None:
    payload = {
        "dealReference": "REF1",
        "dealId": "DEAL1",
        "epic": "EURUSD",
        "direction": "BUY",
        "level": 1.085,
        "size": 1.0,
        "status": "OPEN",
        "date": "2026-05-07T01:23:45.000",
    }
    f = normalize_capital_confirm(
        payload, strategy_id="fx_breakout_basket", pip_value_usd=10.0,
        expected_price=1.085,
    )
    assert f.venue == "capital"
    assert f.instrument_id == "capital:EURUSD"
    assert f.side == "buy"
    assert f.size_usd == 10.0  # 1 lot * pip 10
    assert f.fill_price == 1.085
    assert f.slippage_bps == pytest.approx(0.0)
    assert f.order_id == "DEAL1"


def test_capital_fill_slippage_bps() -> None:
    payload = {
        "dealReference": "R",
        "dealId": "D",
        "epic": "GOLD",
        "direction": "SELL",
        "level": 2050.0,
        "size": 0.5,
        "status": "OPEN",
        "date": "2026-05-07T00:00:00",
    }
    f = normalize_capital_confirm(
        payload,
        strategy_id="xau_indices_trend",
        pip_value_usd=1.0,
        expected_price=2049.0,
    )
    # |2050 - 2049| / 2049 * 10000 ≈ 4.88
    assert f.slippage_bps == pytest.approx(4.88, abs=0.05)
    assert f.side == "sell"


def test_capital_rejects_non_open() -> None:
    payload = {
        "dealReference": "R", "epic": "EURUSD", "direction": "BUY",
        "level": 1.0, "size": 1.0, "status": "REJECTED",
    }
    with pytest.raises(FillNormalizationError):
        normalize_capital_confirm(payload, strategy_id="fx", pip_value_usd=10.0)


def test_capital_fill_includes_leverage_in_notional() -> None:
    """Codex Day 5 P1 — size_usd must include leverage for CFD gross notional."""
    payload = {
        "dealReference": "R",
        "dealId": "D",
        "epic": "EURUSD",
        "direction": "BUY",
        "level": 1.10,
        "size": 1.0,
        "status": "OPEN",
        "date": "2026-05-07T00:00:00",
    }
    f30 = normalize_capital_confirm(
        payload, strategy_id="fx", pip_value_usd=10.0, leverage=30.0
    )
    f1 = normalize_capital_confirm(
        payload, strategy_id="fx", pip_value_usd=10.0, leverage=1.0
    )
    assert f30.size_usd == pytest.approx(300.0)  # 1 * 10 * 30
    assert f1.size_usd == pytest.approx(10.0)
    assert f30.size_usd == 30.0 * f1.size_usd


@pytest.mark.parametrize(
    ("leverage", "exp_size_usd"),
    [
        (30.0, 300.0),  # FX
        (20.0, 200.0),  # index / commodity
        (2.0, 20.0),    # crypto-CFD
        (1.0, 10.0),    # spot-equivalent / no leverage
    ],
)
def test_capital_per_market_leverage_reaches_size_usd(
    leverage: float, exp_size_usd: float
) -> None:
    """T7 — the per-market leverage passed by the real_capital_open_fill caller
    must scale size_usd = size * pip * lev so the recorded notional matches the
    sized notional (index/commodity 20x, crypto 2x, NOT a flat 30x)."""
    payload = {
        "dealReference": "R",
        "dealId": "D",
        "epic": "EPIC",
        "direction": "BUY",
        "level": 1.10,
        "size": 1.0,
        "status": "OPEN",
        "date": "2026-05-07T00:00:00",
    }
    f = normalize_capital_confirm(
        payload, strategy_id="x", pip_value_usd=10.0, leverage=leverage
    )
    assert f.size_usd == pytest.approx(exp_size_usd)


def test_okx_fee_is_real_taker_not_demo_charge() -> None:
    """A SUSHI-USDT buy with $1429.31 notional must record the REAL 10 bps taker
    fee (≈$1.43), NOT the 70 bps OKX-demo charge ($10.005) the venue payload
    reports. The raw demo 'fee' is sandbox overhead; persisting it trained the
    NIG posterior / edge-validation on 7x cost (forensic 2026-05-31)."""
    payload = {
        "ordId": "5",
        "instId": "SUSHI-USDT",
        "side": "buy",
        "tgtCcy": "quote_ccy",
        "accFillSz": "1429.31",  # base SUSHI @ $1.00 → $1429.31 notional
        "avgPx": "1.0",
        "fee": "-10.00517",  # 70 bps demo charge — must be IGNORED
        "feeCcy": "USDT",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="volume_burst")
    assert f.quote_qty == pytest.approx(1429.31)
    assert f.fee_usd == pytest.approx(1429.31 * 0.001, rel=1e-6)  # ≈ $1.43
    assert f.fee_usd < 2.0  # decisively NOT the $10.005 demo charge


def test_okx_fee_real_independent_of_fee_ccy() -> None:
    """The stored fee is the REAL taker fee on notional regardless of the demo
    payload's feeCcy (BTC vs USDT) — the venue's raw 'fee' field is no longer
    read. 100 BTC @ $60000 = $6M notional → 10 bps = $6000."""
    payload = {
        "ordId": "1",
        "instId": "BTC-USDT",
        "side": "buy",
        "tgtCcy": "quote_ccy",
        "accFillSz": "100",
        "avgPx": "60000",
        "fee": "-0.000001",  # BTC — IGNORED now
        "feeCcy": "BTC",
        "state": "filled",
        "uTime": "1762476225678",
    }
    f = normalize_okx_fill(payload, strategy_id="volume_burst")
    # Real taker = 10 bps of the $6,000,000 notional.
    assert f.fee_usd == pytest.approx(100.0 * 60000.0 * 0.001)


def test_capital_fill_stores_real_3bps_fee() -> None:
    """Capital demo reports fee=0, so the stored fee defaults to the REAL 3 bps
    proxy (COST_BPS_CAPITAL) of the gross notional — consistent with the rest of
    the system measuring real-venue viability."""
    payload = {
        "dealReference": "R",
        "dealId": "D",
        "epic": "EURUSD",
        "direction": "BUY",
        "level": 1.10,
        "size": 1.0,
        "status": "OPEN",
        "date": "2026-05-07T00:00:00",
    }
    f = normalize_capital_confirm(
        payload, strategy_id="fx", pip_value_usd=10.0, leverage=30.0
    )
    # size_usd = 1 * 10 * 30 = 300; 3 bps → $0.09.
    assert f.size_usd == pytest.approx(300.0)
    assert f.fee_usd == pytest.approx(300.0 * 3.0 / 1e4)


def test_capital_explicit_fee_is_honored() -> None:
    """An explicit fee_usd override is still respected (backward-compat)."""
    payload = {
        "dealReference": "R", "dealId": "D", "epic": "EURUSD", "direction": "BUY",
        "level": 1.10, "size": 1.0, "status": "OPEN", "date": "2026-05-07T00:00:00",
    }
    f = normalize_capital_confirm(
        payload, strategy_id="fx", pip_value_usd=10.0, fee_usd=1.23
    )
    assert f.fee_usd == pytest.approx(1.23)


def test_alpaca_fill_zero_fee() -> None:
    """Alpaca US equity is commission-free → stored fee is 0."""
    from polaris.core.data.fill_normalizer import normalize_alpaca_fill

    payload = {
        "id": "a1",
        "symbol": "AAPL",
        "side": "buy",
        "filled_avg_price": "190.0",
        "filled_qty": "10",
        "status": "filled",
        "filled_at": "2026-05-07T00:00:00Z",
    }
    f = normalize_alpaca_fill(payload, strategy_id="x")
    assert f.venue == "alpaca"
    assert f.fee_usd == 0.0
