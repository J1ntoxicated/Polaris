"""ADR-012 — Slice 1 probe catalog (four ROW-ONLY probes, no alt-data).

DEMO/PAPER. Each probe is PURE / SYNC and ABSTAINS (returns ``None``) when its
inputs are absent. A probe ONLY describes (signed ``lean`` + ``confidence`` +
``evidence``); it NEVER names an action, closes, sizes, or blocks.

  * **ProfitTakingProbe** — the headline MFE-round-trip lever. ``giveback =
    mfe_r − pnl_r`` → harvest-pressure (adverse) lean as a winner round-trips.
  * **LossDefenseProbe** — adverse excursion (``mae_r``) + still-underwater
    pnl_r + held_seconds → adverse lean (precise exit-timing, never a throttle).
  * **TechnicalProbe** — ``atr_slope`` + recent_ticks momentum → momentum
    (favorable) vs exhaustion (adverse) lean.
  * **SessionHoursProbe** — TIME ONLY: ``seconds_to_close`` → adverse lean that
    grows as the session close nears. Never reads pnl.
  * **RegimeFitProbe** (W2, backgate-plan design-exit-matrix.md §B) —
    ``regime_fit(signal_family, regime)`` as a composite-lean SHADOW: the SAME
    deterministic (family × regime) table already applied directly in
    ``_production_tick_mfe`` (harvest/protect rungs), now also observed here so
    the offline calibrator can compare it against realized outcomes. Parallel
    SHADOW, not a replacement — observe mode threads zero knobs either way, so
    this can never double-tighten the live exit (§ conflict-resolution ④).
  * **LiquidityProbe** (P3 promotion, 2026-07-16, vault/50_research/
    built-not-wired-audit.md — orphan-feed wiring) — universe ``vol_24h_usd``
    + quote-book ``spread_bps`` → an ADVERSE-ONLY exit-lean as the market
    thins (structural response to the STETH/SPK phantom-liquidity class of
    incident: a position whose instrument has gone thin is exactly the one a
    tighter trail protects). Never favorable — deep/tight liquidity is the
    NEUTRAL baseline, not a reward.
  * **FundingProbe** (P3 promotion, same audit) — ``okx_funding`` mean perp
    funding rate → signed carry-cost pressure on THIS position's side (a long
    paying positive funding is adverse; a short collecting it is favorable,
    and vice-versa for negative funding).
"""

from __future__ import annotations

from polaris.core.probes import (
    ProbeContext,
    ProbeKind,
    ProbeReading,
    ProbeRole,
    _clamp,
)
from polaris.core.regime_fit import regime_fit
from polaris.core.universe.schema import DEFAULT_MAX_SPREAD_BPS, liquidity_floor_for_venue
from polaris.strategies.okx_funding_carry_persist import FUNDING_THRESHOLD

__all__ = [
    "FundingProbe",
    "LiquidityProbe",
    "LossDefenseProbe",
    "ProfitTakingProbe",
    "RegimeFitProbe",
    "SessionHoursProbe",
    "TechnicalProbe",
]

# --- calibratable constants (ADR-012 §/debate flags — never silent hardcode) --
# ProfitTaking: a giveback (mfe − pnl) of this many R is treated as full
# harvest-pressure (lean → -1). Below it the lean scales linearly.
_GIVEBACK_FULL_R: float = 1.0
# A position must have reached at least this MFE to even consider giveback.
_GIVEBACK_MIN_MFE_R: float = 0.20
# LossDefense: an adverse excursion this deep (R) is full adverse lean.
_MAE_FULL_R: float = 1.0
# A stale loser held past this many seconds adds to the adverse lean.
_LOSS_STALE_SEC: float = 600.0
# Technical: an atr_slope of this magnitude saturates the momentum lean.
_ATR_SLOPE_FULL: float = 0.01
# Session: lean ramps from 0 at this many seconds-to-close up to full at 0.
_SESSION_LEAD_SEC: float = 1800.0
# Liquidity: spread this many bps ABOVE the venue's universe-membership cap
# (DEFAULT_MAX_SPREAD_BPS) is full adverse — reuses the SAME threshold the
# universe-eligibility gate already applies (discovery.py / entrance.py),
# never a new independent number.
_SPREAD_FULL_BPS: float = DEFAULT_MAX_SPREAD_BPS
# Funding: a carry-cost pressure this many R (fraction) deep is full adverse.
# 3x okx_funding_carry_persist.FUNDING_THRESHOLD (-0.0003, "moderate
# persistent-negative funding" already verified as a genuine trading signal
# elsewhere in this codebase) — a 3x-moderate print is a clearly EXTREME
# funding regime, not a fresh guess.
_FUNDING_FULL: float = abs(FUNDING_THRESHOLD) * 3.0


class ProfitTakingProbe:
    """Giveback (mfe_r − pnl_r) → harvest-pressure adverse lean (the top lever)."""

    probe_id: str = "profit_taking"
    kind: ProbeKind = "profit"
    role: ProbeRole = "Exit"  # SSOT: PROBE_ROLE_REGISTRY (hardening #8)

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
        # ABSTAIN until the position has shown a meaningful favorable excursion.
        if ctx.mfe_r < _GIVEBACK_MIN_MFE_R:
            return None
        giveback_r = ctx.mfe_r - ctx.pnl_r
        if giveback_r <= 0.0:
            return None
        # Adverse (negative) lean — favor harvesting the round-trip EARLIER.
        lean = -_clamp(giveback_r / _GIVEBACK_FULL_R, 0.0, 1.0)
        # Confidence scales with how much MFE there was to give back.
        confidence = _clamp(ctx.mfe_r / (_GIVEBACK_FULL_R * 1.5), 0.0, 1.0)
        return ProbeReading(
            probe_id=self.probe_id,
            kind=self.kind,
            lean=lean,
            confidence=confidence,
            evidence={
                "mfe_r": round(ctx.mfe_r, 4),
                "pnl_r": round(ctx.pnl_r, 4),
                "giveback_r": round(giveback_r, 4),
            },
        )


class LossDefenseProbe:
    """Adverse excursion + underwater pnl + stale hold → adverse exit-timing lean."""

    probe_id: str = "loss_defense"
    kind: ProbeKind = "loss"
    role: ProbeRole = "Exit"  # SSOT: PROBE_ROLE_REGISTRY (hardening #8)

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
        # ABSTAIN when there is no adverse excursion to defend against.
        if ctx.mae_r >= 0.0:
            return None
        adverse = _clamp(abs(ctx.mae_r) / _MAE_FULL_R, 0.0, 1.0)
        # Underwater pnl deepens the lean; a stale dead loser deepens it further.
        underwater = _clamp(-ctx.pnl_r, 0.0, 1.0) if ctx.pnl_r < 0.0 else 0.0
        stale = _clamp(ctx.held_seconds / _SESSION_LEAD_SEC, 0.0, 1.0) * (
            1.0 if ctx.pnl_r < 0.0 else 0.0
        )
        raw = -_clamp((adverse + underwater + stale) / 3.0 * 1.5, 0.0, 1.0)
        if raw == 0.0:
            return None
        confidence = _clamp((adverse + underwater) / 2.0, 0.0, 1.0)
        return ProbeReading(
            probe_id=self.probe_id,
            kind=self.kind,
            lean=raw,
            confidence=confidence,
            evidence={
                "mae_r": round(ctx.mae_r, 4),
                "pnl_r": round(ctx.pnl_r, 4),
                "held_seconds": ctx.held_seconds,
            },
        )


class TechnicalProbe:
    """ATR slope + recent-tick momentum → momentum (favorable) / exhaustion lean."""

    probe_id: str = "technical"
    kind: ProbeKind = "technical"
    role: ProbeRole = "Position"  # SSOT: PROBE_ROLE_REGISTRY (hardening #8)

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
        ticks = ctx.recent_ticks
        if not ticks or len(ticks) < 2:
            return None
        # Recent-tick drift in the position's favor (long: up = favorable).
        first_close = float(ticks[0].get("close", ctx.entry_price))
        last_close = float(ticks[-1].get("close", first_close))
        if first_close <= 0.0:
            return None
        drift = (last_close - first_close) / first_close
        if ctx.side != "long":
            drift = -drift
        # Expanding ATR in the drift direction = momentum (favorable); contracting
        # against a stalled drift = exhaustion (adverse).
        slope_term = _clamp(ctx.atr_slope / _ATR_SLOPE_FULL, -1.0, 1.0)
        drift_term = _clamp(drift / 0.01, -1.0, 1.0)
        lean = _clamp((drift_term + slope_term) / 2.0, -1.0, 1.0)
        confidence = _clamp(abs(drift_term) * 0.5 + 0.25, 0.0, 1.0)
        return ProbeReading(
            probe_id=self.probe_id,
            kind=self.kind,
            lean=lean,
            confidence=confidence,
            evidence={
                "atr_slope": round(ctx.atr_slope, 6),
                "drift": round(drift, 6),
                "n_ticks": len(ticks),
            },
        )


class SessionHoursProbe:
    """TIME ONLY — adverse lean grows as the session close nears. Never reads pnl."""

    probe_id: str = "session_hours"
    kind: ProbeKind = "session"
    role: ProbeRole = "Exit"  # SSOT: PROBE_ROLE_REGISTRY (hardening #8)

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
        secs = ctx.seconds_to_close
        if secs is None:
            return None  # no close to lean toward → ABSTAIN
        # Ramp: lean 0 at >= lead, -1 at the close (0s). TIME only — pnl-blind.
        proximity = _clamp(1.0 - (secs / _SESSION_LEAD_SEC), 0.0, 1.0)
        lean = -proximity
        if lean == 0.0:
            return None
        confidence = proximity
        return ProbeReading(
            probe_id=self.probe_id,
            kind=self.kind,
            lean=lean,
            confidence=confidence,
            evidence={"seconds_to_close": int(secs)},
        )


class RegimeFitProbe:
    """``regime_fit(signal_family, regime)`` as a composite-lean SHADOW (W2).

    Deterministic — a real (family, regime) table hit is FULL confidence (the
    fit is exact, not a noisy estimate); an unrecognised family or unknown/None
    regime degrades to ``regime_fit`` neutral (``0.0``), which ABSTAINs here
    rather than injecting a false-confident zero into the composite.
    """

    probe_id: str = "regime_fit"
    kind: ProbeKind = "regime"
    role: ProbeRole = "Exit"  # SSOT: PROBE_ROLE_REGISTRY (W2)

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
        fit = regime_fit(ctx.signal_family, ctx.regime)
        if fit == 0.0:
            return None  # unrecognised family / unknown regime → ABSTAIN
        return ProbeReading(
            probe_id=self.probe_id,
            kind=self.kind,
            lean=_clamp(fit, -1.0, 1.0),
            confidence=1.0,
            evidence={
                "signal_family": ctx.signal_family,
                "regime": ctx.regime,
                "fit": round(fit, 4),
            },
        )


class LiquidityProbe:
    """universe vol_24h_usd + quote-book spread_bps → ADVERSE-ONLY exit-lean.

    P3 promotion (2026-07-16, vault/50_research/built-not-wired-audit.md) —
    the orphan-feed sweep flagged universe liquidity + the quote-book state as
    collected-but-unconsumed. Structural response to the STETH/SPK phantom-
    liquidity class of incident (vault): a position whose instrument has gone
    thin (below the venue's own eligibility floor) or whose spread has widened
    past the venue's own eligibility cap is exactly the one a tighter trail
    protects — this NEVER produces a favorable lean (deep/tight liquidity is
    the neutral baseline, not a reward; ``aggressive_always_profit`` / flow_
    not_block: this is a PRECISION signal for the SAME tighten pathway
    TIGHTEN/HARVEST already use, never a new block/size-cut).
    """

    probe_id: str = "liquidity"
    kind: ProbeKind = "liquidity"
    role: ProbeRole = "Position"  # SSOT: PROBE_ROLE_REGISTRY

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
        if ctx.vol_24h_usd is None and ctx.spread_bps is None:
            return None  # neither datum present → ABSTAIN
        floor = liquidity_floor_for_venue(ctx.venue)
        thinness = 0.0
        vol_known = ctx.vol_24h_usd is not None and floor.min_vol_24h_usd > 0.0
        if vol_known:
            thinness = 1.0 - _clamp(
                ctx.vol_24h_usd / floor.min_vol_24h_usd, 0.0, 1.0  # type: ignore[operator]
            )
        wideness = 0.0
        spread_known = ctx.spread_bps is not None and _SPREAD_FULL_BPS > 0.0
        if spread_known:
            wideness = _clamp(ctx.spread_bps / _SPREAD_FULL_BPS, 0.0, 1.0)  # type: ignore[operator]
        n_known = int(vol_known) + int(spread_known)
        if n_known == 0:
            return None  # venue has no floor/cap configured → ABSTAIN
        adverse = (thinness + wideness) / n_known
        if adverse <= 0.0:
            return None  # liquid + tight → neutral, never favorable
        return ProbeReading(
            probe_id=self.probe_id,
            kind=self.kind,
            lean=-_clamp(adverse, 0.0, 1.0),
            confidence=_clamp(n_known / 2.0, 0.0, 1.0),
            evidence={
                "vol_24h_usd": ctx.vol_24h_usd,
                "spread_bps": ctx.spread_bps,
                "min_vol_24h_usd": floor.min_vol_24h_usd,
            },
        )


class FundingProbe:
    """okx_funding mean perp funding → signed carry-cost pressure lean.

    P3 promotion (2026-07-16, vault/50_research/built-not-wired-audit.md) —
    ``okx_funding`` is collected (``OKXFundingCollector``, ``AltDataCache``)
    but the ONLY consumers are two ENTRY strategies (weekend_funding_
    capitulation_maker's per-symbol p10 squeeze / okx_funding_carry_persist's
    own carry entry); no position-monitor axis reads the funding a position is
    ALREADY paying/collecting. Sign convention (OKX ``fundingRate``, positive =
    longs pay shorts): a LONG paying positive funding is adverse; a SHORT
    collecting it is favorable, and symmetrically for negative funding.
    """

    probe_id: str = "funding"
    kind: ProbeKind = "funding"
    role: ProbeRole = "Position"  # SSOT: PROBE_ROLE_REGISTRY

    def evaluate(self, ctx: ProbeContext) -> ProbeReading | None:
        if ctx.funding_rate is None:
            return None  # feed absent/stale for this group → ABSTAIN
        # Cost paid BY this position's side: a long pays positive funding (cost);
        # a short is PAID positive funding (benefit) — invert for short.
        cost = ctx.funding_rate if ctx.side == "long" else -ctx.funding_rate
        if cost == 0.0:
            return None
        lean = -_clamp(cost / _FUNDING_FULL, -1.0, 1.0)
        return ProbeReading(
            probe_id=self.probe_id,
            kind=self.kind,
            lean=lean,
            confidence=_clamp(abs(cost) / _FUNDING_FULL, 0.0, 1.0),
            evidence={
                "funding_rate": round(ctx.funding_rate, 6),
                "side": ctx.side,
                "cost": round(cost, 6),
            },
        )
