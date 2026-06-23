"""Alpaca trading-halt reject classification — validation_rejected fix.

Live incident (2026-06-22): a market order on a HALTED symbol (EHGO) was
rejected with a 422 "market order rejected due to trading halt on symbol ...
please place a limit order instead". The classifier mapped it to
``validation_rejected`` (intentionally OUTSIDE the external set) → the reject
handler recorded a FAULT_REJECT every tick (ERROR-level log spam).

A trading halt is an EXTERNAL venue condition (the symbol is halted right now),
NOT a strategy fault. ``trading_halt`` is now a first-class external token in
C_alpaca_equity.external_reject_codes so it is released, no strategy fault.

DEMO/PAPER only.
"""

from __future__ import annotations

from polaris.core.streams.config import STREAMS
from polaris.venues.alpaca.reject_codes import (
    REJECT_INSUFFICIENT_BUYING_POWER,
    REJECT_TRADING_HALT,
    REJECT_VALIDATION,
    classify_reject_code,
)


def test_trading_halt_message_classifies_as_trading_halt() -> None:
    code = classify_reject_code(
        numeric_code=42210000,
        status_code=422,
        message=(
            'market order rejected due to trading halt on symbol: "EHGO", '
            "please place a limit order instead"
        ),
    )
    assert code == REJECT_TRADING_HALT


def test_trading_halt_is_external_not_validation() -> None:
    assert REJECT_TRADING_HALT != REJECT_VALIDATION


def test_trading_halt_in_external_reject_codes() -> None:
    cfg = STREAMS["C_alpaca_equity"]
    assert REJECT_TRADING_HALT in cfg.external_reject_codes


def test_generic_422_still_faults_as_validation() -> None:
    # A non-halt 422 must STILL be a fault (no over-broad whitelist).
    code = classify_reject_code(
        numeric_code=42210000,
        status_code=422,
        message="qty must be a positive integer",
    )
    assert code == REJECT_VALIDATION


def test_insufficient_buying_power_unchanged() -> None:
    code = classify_reject_code(
        numeric_code=40310000,
        status_code=403,
        message="insufficient buying power",
    )
    assert code == REJECT_INSUFFICIENT_BUYING_POWER
