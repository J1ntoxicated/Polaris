"""Capital.com REST session manager — login + token + 9-min anti-idle ping.

Spec source:
- ``feedback_capital_demo_live_split.md`` (CST + X-SECURITY-TOKEN, 10-min idle)
- vault/30_components/layer-0-universe-discovery.md (Capital ping cadence)
- ADR-003 §Per-Venue Adapters (session.py mandate)

Session tokens (``CST`` + ``X-SECURITY-TOKEN``) expire after 10 min idle. We:
- Log in with ``POST /api/v1/session`` (X-CAP-API-KEY + identifier + password)
- Cache tokens with a refresh deadline = now + 540 s (9-min, anti-idle)
- Re-login (rate-capped to 1/s by spec) when:
  * tokens have not yet been minted, or
  * deadline elapsed, or
  * a request returns 401/expired
- Optionally run an idle-ping background task (``GET /api/v1/ping``) every 540 s

Pure async; no module-level mutable state.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "CAPITAL_BASE_DEMO",
    "CAPITAL_BASE_LIVE",
    "CAPITAL_PING_PATH",
    "CAPITAL_SESSION_PATH",
    "CapitalSession",
    "CapitalSessionError",
    "CapitalTokens",
    "PING_INTERVAL_SEC",
    "TOKEN_REFRESH_DEADLINE_SEC",
]

CAPITAL_BASE_DEMO: Final[str] = "https://demo-api-capital.backend-capital.com"
CAPITAL_BASE_LIVE: Final[str] = "https://api-capital.backend-capital.com"
CAPITAL_SESSION_PATH: Final[str] = "/api/v1/session"
CAPITAL_PING_PATH: Final[str] = "/api/v1/ping"
CAPITAL_ACCOUNTS_PATH: Final[str] = "/api/v1/accounts"

REST_TIMEOUT_SEC: Final[float] = 15.0
TOKEN_REFRESH_DEADLINE_SEC: Final[float] = 540.0  # 9 min (10 min idle - 1 min buffer)
PING_INTERVAL_SEC: Final[float] = 540.0
LOGIN_RATE_CAP_SEC: Final[float] = 1.05  # 1/s with small buffer
PING_RETRY_BACKOFF_SEC: Final[float] = 5.0  # backoff after a failed anti-idle cycle


class CapitalSessionError(RuntimeError):
    """Raised on auth / session lifecycle failures."""


@dataclass(frozen=True, slots=True)
class CapitalTokens:
    """Snapshot of CST + X-SECURITY-TOKEN with refresh deadline."""

    cst: str
    security_token: str
    minted_ts: float
    deadline_ts: float
    account_id: str | None = None

    def is_expired(self, *, now: float | None = None) -> bool:
        return (now or time.time()) >= self.deadline_ts


@dataclass(slots=True)
class CapitalSession:
    """Capital.com REST session with auto-refresh + anti-idle ping.

    Use as an async context manager so the background ping task is cleaned up.
    Set ``auto_ping=False`` to opt out (tests that stub HTTP).
    """

    api_key: str
    identifier: str
    password: str
    base_url: str = CAPITAL_BASE_DEMO
    client: httpx.AsyncClient | None = None
    auto_ping: bool = True
    _tokens: CapitalTokens | None = field(default=None, init=False)
    _last_login_ts: float = field(default=0.0, init=False)
    _login_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _ping_task: asyncio.Task[None] | None = field(default=None, init=False)
    _owns_client: bool = field(default=False, init=False)

    async def __aenter__(self) -> CapitalSession:
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=REST_TIMEOUT_SEC)
            self._owns_client = True
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        import contextlib

        if self._ping_task is not None:
            self._ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._ping_task
            self._ping_task = None
        if self._owns_client and self.client is not None:
            await self.client.aclose()
            self.client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def tokens(self) -> CapitalTokens | None:
        return self._tokens

    async def ensure_tokens(self) -> CapitalTokens:
        """Return valid tokens, logging in if missing/expired."""
        if self._tokens is not None and not self._tokens.is_expired():
            return self._tokens
        return await self.login()

    async def login(self) -> CapitalTokens:
        """Re-login (rate-limited to 1/s by Capital docs)."""
        async with self._login_lock:
            # Double-check inside lock to coalesce concurrent callers.
            if self._tokens is not None and not self._tokens.is_expired():
                return self._tokens
            wait = LOGIN_RATE_CAP_SEC - (time.time() - self._last_login_ts)
            if wait > 0.0:
                await asyncio.sleep(wait)
            cli = self._require_client()
            resp = await cli.post(
                CAPITAL_SESSION_PATH,
                headers={"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"},
                json={
                    "identifier": self.identifier,
                    "password": self.password,
                    "encryptedPassword": False,
                },
            )
            self._last_login_ts = time.time()
            if resp.status_code != 200:
                raise CapitalSessionError(
                    f"login failed status={resp.status_code} body={resp.text[:200]!r}"
                )
            cst = resp.headers.get("CST")
            sec = resp.headers.get("X-SECURITY-TOKEN")
            if not cst or not sec:
                raise CapitalSessionError("login missing CST or X-SECURITY-TOKEN headers")
            body = _safe_json(resp)
            account_id = (
                str(body.get("currentAccountId"))
                if isinstance(body, dict) and body.get("currentAccountId")
                else None
            )
            now = time.time()
            self._tokens = CapitalTokens(
                cst=cst,
                security_token=sec,
                minted_ts=now,
                deadline_ts=now + TOKEN_REFRESH_DEADLINE_SEC,
                account_id=account_id,
            )
            # Security: never log CST or X-SECURITY-TOKEN values; deadline is fine.
            logger.info(
                "[capital] login OK account_id=%s deadline_ts=%d",
                account_id or "-",
                int(now + TOKEN_REFRESH_DEADLINE_SEC),
            )
            # Auto-spawn anti-idle ping the first time tokens are minted so a
            # long-lived session does not silently expire (codex Day 5 P0 fix).
            if self.auto_ping:
                self.start_ping_loop()
            return self._tokens

    async def auth_headers(self) -> dict[str, str]:
        toks = await self.ensure_tokens()
        return {
            "CST": toks.cst,
            "X-SECURITY-TOKEN": toks.security_token,
            "Content-Type": "application/json",
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_on_expire: bool = True,
    ) -> httpx.Response:
        """Issue an authenticated request, refreshing tokens on 401."""
        cli = self._require_client()
        headers = await self.auth_headers()
        resp = await cli.request(method, path, params=params, json=json_body, headers=headers)
        if resp.status_code == 401 and retry_on_expire:
            # Force re-login + retry once.
            logger.warning(
                "[capital] 401 on %s %s — refreshing tokens + retrying once",
                method,
                path,
            )
            self._tokens = None
            headers = await self.auth_headers()
            resp = await cli.request(
                method, path, params=params, json=json_body, headers=headers
            )
        return resp

    async def ping(self) -> bool:
        """Anti-idle ping. Returns True on 200 OK."""
        try:
            resp = await self.request("GET", CAPITAL_PING_PATH, retry_on_expire=True)
        except httpx.HTTPError:
            return False
        ok = resp.status_code == 200
        if ok and self._tokens is not None:
            # Pushing the deadline forward keeps the session warm.
            self._tokens = CapitalTokens(
                cst=self._tokens.cst,
                security_token=self._tokens.security_token,
                minted_ts=self._tokens.minted_ts,
                deadline_ts=time.time() + TOKEN_REFRESH_DEADLINE_SEC,
                account_id=self._tokens.account_id,
            )
        return ok

    def start_ping_loop(self) -> None:
        """Spawn the background ping task (idempotent)."""
        if self._ping_task is not None and not self._ping_task.done():
            return
        logger.info(
            "[capital] anti-idle ping started interval=%.0fs",
            PING_INTERVAL_SEC,
        )
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def _ping_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(PING_INTERVAL_SEC)
                await self.ping()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — anti-idle is best-effort
                # Avoid tight loop on persistent failure.
                await asyncio.sleep(PING_RETRY_BACKOFF_SEC)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_client(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=REST_TIMEOUT_SEC)
            self._owns_client = True
        return self.client


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (ValueError, TypeError):
        return None
