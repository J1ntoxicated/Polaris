"""Alpaca order-reject code normalization (H2).

Alpaca's order-reject body carries a NUMERIC error code (e.g. 40310000) under an
HTTP status (403 / 422). The circuit-breaker classifier (``_is_external_reject``
in ``_production_pipeline``) compares the reject code against the StreamConfig
SSOT ``C_alpaca_equity.external_reject_codes`` — a set of *semantic tokens*
("insufficient_buying_power", "pdt_block", "market_closed", "forbidden").

Emitting the raw numeric code would never match → a genuine external reject
(market closed / PDT / buying power) would be misclassified as a STRATEGY FAULT
and trip an unjust HARD_HALT (lesson 1a315a3). ``classify_reject_code`` maps the
(numeric code, HTTP status, message) into that semantic vocabulary; the raw
numeric stays in the adapter response ``raw`` for audit. A reject we cannot map
to an external token becomes ``REJECT_VALIDATION`` — intentionally NOT in the
external set — so a true anomalous reject still records a fault.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = [
    "REJECT_FORBIDDEN",
    "REJECT_INSUFFICIENT_BUYING_POWER",
    "REJECT_MARKET_CLOSED",
    "REJECT_NOT_FRACTIONABLE",
    "REJECT_PDT_BLOCK",
    "REJECT_VALIDATION",
    "classify_reject_code",
]

# Alpaca numeric codes (docs: alpaca.markets/learn/.../trading-api-errors).
_ALPACA_CODE_BUYING_POWER: Final[str] = "40310000"  # 403: BP / restriction / not-authorized
_ALPACA_CODE_PDT: Final[str] = "40310100"  # 403: pattern-day-trading protection

# Semantic tokens — MUST stay aligned with C_alpaca_equity.external_reject_codes
# (the external ones) so _is_external_reject matches. ``REJECT_VALIDATION`` is
# intentionally OUTSIDE that set (anomalous → fault).
REJECT_INSUFFICIENT_BUYING_POWER: Final[str] = "insufficient_buying_power"
REJECT_PDT_BLOCK: Final[str] = "pdt_block"
REJECT_MARKET_CLOSED: Final[str] = "market_closed"
REJECT_FORBIDDEN: Final[str] = "forbidden"
# A whole-share-only asset rejects a $-notional order. Alpaca reuses the generic
# 40310000 / 403 (same as insufficient buying power) but the message says "not
# fractionable" — so it MUST be discriminated by message, not numeric code, or it
# masquerades as a buying-power reject and pollutes that signal. It is EXTERNAL
# (a venue constraint, not a strategy fault) AND recovered by the whole-share
# retry in ``_alpaca_open`` (flow_not_block), so it stays in the external set.
REJECT_NOT_FRACTIONABLE: Final[str] = "not_fractionable"
# A market order on a HALTED symbol is rejected with a 422 "trading halt ...
# please place a limit order instead". This is an EXTERNAL venue condition (the
# symbol is halted right now), NOT a strategy fault — recording a FAULT_REJECT
# for it pollutes the fault signal (live: EHGO halt logged ERROR every tick).
# Discriminated by message because Alpaca reuses the generic 422.
REJECT_TRADING_HALT: Final[str] = "trading_halt"
REJECT_VALIDATION: Final[str] = "validation_rejected"


def classify_reject_code(
    *, numeric_code: Any, status_code: int, message: str
) -> str:
    """Map an Alpaca reject (numeric code + HTTP status + msg) → semantic token.

    External (non-fault) tokens mirror ``C_alpaca_equity.external_reject_codes``:
    insufficient buying power / PDT / market closed / forbidden. A 422 (or any
    unrecognized 4xx) is a validation/client reject → ``REJECT_VALIDATION``
    (NOT external → still faults). Keyed on the numeric code first (it carries
    the precise semantics), then the message, then the HTTP status.
    """
    code_str = str(numeric_code) if numeric_code is not None else ""
    msg_l = (message or "").lower()
    # "not fractionable" shares 40310000/403 with insufficient-buying-power, so
    # the message MUST win before the numeric branch — else it misclassifies as
    # a buying-power reject. Recovered by the whole-share retry; external.
    if "not fractionable" in msg_l:
        return REJECT_NOT_FRACTIONABLE
    # Halted symbol rejecting a market order — external venue condition, not a
    # fault. Message wins (Alpaca reuses the generic 422 validation code).
    if "trading halt" in msg_l or "halt on symbol" in msg_l:
        return REJECT_TRADING_HALT
    # Numeric code is the most precise signal.
    if code_str == _ALPACA_CODE_PDT:
        return REJECT_PDT_BLOCK
    if code_str == _ALPACA_CODE_BUYING_POWER:
        return REJECT_INSUFFICIENT_BUYING_POWER
    # Message keywords (market-closed / PDT / buying power surface in the text).
    if "pattern day" in msg_l or "pdt" in msg_l:
        return REJECT_PDT_BLOCK
    if "market is closed" in msg_l or "market closed" in msg_l:
        return REJECT_MARKET_CLOSED
    if "buying power" in msg_l or "insufficient" in msg_l:
        return REJECT_INSUFFICIENT_BUYING_POWER
    # HTTP status fallbacks: 401/403 → auth/permission (external); everything
    # else (422 validation, unknown 4xx) → validation reject (anomalous → fault).
    if status_code in (401, 403):
        return REJECT_FORBIDDEN
    return REJECT_VALIDATION
