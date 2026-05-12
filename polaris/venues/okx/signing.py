"""OKX HMAC-SHA256 request signing.

Spec source: OKX docs §Authentication.

OK-ACCESS-SIGN = base64( HMAC_SHA256( secret, ts + method + path + body ) )

- ``ts`` is ISO-8601 with milliseconds and 'Z' suffix, e.g.
  ``2026-05-07T01:23:45.678Z``.
- ``method`` upper-cased.
- ``path`` includes the query string for GET (``/api/v5/...?a=b``).
- ``body`` is the JSON request body for POST (or empty string for GET).

Demo header (``x-simulated-trading: 1``) is layered on top by the adapter.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Final

__all__ = [
    "OKX_DEMO_SIM_HEADER",
    "build_signed_headers",
    "iso_timestamp",
    "okx_signature",
]

OKX_DEMO_SIM_HEADER: Final[str] = "x-simulated-trading"


def iso_timestamp(now_ms: int | None = None) -> str:
    """Return UTC ISO-8601 timestamp with millisecond precision + 'Z' suffix.

    OKX rejects signatures with > 30 s clock skew, so callers should pass
    ``now_ms`` from a synchronized clock (or rely on ``time.time()``).
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    seconds, millis = divmod(now_ms, 1000)
    dt = datetime.fromtimestamp(seconds, tz=UTC)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def okx_signature(
    *,
    secret: str,
    timestamp: str,
    method: str,
    request_path: str,
    body: str = "",
) -> str:
    """Compute the ``OK-ACCESS-SIGN`` base64 signature."""
    if not secret:
        raise ValueError("secret must be non-empty")
    method_u = method.upper()
    msg = f"{timestamp}{method_u}{request_path}{body}".encode()
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def build_signed_headers(
    *,
    api_key: str,
    secret: str,
    passphrase: str,
    method: str,
    request_path: str,
    body: str = "",
    demo: bool = True,
    now_ms: int | None = None,
) -> dict[str, str]:
    """Return the full set of OKX auth headers for one request.

    For demo trading the ``x-simulated-trading: 1`` header is included; live
    callers should pass ``demo=False``.
    """
    if not api_key or not passphrase:
        raise ValueError("api_key and passphrase must be non-empty")
    ts = iso_timestamp(now_ms)
    sign = okx_signature(
        secret=secret,
        timestamp=ts,
        method=method,
        request_path=request_path,
        body=body,
    )
    headers: dict[str, str] = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }
    if demo:
        headers[OKX_DEMO_SIM_HEADER] = "1"
    return headers
