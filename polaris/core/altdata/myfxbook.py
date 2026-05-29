"""MyFxBook collector — graceful-skip stub (EVIDENCE only).

DEMO/PAPER. MyFxBook (retail FX community sentiment / outlook) needs
``MYFXBOOK_EMAIL`` + ``MYFXBOOK_PASSWORD``. Both are ABSENT in this environment
(``.env`` has them empty), so ``fetch`` returns ``{}`` immediately with NO
network call. When creds are added, wire the community-outlook parse here.
Returning ``{}`` is a no-evidence fallback, never a throttle.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MyfxbookCollector:
    """MyFxBook FX sentiment (forex). Skips gracefully without credentials."""

    name = "myfxbook"
    ttl_sec = 1800
    asset_classes = ("forex",)

    def __init__(self, *, email: str | None = None, password: str | None = None) -> None:
        # Empty-string env creds count as absent.
        self._email = (email if email is not None else os.environ.get("MYFXBOOK_EMAIL", "")) or ""
        self._password = (
            password if password is not None else os.environ.get("MYFXBOOK_PASSWORD", "")
        ) or ""

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        if not (self._email and self._password):
            logger.debug("[altdata] myfxbook: no creds (graceful skip)")
            return {}
        # Creds present → real parse goes here when MyFxBook is activated.
        return {}
