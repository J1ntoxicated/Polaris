"""Canonical risk-unit (R) definition — the ONE measurement SSOT.

The 2026-06-22 design audit found the same trade carried THREE different R
values (dashboard ``$10`` proxy / confidence ``$50`` proxy / positions ATR
denominator) and that the R ledger disagreed with the ``fills.pnl_usd`` dollar
ledger. Step M unified them onto a single per-trade ATR ``risk_usd`` denominator.

Step N (2026-06-23, Jin LOCKED) — the STREAM-COMMON R
-----------------------------------------------------
A deeper measurement audit then found the Step-M ``risk_usd`` denominator,
though internally consistent WITHIN a venue, is NOT comparable ACROSS venues:
``risk_usd = entry_price · entry_atr_pct · STOP_ATR_MULT · base_qty`` anchors on
each strategy's OWN timeframe ATR (1D for Alpaca, 1m for OKX/Capital), so "1R"
is a DIFFERENT physical time-horizon per stream. A daily ATR is ~390× a 1-minute
ATR, so the SAME ~$1k notional yielded a risk unit ~123× larger on Alpaca than
OKX — a −$2 OKX loss read as multiple R while a −$44 Alpaca loss read as ~−0.25R.

The fix REDEFINES the realised-R denominator as a per-stream **R_budget** — the
stream's INTENDED per-trade dollar risk budget, a fixed constant per stream::

    R_budget(stream) = base_risk_pct(0.02) · starting_equity(stream)
      OKX     = 0.02 · 79,000 = $1,580
      Capital = 0.02 · 51,000 = $1,020
      Alpaca  = 0.02 · starting_equity  (live-probed; deterministic fallback)

    realised_R = realised_pnl_usd / R_budget(stream)

Why R_budget (not the ATR ``risk_usd``):
  * COMPARABLE cross-venue — a $X outcome maps to the SAME R order of magnitude
    regardless of venue, scaled only by each stream's intended per-trade risk.
    No ATR-timeframe leak. A −$2 loss is ~comparable R on every venue.
  * WITHIN-venue ranking preserved — R_budget is a per-stream CONSTANT, so within
    a stream R is a pure linear rescale of pnl_usd → identical bleeder ordering
    and ``sign(R) == sign(pnl_usd)`` (the two ledgers stay reconciled).
  * SIMPLE / AUDITABLE — two existing SSOT numbers, no per-trade ATR read, no
    clamp/floor artefact, deterministic.

The ATR-based ``risk_usd`` (per-trade stop distance) is RETAINED but DEMOTED to
a sizing/excursion input (mfe_r/mae_r stop-quality telemetry) — it is NO LONGER
the cross-venue realised-R denominator. ``realised_r``/``risk_usd_at_entry`` are
kept for that per-trade-ATR excursion unit (a DIFFERENT R than the realised
stream-common R — do not re-conflate them).

ATR sanity bounds
-----------------
The audit found 46.6% of ``entry_atr_pct`` anchors were outliers — both
collapsed (flat/stale bars → denominator → 0 → R explodes) and absurdly high.
``clamp_entry_atr_pct`` bounds the anchor to a sane band BEFORE it becomes a
denominator, so ``risk_usd`` can neither collapse nor balloon.

Clamp
-----
``R_CLAMP`` (±100R) is the single telemetry bound shared by the realised path
(``real_pnl_r_from_fills``), the excursion path (``_EXCURSION_R_CAP``) and the
decision path (``compute_unrealized_pnl_r`` — previously a hidden ±10 that
masked −34..−100R losses from G6/G7/precise-exit). One number everywhere.

Measurement only — never sizes, gates, blocks, or throttles. flow_not_block /
aggressive bias intact (DEMO/PAPER).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Final

from polaris.core.sizing.constants import (
    demo_starting_equity_alpaca,
    demo_starting_equity_capital,
    demo_starting_equity_okx,
)
from polaris.core.streams.config import VENUE_TO_STREAM

__all__ = [
    "BASE_RISK_PCT",
    "ENTRY_ATR_PCT_MAX",
    "ENTRY_ATR_PCT_MIN",
    "R_CLAMP",
    "R_USD_PROXY",
    "RISK_USD_ABS_FLOOR",
    "RISK_USD_NOTIONAL_PCT",
    "STOP_ATR_MULT",
    "clamp_entry_atr_pct",
    "clamp_r",
    "r_budget_for_stream",
    "r_budget_for_venue",
    "realised_r",
    "realised_r_stream",
    "risk_usd_at_entry",
]

# Common per-trade risk fraction across ALL streams (SizingProfile.base_risk_pct
# in streams.config — A/B/C all 0.02). The stream-common R denominator is this
# fraction of each stream's starting equity. Kept here as the R-measurement SSOT
# so the denominator never silently drifts from the sizing profile.
BASE_RISK_PCT: Final[float] = 0.02

# ONE shared $-per-R fallback proxy for the display paths that have ONLY a
# pnl_usd and no per-trade ``risk_usd`` to rescale by (legacy / smoke close
# fills, the confidence panel's cross-strategy projection). This REPLACES the
# inconsistent dashboard ``$10`` (DEFAULT_R_USD) and confidence ``$50``
# (PNL_R_USD_DENOM) split so the SAME trade can never show two different R on
# two panels. The risk$-based ``realised_r`` is always preferred where a
# per-trade ``risk_usd`` exists; this is the documented fallback only. Display
# only — never sizes/gates.
R_USD_PROXY: Final[float] = 25.0

# Default stop distance in ATR units. 2 ATR matches the volume_burst /
# spot_donchian initial-stop convention and is the multiplier every R
# denominator already used (``entry_price * atr_pct * 2``). Keeping it here as
# the SSOT means the close path, the decision path, and the dashboard all carve
# the same 1R ruler.
STOP_ATR_MULT: Final[float] = 2.0

# Single telemetry clamp for EVERY R surface (realised / excursion / decision).
# ±100R bounds atr-floor artefacts without hiding real catastrophic losses (the
# old ±10 on the decision path masked −34..−100R moves from G6/G7/precise-exit).
R_CLAMP: Final[float] = 100.0

# Entry-ATR% sanity band (audit: 46.6% outliers). A 1m/intraday ATR% below
# ~5 bps is almost always a flat/stale-bar collapse (denominator → 0 → R
# explodes); above 25% is an absurd anchor that would crush R toward 0 and hide
# a real loss. Clamping the ANCHOR (not the R) keeps risk_usd finite and
# meaningful while leaving the dollar truth untouched.
ENTRY_ATR_PCT_MIN: Final[float] = 5e-4   # 0.05%
ENTRY_ATR_PCT_MAX: Final[float] = 0.25   # 25%

# risk_usd FLOOR (anti-phantom-R). [[harvest_generalization_2026-06-23]]: the ATR
# anchor clamp above bounds the atr% term, but on a TINY notional (small
# base_qty × price — measured as low as $130 on OKX) the resulting risk_usd still
# collapsed to ~$0.0058, so a trivial -$2 loss read as a phantom multi-R. That
# poisons the per-trade excursion unit (mfe_r/mae_r) that trains the learner and
# ranks bleeders. The floor lifts a degenerate-small risk unit to the LARGER of an
# absolute floor and a small fraction of the position's NOTIONAL
# (entry_price × base_qty), so |R| on a real $ outcome cannot explode. It only
# ever RAISES a too-small risk unit — a genuine (real-ATR × size) risk_usd is far
# above the floor and untouched. Measurement-only — never sizes / gates / blocks /
# throttles (the dollar pnl_usd truth is never altered).
RISK_USD_ABS_FLOOR: Final[float] = 0.50      # absolute $ floor (micro-notional)
RISK_USD_NOTIONAL_PCT: Final[float] = 0.005  # 0.5% of notional


def clamp_entry_atr_pct(atr_pct: float) -> float:
    """Bound an entry ATR% anchor into the sane band (prevents R blow-up/collapse).

    Non-finite / non-positive inputs return ``ENTRY_ATR_PCT_MIN`` so the
    denominator is always usable. This shapes the DENOMINATOR only — the
    realised ``pnl_usd`` dollar truth is never altered.
    """
    if not math.isfinite(atr_pct) or atr_pct <= 0.0:
        return ENTRY_ATR_PCT_MIN
    return max(ENTRY_ATR_PCT_MIN, min(ENTRY_ATR_PCT_MAX, atr_pct))


def clamp_r(r: float) -> float:
    """Bound an R value to ``±R_CLAMP`` (the one telemetry clamp).

    NaN → 0.0 (no information). ``±inf`` saturate to the clamp bound (a genuine
    overflow is a max win/loss, not a neutral zero).
    """
    if math.isnan(r):
        return 0.0
    return max(-R_CLAMP, min(R_CLAMP, r))


def risk_usd_at_entry(
    *, entry_price: float, entry_atr_pct: float, base_qty: float,
    stop_atr_mult: float = STOP_ATR_MULT, quote_usd_rate: float = 1.0,
) -> float:
    """Intended 1R in dollars = stop distance × filled size, FLOORED.

    ``risk_usd = entry_price * clamp(entry_atr_pct) * stop_atr_mult * base_qty``,
    then lifted to ``max(risk_usd, RISK_USD_ABS_FLOOR,
    RISK_USD_NOTIONAL_PCT * notional)`` where ``notional = entry_price *
    base_qty`` — all in the price's QUOTE currency — then converted to USD via
    ``quote_usd_rate`` (default 1.0 = already-USD-quoted, e.g. OKX USDT pairs).
    A non-USD-quoted Capital instrument (USDJPY, J225=JPY, EU50=EUR, …) MUST
    pass the real quote→USD rate or ``risk_usd`` inflates/deflates by the raw FX
    level (live: J225 risk_usd stamped $47,615.89 vs real ≈$317, USDJPY ~40×) —
    audit [[trade_mess_full_audit_2026-07-02_verdict]] rank 4. The ATR% is
    clamped into the sane band first so a flat/stale anchor cannot collapse the
    unit; the notional floor then guards against a tiny-notional collapse (a
    $130 OKX order → ~$0.0058 risk_usd → phantom multi-R on a -$2 loss). The
    floor only ever RAISES a degenerate-small unit — a real (ATR × size)
    risk_usd is far above it. Returns 0.0 only when entry price / qty / rate are
    non-positive (an unknowable risk unit) — the caller keeps R at 0.
    Measurement-only; the ``pnl_usd`` dollar truth is never altered.
    """
    if (
        entry_price <= 0.0 or base_qty <= 0.0 or stop_atr_mult <= 0.0
        or quote_usd_rate <= 0.0
    ):
        return 0.0
    atr_pct = clamp_entry_atr_pct(entry_atr_pct)
    risk = entry_price * atr_pct * stop_atr_mult * base_qty * quote_usd_rate
    notional = entry_price * base_qty * quote_usd_rate
    return max(risk, RISK_USD_ABS_FLOOR, RISK_USD_NOTIONAL_PCT * notional)


def realised_r(*, pnl_usd: float, risk_usd: float) -> float:
    """Per-trade-ATR R = the dollar truth rescaled by the entry stop unit.

    ``R = pnl_usd / risk_usd``, clamped to ``±R_CLAMP``. A non-positive /
    non-finite ``risk_usd`` yields 0.0. Step N: this is the EXCURSION / per-trade
    stop-quality unit (mfe_r / mae_r), NOT the cross-venue realised-R denominator
    — that is now ``realised_r_stream`` (per-stream R_budget). Kept for the
    excursion path, which is correctly a per-trade stop-distance concept.
    """
    if risk_usd <= 0.0 or not math.isfinite(risk_usd):
        return 0.0
    return clamp_r(pnl_usd / risk_usd)


# Per-stream R_budget = BASE_RISK_PCT × the stream's starting equity. Resolved
# lazily (the equity helpers honour env overrides) so a test/operator override of
# the demo equity flows straight through to the R denominator. OKX $1,580 /
# Capital $1,020 / Alpaca 0.02 × (probe-or-fallback). Display/measurement only.
_R_BUDGET_EQUITY_BY_STREAM: Final[dict[str, Callable[[], float]]] = {
    "A_okx_crypto": demo_starting_equity_okx,
    "B_capital_cfd": demo_starting_equity_capital,
    "C_alpaca_equity": demo_starting_equity_alpaca,
}


def r_budget_for_stream(stream_id: str, *, equity_override: float | None = None) -> float:
    """Per-trade dollar risk budget for a stream = ``BASE_RISK_PCT × equity``.

    This is the STREAM-COMMON realised-R denominator (Step N). ``equity_override``
    (e.g. the live Alpaca ``/v2/account`` probe) supersedes the per-stream
    starting-equity constant when supplied and positive; otherwise the SSOT demo
    starting-equity helper is used (Alpaca falls back to its deterministic
    constant when no probe is wired). Returns 0.0 for an unknown stream so the
    caller keeps R at 0 (never an exploded denominator).
    """
    if equity_override is not None and equity_override > 0.0:
        return BASE_RISK_PCT * equity_override
    equity_fn = _R_BUDGET_EQUITY_BY_STREAM.get(stream_id)
    if equity_fn is None:
        return 0.0
    equity = float(equity_fn())
    return BASE_RISK_PCT * equity if equity > 0.0 else 0.0


def r_budget_for_venue(venue: str, *, equity_override: float | None = None) -> float:
    """Per-trade dollar risk budget resolved by venue (okx/capital/alpaca).

    Thin venue→stream wrapper over :func:`r_budget_for_stream` (the close path +
    dashboard know the venue, not the stream_id). Unknown venue → 0.0.
    """
    stream_id = VENUE_TO_STREAM.get((venue or "").lower())
    if stream_id is None:
        return 0.0
    return r_budget_for_stream(stream_id, equity_override=equity_override)


def realised_r_stream(
    *, pnl_usd: float, venue: str, equity_override: float | None = None,
) -> float:
    """STREAM-COMMON realised R = ``pnl_usd / R_budget(stream)`` (Step N).

    The cross-venue-comparable realised-R denominator: each stream's intended
    per-trade dollar risk budget (a per-stream CONSTANT), so the SAME $ outcome
    maps to a comparable R on every venue and within a stream R is a pure linear
    rescale of the dollar truth (``sign(R) == sign(pnl_usd)``, identical bleeder
    ranking — the ledgers stay reconciled). Clamped to ``±R_CLAMP``. An unknown
    venue / undefined budget yields 0.0 (never an exploded R). Measurement only.
    """
    budget = r_budget_for_venue(venue, equity_override=equity_override)
    if budget <= 0.0 or not math.isfinite(budget):
        return 0.0
    return clamp_r(pnl_usd / budget)
