"""MyFxBook collector (EVIDENCE only) — activation-ready, creds-gated.

DEMO/PAPER. MyFxBook (retail FX community sentiment / outlook) needs
``MYFXBOOK_EMAIL`` + ``MYFXBOOK_PASSWORD``. Both are ABSENT in this environment
(``.env`` has them empty), so ``fetch`` returns ``{}`` immediately with NO
network call — a no-evidence fallback, never a throttle (flow_not_block).

When Jin adds creds, the collector activates with no further code change: it does
the login → session-token → get-community-outlook flow and emits a per-pair
retail long/short % map. This is the contrarian FX POSITIONING axis the FX
branch lacks today (FX currently has only the FRED macro scorer). The fuser
scores extreme crowd positioning as a contrarian tilt.

Currency (#69): community-outlook is a live snapshot; the collector stamps the
fetch time as ``asof`` per row so a cached read is age-labelable downstream.

degrade-never-crash: a failed login / outlook call returns ``{}`` (creds present
or not); a per-pair parse error skips only that pair.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any, Final

import httpx

from polaris.core.universe._helpers import REST_TIMEOUT_SEC

logger = logging.getLogger(__name__)

MYFXBOOK_BASE: Final[str] = "https://www.myfxbook.com"
_LOGIN_PATH: Final[str] = "/api/login.json"
_OUTLOOK_PATH: Final[str] = "/api/get-community-outlook.json"


def _as_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class MyfxbookCollector:
    """MyFxBook FX community outlook (forex). Creds-gated graceful skip."""

    name = "myfxbook"
    ttl_sec = 1800
    asset_classes = ("forex",)

    def __init__(
        self,
        *,
        email: str | None = None,
        password: str | None = None,
        base_url: str = MYFXBOOK_BASE,
    ) -> None:
        # Empty-string env creds count as absent.
        self._email = (email if email is not None else os.environ.get("MYFXBOOK_EMAIL", "")) or ""
        self._password = (
            password if password is not None else os.environ.get("MYFXBOOK_PASSWORD", "")
        ) or ""
        self._base_url = base_url

    async def fetch(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any] | None:
        if not (self._email and self._password):
            logger.debug("[altdata] myfxbook: no creds (graceful skip)")
            return {}
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        try:
            session = await self._login(cli)
            if not session:
                return {}
            return await self._community_outlook(cli, session)
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            logger.info("[altdata] myfxbook fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()

    async def _login(self, cli: httpx.AsyncClient) -> str | None:
        """email/password → session token (``None`` on failure)."""
        resp = await cli.get(
            _LOGIN_PATH, params={"email": self._email, "password": self._password}
        )
        if resp.status_code != 200:
            return None
        body = resp.json()
        if not isinstance(body, dict) or body.get("error", True):
            return None
        session = body.get("session")
        return str(session) if session else None

    async def _community_outlook(
        self, cli: httpx.AsyncClient, session: str
    ) -> dict[str, Any]:
        """Per-pair retail long/short % map + per-row fetch-time asof."""
        resp = await cli.get(_OUTLOOK_PATH, params={"session": session})
        if resp.status_code != 200:
            return {}
        body = resp.json()
        if not isinstance(body, dict) or body.get("error", True):
            return {}
        symbols = body.get("symbols")
        if not isinstance(symbols, list):
            return {}
        asof = _dt.datetime.now(tz=_dt.UTC).replace(microsecond=0).isoformat()
        out: dict[str, dict[str, float | str | None]] = {}
        for sym in symbols:
            if not isinstance(sym, dict):
                continue
            name = sym.get("name")
            long_pct = _as_float(sym.get("longPercentage"))
            short_pct = _as_float(sym.get("shortPercentage"))
            if not isinstance(name, str) or not name:
                continue
            if long_pct is None and short_pct is None:
                continue
            row: dict[str, float | str | None] = {"asof": asof}
            if long_pct is not None:
                row["long_pct"] = long_pct
            if short_pct is not None:
                row["short_pct"] = short_pct
            out[name] = row
        return out
