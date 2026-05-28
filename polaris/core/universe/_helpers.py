"""Layer 0 universe discovery — shared low-level helpers.

Tiny shared surface (``_to_float`` coercion + REST timeout) used by both the
OKX path (``discovery``) and the Capital path (``_capital``). Split out so the
two venue modules avoid a circular import. No network / no DB.
"""

from __future__ import annotations

from typing import Any

REST_TIMEOUT_SEC = 15.0


def _to_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
