"""AltDataCollector ABC — pure EVIDENCE source contract.

DEMO/PAPER only. A collector fetches read-only alt-data and returns a plain
dict of parsed values. On a missing key or any network/parse error it returns
``{}`` and **never raises** — that empty result contributes no evidence and the
price-only regime stands (graceful fallback, NOT a defensive throttle).
"""

from __future__ import annotations

import abc
from typing import Any

import httpx


class AltDataCollector(abc.ABC):
    """Abstract base for an alt-data evidence collector.

    Subclasses parse one read-only source into a flat dict. They MUST swallow
    network/parse errors and missing-key conditions, returning ``{}`` instead
    of raising. Returning ``{}`` is a correct no-evidence fallback, not a halt.
    """

    #: Stable cache/source identifier (e.g. ``"okx_funding"``).
    name: str = ""
    #: Cache freshness window in seconds.
    ttl_sec: int = 0
    #: Asset-class prefixes this source informs (e.g. ``("crypto",)``).
    asset_classes: tuple[str, ...] = ()

    @abc.abstractmethod
    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        """Fetch and parse the source.

        Returns the parsed payload, or ``{}`` on a missing key / network /
        parse error. Never raises. ``client`` may be injected for testing.
        """
        raise NotImplementedError
