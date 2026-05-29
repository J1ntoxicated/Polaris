"""Binance USDⓈ-M Futures HMAC-SHA256 request signing.

Spec source: Binance Futures docs §SIGNED (TRADE & USER_DATA) Endpoint security.

Binance signs the **query string** (for GET/DELETE) or the **request body**
(for POST, form-encoded) appended with a ``timestamp`` (ms) and optional
``recvWindow`` parameter:

    signature = hex( HMAC_SHA256( secret, query_string ) )

- ``signature`` is appended as the final query parameter (lower-case hex).
- The API key is sent in the ``X-MBX-APIKEY`` header.
- Unlike OKX, there is **no** method / path / ISO-timestamp in the signed
  payload — only the urlencoded params (which include ``timestamp``).

This module is testnet-only in practice; the caller wires the testnet base
URL. There is no demo flag in the signature itself.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Final
from urllib.parse import urlencode

__all__ = [
    "BINANCE_APIKEY_HEADER",
    "binance_signature",
    "build_signed_query",
    "now_ms",
]

BINANCE_APIKEY_HEADER: Final[str] = "X-MBX-APIKEY"


def now_ms() -> int:
    """Current UNIX epoch in milliseconds (Binance ``timestamp`` param)."""
    return int(time.time() * 1000)


def binance_signature(*, secret: str, query_string: str) -> str:
    """Compute the lower-case hex ``HMAC_SHA256(secret, query_string)``."""
    if not secret:
        raise ValueError("secret must be non-empty")
    return hmac.new(
        secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_signed_query(
    *,
    secret: str,
    params: dict[str, str | int | float],
    recv_window: int = 5_000,
    timestamp_ms: int | None = None,
) -> str:
    """Return the full urlencoded query string with ``timestamp`` + ``signature``.

    ``timestamp`` and ``recvWindow`` are appended (in that order, before the
    signature) so the signed payload is deterministic given ``timestamp_ms``.
    Binance rejects signatures with > ``recvWindow`` clock skew, so callers
    should rely on a synchronized clock (or pass ``timestamp_ms`` in tests).
    """
    if not secret:
        raise ValueError("secret must be non-empty")
    ts = now_ms() if timestamp_ms is None else timestamp_ms
    signed_params: dict[str, str | int | float] = dict(params)
    signed_params["timestamp"] = ts
    signed_params["recvWindow"] = recv_window
    query_string = urlencode(signed_params)
    sig = binance_signature(secret=secret, query_string=query_string)
    return f"{query_string}&signature={sig}"
