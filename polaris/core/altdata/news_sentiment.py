"""News-sentiment collector (EVIDENCE only) — Alpaca News + async GPT classifier.

DEMO/PAPER. Reads recent market headlines from the Alpaca News REST API
(``/v1beta1/news``) using the SAME Alpaca paper credentials Polaris already
holds. Each article arrives PRE-TAGGED with its symbols (entity→ticker mapping
is solved upstream), so we map article symbols → universe symbols directly and
emit a per-symbol aggregate:

  ``{"AAPL": {"sentiment": +0.6, "relevance": 0.8, "magnitude": 0.7, "n": 3,
              "headline": "..."}, ...}``

sentiment ∈ [-1, +1] (bearish..bullish), relevance ∈ [0, 1], magnitude ∈ [0, 1].

Classification is an ASYNC GPT advisory call (``gpt-5-mini``, P0,
``reasoning_effort='minimal'``) made INSIDE ``fetch`` — headlines arrive on the
collector's own ~15min cadence, NOT per tick, so this is off the per-tick hot
path (in-loop GPT = 0 holds; the fuser/strategy hot path reads only the cached
deterministic numbers this returns). Runtime LLM = OpenAI GPT (Anthropic is
dev-only and never appears here).

A deterministic finance lexicon is the KEYLESS GRACEFUL FALLBACK: when the GPT
key is absent (or a GPT call fails) we score headlines with the lexicon so the
collector still emits evidence. The lexicon ALONE is a weak directional edge
(~coin-flip on return direction in the literature) — it is a fallback, not the
intended scorer; GPT is the primary classifier.

SIGNAL/EVIDENCE only: on a missing key / network / parse error this returns
``{}`` and contributes NO evidence — the price-only regime stands (graceful
fallback, NOT a defensive throttle). It NEVER raises.

Ref pattern: ``crypto_fg.py`` / ``fred_macro.py`` (collector contract),
``core/universe/_alpaca.py`` (Alpaca paper cred resolution).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sqlite3
from collections.abc import Callable
from typing import Any, Final

import httpx

from polaris.core.altdata.news_timing_shadow import log_news_timing_shadow
from polaris.core.universe._helpers import REST_TIMEOUT_SEC
from polaris.storage.db_writer import DBWriter

logger = logging.getLogger(__name__)

ALPACA_DATA_BASE: Final[str] = "https://data.alpaca.markets"
_NEWS_PATH: Final[str] = "/v1beta1/news"

# Currency: lower-bound the fetch window so a thin ticker's weeks-old headline is
# not pulled as 'latest'. Env-tunable (no_hardcode_in_plans); default 36h. This is
# a freshness WINDOW on the request, NOT a per-trade block (flow_not_block).
_RECENCY_HOURS_ENV: Final[str] = "POLARIS_NEWS_RECENCY_HOURS"
_RECENCY_HOURS_DEFAULT: Final[float] = 36.0

# Reuse the SAME env keys + ARCHIVE_* fallback as the Alpaca universe fetcher.
_API_KEY_ENV: Final[str] = "ALPACA_PAPER_API_KEY"
_SECRET_ENV: Final[str] = "ALPACA_PAPER_SECRET"
_API_KEY_ENV_FALLBACK: Final[str] = "ARCHIVE_ALPACA_PAPER_API_KEY"
_SECRET_ENV_FALLBACK: Final[str] = "ARCHIVE_ALPACA_PAPER_SECRET"

# Bound the per-fetch headline pull (Alpaca caps at 50/page; one page is plenty
# at the 15min cadence) and the GPT classification batch.
_NEWS_LIMIT: Final[int] = 50
_GPT_BATCH: Final[int] = 25

# gpt-5-mini classifier knobs (P0; reasoning_effort=minimal = no chain-of-thought).
_GPT_MODEL: Final[str] = "gpt-5-mini"
_GPT_MAX_TOKENS: Final[int] = 700
_GPT_TIMEOUT_SEC: Final[float] = 30.0

# Deterministic lexicon — weak keyless fallback only (GPT is the real scorer).
# Small, curated finance-tone word lists; a headline's score is the signed,
# count-normalised, magnitude-bounded difference of hits.
_BULL_WORDS: Final[frozenset[str]] = frozenset(
    {
        "beat", "beats", "surge", "surges", "surged", "rally", "rallies",
        "rallied", "record", "soar", "soars", "soared", "jump", "jumps",
        "jumped", "gain", "gains", "gained", "upgrade", "upgrades", "upgraded",
        "strong", "growth", "profit", "profits", "bullish", "outperform",
        "raises", "raise", "raised", "high", "highs", "rebound", "wins", "win",
        "approval", "approved", "inflows", "boom",
    }
)
_BEAR_WORDS: Final[frozenset[str]] = frozenset(
    {
        "miss", "misses", "missed", "plunge", "plunges", "plunged", "drop",
        "drops", "dropped", "fall", "falls", "fell", "slump", "slumps",
        "downgrade", "downgrades", "downgraded", "weak", "loss", "losses",
        "bearish", "underperform", "cut", "cuts", "fraud", "lawsuit",
        "bankruptcy", "default", "probe", "investigation", "recall", "warning",
        "low", "lows", "crash", "crashes", "selloff", "outflows", "halt",
    }
)


def _recency_hours() -> float:
    """Fetch recency-window size in hours (env-tunable; default 36)."""
    raw = os.environ.get(_RECENCY_HOURS_ENV)
    if not raw or not raw.strip():
        return _RECENCY_HOURS_DEFAULT
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _RECENCY_HOURS_DEFAULT
    return val if val > 0.0 else _RECENCY_HOURS_DEFAULT


def _headline_age_hours(created_at: Any, now: _dt.datetime) -> float | None:
    """Hours between an Alpaca ``created_at`` ISO8601 stamp and ``now``.

    Returns ``None`` for an absent / unparsable stamp (graceful — no fake age).
    """
    if not isinstance(created_at, str) or not created_at:
        return None
    try:
        ts = _dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.UTC)
    age = (now - ts).total_seconds() / 3600.0
    return age if age >= 0.0 else 0.0


def _resolve_creds(
    api_key: str | None, secret_key: str | None
) -> tuple[str | None, str | None]:
    """Resolve Alpaca paper creds: explicit args → bare env → ARCHIVE_* env.

    Empty-string env values count as absent (mirrors fred_macro / _alpaca).
    """
    api_key = (
        api_key
        or os.environ.get(_API_KEY_ENV)
        or os.environ.get(_API_KEY_ENV_FALLBACK)
    ) or None
    secret_key = (
        secret_key
        or os.environ.get(_SECRET_ENV)
        or os.environ.get(_SECRET_ENV_FALLBACK)
    ) or None
    return api_key, secret_key


def _lexicon_score(text: str) -> float:
    """Deterministic finance-tone score ∈ [-1, +1] (keyless fallback).

    Signed difference of bull/bear hits, normalised by total hits. No hits → 0.0
    (neutral). This is intentionally simple — a weak directional prior used ONLY
    when GPT is unavailable; the GPT classifier is the intended scorer.
    """
    if not text:
        return 0.0
    words = "".join(c.lower() if (c.isalnum() or c.isspace()) else " " for c in text).split()
    bull = sum(1 for w in words if w in _BULL_WORDS)
    bear = sum(1 for w in words if w in _BEAR_WORDS)
    total = bull + bear
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (bull - bear) / total))


class NewsSentimentCollector:
    """Alpaca News headlines → async-GPT-classified per-symbol sentiment.

    Mirrors the ``AltDataCollector`` contract (name / ttl_sec / asset_classes,
    async ``fetch`` returning a flat dict, ``{}`` on keyless/error, never raises).
    """

    name = "news_sentiment"
    ttl_sec = 900  # 15min — headline cadence (TTL honoured by the producer loop)
    asset_classes = ("crypto", "forex", "commodity", "index", "equity")

    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str = ALPACA_DATA_BASE,
        gpt_client_factory: Callable[[], Any] | None = None,
        shadow_conn: sqlite3.Connection | None = None,
        shadow_db_writer: DBWriter | None = None,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._base_url = base_url
        # Factory so tests can inject a stub; defaults to the OpenAI GPT client
        # (runtime LLM = OpenAI), resolved lazily so a missing key → lexicon.
        self._gpt_client_factory = gpt_client_factory
        # frontgate-scan item #7 (behavior-0): optional timestamp-audit +
        # syndicate-dedup SHADOW log. None (default) = byte-identical to the
        # pre-item-#7 collector — this is a pure additive side-effect, the
        # returned aggregate below is NEVER changed by its presence.
        self._shadow_conn = shadow_conn
        # db-writer-reader-split (opt-in, 2026-07-12): threaded through to
        # ``log_news_timing_shadow`` only when ``shadow_conn`` is ALSO wired
        # (same no-op-unless-shadow_conn contract). None is byte-identical.
        self._shadow_db_writer = shadow_db_writer

    async def fetch(
        self, *, client: httpx.AsyncClient | None = None
    ) -> dict[str, Any] | None:
        api_key, secret_key = _resolve_creds(self._api_key, self._secret_key)
        if not (api_key and secret_key):
            logger.info("[altdata] news_sentiment: no Alpaca creds (graceful skip)")
            return {}
        own = client is None
        cli = client or httpx.AsyncClient(base_url=self._base_url, timeout=REST_TIMEOUT_SEC)
        try:
            articles = await self._fetch_headlines(cli, api_key, secret_key)
            if not articles:
                return {}
            scored = await self._classify(articles)
            ref_now = _dt.datetime.now(_dt.UTC)
            self._log_timing_shadow(articles, scored, ref_now)
            return _aggregate(articles, scored, now=ref_now)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            logger.info("[altdata] news_sentiment fetch failed (graceful skip): %s", exc)
            return {}
        finally:
            if own:
                await cli.aclose()

    async def _fetch_headlines(
        self, cli: httpx.AsyncClient, api_key: str, secret_key: str
    ) -> list[dict[str, Any]]:
        # Currency: ``start`` lower-bounds the window so an inactive ticker's
        # weeks-old headline can never surface as the 'latest' (the audit's thin-
        # ticker stale-as-current case). flow_not_block: this is a fetch window,
        # never a per-trade block.
        start = (
            _dt.datetime.now(_dt.UTC) - _dt.timedelta(hours=_recency_hours())
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = await cli.get(
            _NEWS_PATH,
            params={"limit": str(_NEWS_LIMIT), "sort": "desc", "start": start},
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": secret_key,
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        body = resp.json()
        items = body.get("news") if isinstance(body, dict) else None
        out: list[dict[str, Any]] = []
        for idx, art in enumerate(items or []):
            if not isinstance(art, dict):
                continue
            headline = str(art.get("headline", "")).strip()
            symbols = [
                str(s).strip().upper()
                for s in (art.get("symbols") or [])
                if str(s).strip()
            ]
            if not headline or not symbols:
                continue
            out.append(
                {
                    "id": art.get("id", idx),
                    "headline": headline,
                    "summary": str(art.get("summary", "")).strip(),
                    "symbols": symbols,
                    "source": str(art.get("source", "")).strip(),
                    # Currency: preserve the Alpaca publish timestamp so the
                    # aggregate can surface per-symbol headline age (recency).
                    "created_at": str(art.get("created_at", "")).strip(),
                }
            )
        return out

    async def _classify(
        self, articles: list[dict[str, Any]]
    ) -> dict[Any, dict[str, float]]:
        """Score each article → {id: {sentiment, relevance, magnitude}}.

        Tries the async GPT classifier first (runtime LLM = OpenAI GPT). On a
        missing key OR any GPT error, falls back to the deterministic lexicon so
        the collector still emits evidence (graceful, never raises here).
        """
        gpt_scores = await self._classify_gpt(articles)
        if gpt_scores:
            return gpt_scores
        return _classify_lexicon(articles)

    async def _classify_gpt(
        self, articles: list[dict[str, Any]]
    ) -> dict[Any, dict[str, float]]:
        client = self._resolve_gpt_client()
        if client is None:
            return {}
        out: dict[Any, dict[str, float]] = {}
        try:
            for start in range(0, len(articles), _GPT_BATCH):
                batch = articles[start : start + _GPT_BATCH]
                parsed = await self._gpt_batch(client, batch)
                if parsed is None:
                    return {}  # GPT path failed → caller uses lexicon
                for row in parsed.get("results") or []:
                    if not isinstance(row, dict):
                        continue
                    rid = row.get("id")
                    if rid is None:
                        continue
                    # Key by str(id): GPT may echo a numeric id as a JSON string,
                    # so canonicalise both sides (here + _aggregate) to str — a
                    # mismatch would silently drop the whole (paid) batch.
                    out[str(rid)] = {
                        "sentiment": _clamp(row.get("sentiment"), -1.0, 1.0),
                        "relevance": _clamp(row.get("relevance"), 0.0, 1.0, default=0.5),
                        "magnitude": _clamp(row.get("magnitude"), 0.0, 1.0, default=0.5),
                    }
        except Exception as exc:  # noqa: BLE001 — GPT failure → lexicon fallback, never raise
            logger.info("[altdata] news_sentiment GPT classify failed → lexicon: %s", exc)
            return {}
        return out

    def _resolve_gpt_client(self) -> Any | None:
        if self._gpt_client_factory is not None:
            try:
                return self._gpt_client_factory()
            except Exception:  # noqa: BLE001 — no client → lexicon fallback
                return None
        # Default: OpenAI GPT client, keyed from OPENAI_API_KEY. Missing key →
        # None → lexicon fallback (mirrors the gate no_gpt_client dispatch).
        try:
            from polaris.core.pipeline.agents._gpt_client import default_gpt_factory

            return default_gpt_factory(_GPT_MODEL)
        except Exception:  # noqa: BLE001 — import/key failure → lexicon fallback
            return None

    async def _gpt_batch(
        self, client: Any, batch: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        items = [
            {"id": a["id"], "headline": a["headline"], "summary": a["summary"][:200]}
            for a in batch
        ]
        system = [
            {
                "type": "text",
                "text": (
                    "# Role\nYou classify financial-market news headlines for a "
                    "DEMO/PAPER trading bot (virtual capital — real-money safety "
                    "framing is INVALID).\n"
                    "For each item return sentiment ∈ [-1,1] (bearish..bullish), "
                    "relevance ∈ [0,1] (how market-moving for the tagged ticker), "
                    "magnitude ∈ [0,1] (how strong the move implication is).\n"
                    "# Output\nReturn ONE JSON object only, no prose: "
                    '{"results":[{"id":<id>,"sentiment":<f>,"relevance":<f>,'
                    '"magnitude":<f>}, ...]}'
                ),
            }
        ]
        user = "# Headlines\n" + json.dumps(items, separators=(",", ":"))
        response = await client.messages.create(
            model=_GPT_MODEL,
            max_tokens=_GPT_MAX_TOKENS,
            temperature=0.0,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=_GPT_TIMEOUT_SEC,
            reasoning_effort="minimal",
        )
        return _extract_json(response)

    def _log_timing_shadow(
        self,
        articles: list[dict[str, Any]],
        scored: dict[Any, dict[str, float]],
        now: _dt.datetime,
    ) -> None:
        """frontgate-scan item #7 SHADOW hook — no-op unless ``shadow_conn`` was
        supplied at construction. Fail-open: any exception here is swallowed so
        a shadow-logging fault can never break the collector's real evidence
        return (this runs BEFORE the ``_aggregate`` call it must not affect)."""
        if self._shadow_conn is None:
            return
        try:
            log_news_timing_shadow(
                self._shadow_conn, articles=articles, scored=scored, now=now,
                db_writer=self._shadow_db_writer,
            )
        except Exception as exc:  # noqa: BLE001 — instrumentation must never break fetch()
            logger.warning("[altdata] news_timing_shadow log dropped: %r", exc)


def _classify_lexicon(
    articles: list[dict[str, Any]]
) -> dict[Any, dict[str, float]]:
    """Deterministic keyless fallback: lexicon score per article."""
    out: dict[Any, dict[str, float]] = {}
    for a in articles:
        text = f"{a['headline']} {a.get('summary', '')}"
        sent = _lexicon_score(text)
        # str(id) to match the _aggregate lookup (GPT path keys the same way).
        out[str(a["id"])] = {
            "sentiment": sent,
            # Lexicon has no real relevance/magnitude signal → modest fixed prior
            # so its evidence weight stays conservative vs the GPT path.
            "relevance": 0.4,
            "magnitude": min(1.0, abs(sent)),
        }
    return out


_CRYPTO_QUOTE_SUFFIXES: Final[tuple[str, ...]] = ("USDT", "USDC", "USD")


def _emit_keys(symbols: list[str]) -> list[str]:
    """Per-article output keys: the raw symbol PLUS a crypto-base alias.

    Alpaca tags crypto in pair form (``BTCUSD`` / ``BTC/USD`` / ``BTCUSDT``) but
    the fuser routes a crypto group as ``crypto:<BASE>`` (e.g. ``crypto:BTC`` —
    ``compute_underlying_group_id`` strips the quote ccy). Without an alias the
    fuser's ``news.get("BTC")`` would miss and crypto news (the highest-weight
    news class) would be silently dropped. We therefore emit BOTH keys: the raw
    symbol (so equity ``AAPL`` / forex ``EURUSD`` lookups hit) and the stripped
    base (so ``crypto:BTC`` hits). Emitting an extra harmless alias for a forex
    pair (``EURUSD`` → ``EUR``) is a no-op — the forex fuser never looks it up.
    De-duped, raw-first order preserved.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        sym = raw.strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
        # Derive a crypto-base alias: drop a slash/dash separator then a quote
        # suffix (BTC/USD, BTC-USD, BTCUSDT → BTC). Only adds a key, never drops.
        base = sym.replace("/", "").replace("-", "")
        for suf in _CRYPTO_QUOTE_SUFFIXES:
            if base.endswith(suf) and len(base) > len(suf):
                base = base[: -len(suf)]
                break
        if base and base != sym and base not in seen:
            seen.add(base)
            out.append(base)
    return out


def _aggregate(
    articles: list[dict[str, Any]],
    scored: dict[Any, dict[str, float]],
    *,
    now: _dt.datetime | None = None,
) -> dict[str, Any]:
    """Fold per-article scores into a per-symbol relevance-weighted aggregate.

    Each article fans out to all its tagged symbols. Per symbol we keep a
    relevance-weighted mean sentiment, the max relevance/magnitude seen, the
    headline count ``n``, the single most-relevant headline (display), and the
    OLDEST contributing headline age in hours (``news_max_age_h``) so a thin
    ticker's stale headline is visible to the judge (currency). Age is surfaced
    only when at least one contributing headline carries a parsable timestamp.
    """
    ref_now = now if now is not None else _dt.datetime.now(_dt.UTC)
    acc: dict[str, dict[str, Any]] = {}
    for a in articles:
        score = scored.get(str(a["id"]))
        if score is None:
            continue
        sent = float(score["sentiment"])
        rel = float(score["relevance"])
        mag = float(score["magnitude"])
        age_h = _headline_age_hours(a.get("created_at"), ref_now)
        for sym in _emit_keys(a["symbols"]):
            row = acc.setdefault(
                sym,
                {"wsum": 0.0, "wt": 0.0, "relevance": 0.0, "magnitude": 0.0,
                 "n": 0, "headline": "", "_best_rel": -1.0, "_max_age_h": None},
            )
            weight = rel if rel > 0.0 else 0.01
            row["wsum"] += sent * weight
            row["wt"] += weight
            row["relevance"] = max(row["relevance"], rel)
            row["magnitude"] = max(row["magnitude"], mag)
            row["n"] += 1
            if age_h is not None:
                prev = row["_max_age_h"]
                row["_max_age_h"] = age_h if prev is None else max(prev, age_h)
            if rel > row["_best_rel"]:
                row["_best_rel"] = rel
                row["headline"] = a["headline"]
    out: dict[str, Any] = {}
    for sym, row in acc.items():
        wt = row["wt"]
        agg_sent = (row["wsum"] / wt) if wt > 0.0 else 0.0
        entry: dict[str, Any] = {
            "sentiment": round(max(-1.0, min(1.0, agg_sent)), 4),
            "relevance": round(row["relevance"], 4),
            "magnitude": round(row["magnitude"], 4),
            "n": int(row["n"]),
            "headline": row["headline"],
        }
        if row["_max_age_h"] is not None:
            entry["news_max_age_h"] = round(float(row["_max_age_h"]), 2)
        out[sym] = entry
    return out


def _clamp(value: Any, lo: float, hi: float, *, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _extract_json(response: Any) -> dict[str, Any] | None:
    """Pull a JSON object out of a GPTClient-shaped response (tolerant)."""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if not content:
        return None
    pieces: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text", "")
        if text:
            pieces.append(str(text))
    raw = "".join(pieces).strip()
    if not raw:
        return None
    if raw.startswith("```"):
        nl = raw.find("\n")
        if nl != -1:
            raw = raw[nl + 1 :]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
