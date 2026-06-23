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
"""

from __future__ import annotations

from polaris.core.probes import ProbeContext, ProbeKind, ProbeReading, _clamp

__all__ = [
    "LossDefenseProbe",
    "ProfitTakingProbe",
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


class ProfitTakingProbe:
    """Giveback (mfe_r − pnl_r) → harvest-pressure adverse lean (the top lever)."""

    probe_id: str = "profit_taking"
    kind: ProbeKind = "profit"

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
