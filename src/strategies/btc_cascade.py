"""BTC-Led Alt Cascade — pure (P6).

HYPOTHESIS-017 (Codex Round 11 + INSIGHT-027 forensic):
BTC 매크로 파동 동조성 활용. BTC가 막 상승하는 1min 순간 → 알트 follow lag 활용.

Logic (`evaluate_cascade(alt_tick, btc_state, eth_state, now_ms)`):
- btc_1min_delta > +0.30% (BTC 가격이 지금 막 움직임)
- AND eth_1min_delta > +0.10% (ETH 동방향 confirmation — false positive 차단)
- AND alt_24h_change < +0.50% (alt가 이미 강하지 않음 — HYPO-010 orthogonality guard)
- AND alt_24h_change > -2.0% (alt 약세 차단 — cascade 가설 violated)
- AND btc_state/eth_state not stale (< 30s)
- → ENTER_LONG

Exit:
- btc_1min_delta <= -0.20% (BTC reversal) → EXIT
  (EXIT checked before entry — BTC 하락 시 진입 차단 포함)

Hysteresis deadzone: -0.20% ~ +0.30%
- BTC delta in deadzone → HOLD (no entry, no exit)
- flip-flop 방지

Risk guards:
- btc/eth state stale (now_ms - ts_ms >= 30_000ms) → HOLD
- alt_24h_change >= +0.50% → HOLD (HYPO-010 territory)
- alt_24h_change <= -2.0% → HOLD (alt 약세, cascade 가설 violated)

Orthogonality (INSIGHT-027):
- HYPO-010 TickMomentum: 24h 단일 ticker slow signal
- HYPO-017 BTC Cascade: 1min cross-ticker fast signal
- Overlap 11.1% raw, causal cascade ~0% → orthogonal by design
- alt_24h >= +0.5% guard: HYPO-010 trigger zone → skip cascade entry
"""
from __future__ import annotations

from src.domain.candle import Candle
from src.domain.signal import Signal, SignalAction
from src.domain.strategy import Strategy

# Thresholds — Codex Round 11 권고 + forensic INSIGHT-027 보강
# entry: BTC delta > +0.30% (1min rapid move)
# exit: BTC delta <= -0.20% (reversal begins)
# deadzone: -0.20% ~ +0.30% — noise / mean-revert zone (flip-flop 방지)
DEFAULT_BTC_DELTA_BUY: float = 0.0030      # +0.30% BTC 1min delta
DEFAULT_BTC_DELTA_EXIT: float = -0.0020    # -0.20% BTC 1min delta (reversal)
DEFAULT_ETH_DELTA_CONFIRM: float = 0.0010  # +0.10% ETH 1min delta (confirmation)
DEFAULT_ALT_MAX_24H: float = 0.005         # +0.50% (HYPO-010 orthogonality boundary)
DEFAULT_ALT_MIN_24H: float = -0.020        # -2.00% (alt 약세 차단)
DEFAULT_TARGET_SIZE_USD: float = 100.0
DEFAULT_STALE_MS: int = 30_000             # 30s staleness threshold


def _compute_1min_delta(state: dict) -> float:
    """1min price delta from state dict.

    Args:
        state: {"price_now": float, "price_1min_ago": float, "ts_ms": int}

    Returns:
        (price_now - price_1min_ago) / price_1min_ago as fractional delta.
        Returns 0.0 if price_1min_ago is zero.

    Pure function — no I/O, deterministic.
    """
    price_now = float(state.get("price_now", 0) or 0)
    price_1min_ago = float(state.get("price_1min_ago", 0) or 0)
    if price_1min_ago == 0.0:
        return 0.0
    return (price_now - price_1min_ago) / price_1min_ago


def _is_stale(state: dict, now_ms: int, stale_threshold_ms: int) -> bool:
    """Check if state is stale (last update older than threshold).

    Args:
        state: {"ts_ms": int} — timestamp of last update.
        now_ms: current time in milliseconds.
        stale_threshold_ms: maximum freshness window in ms.

    Returns:
        True if stale (now_ms - ts_ms >= stale_threshold_ms).

    Pure function — no I/O, deterministic.
    """
    ts_ms = int(state.get("ts_ms", 0) or 0)
    return (now_ms - ts_ms) >= stale_threshold_ms


class BTCCascade(Strategy):
    """BTC-Led Alt Cascade — pure (P6).

    HYPOTHESIS-017: BTC macro wave synchronization.
    BTC 1min surge → alt follow lag entry.
    ETH confirmation guards against BTC-specific false positives.
    HYPO-010 orthogonality guard (alt_24h < 0.5%) prevents overlap.
    """

    name = "btc_cascade"
    min_window = 1

    def __init__(
        self,
        btc_delta_buy: float = DEFAULT_BTC_DELTA_BUY,
        btc_delta_exit: float = DEFAULT_BTC_DELTA_EXIT,
        eth_delta_confirm: float = DEFAULT_ETH_DELTA_CONFIRM,
        alt_max_24h: float = DEFAULT_ALT_MAX_24H,
        alt_min_24h: float = DEFAULT_ALT_MIN_24H,
        target_size_usd: float = DEFAULT_TARGET_SIZE_USD,
        stale_ms: int = DEFAULT_STALE_MS,
    ) -> None:
        self.btc_delta_buy = btc_delta_buy
        self.btc_delta_exit = btc_delta_exit
        self.eth_delta_confirm = eth_delta_confirm
        self.alt_max_24h = alt_max_24h
        self.alt_min_24h = alt_min_24h
        self.target_size_usd = target_size_usd
        self.stale_ms = stale_ms

    def evaluate(self, window: list[Candle]) -> Signal:
        """Strategy ABC stub — BTCCascade uses evaluate_cascade, not candles."""
        ts = window[-1].timestamp_ms if window else 1
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason="BTC cascade requires alt+btc+eth state — use evaluate_cascade",
        )

    def evaluate_cascade(
        self,
        alt_tick: dict,
        btc_state: dict,
        eth_state: dict,
        now_ms: int,
    ) -> Signal:
        """Evaluate BTC-led alt cascade signal.

        Args:
            alt_tick: OKX alt ticker payload with 'last', 'open24h', 'ts' fields.
            btc_state: {"price_now": float, "price_1min_ago": float, "ts_ms": int}
            eth_state: same shape as btc_state.
            now_ms: current time in milliseconds (caller provides for determinism).

        Returns:
            Signal — ENTER_LONG / EXIT / HOLD.

        Pure function — no I/O, deterministic.
        """
        ts = int(alt_tick.get("ts", 0) or 0) or 1
        last = float(alt_tick.get("last", 0) or 0)
        open24h = float(alt_tick.get("open24h", 0) or 0)

        # Guard 1: staleness — stale BTC/ETH state = unreliable cascade signal
        if _is_stale(btc_state, now_ms, self.stale_ms):
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"btc_state stale (>= {self.stale_ms}ms)",
            )
        if _is_stale(eth_state, now_ms, self.stale_ms):
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=f"eth_state stale (>= {self.stale_ms}ms)",
            )

        btc_delta = _compute_1min_delta(btc_state)
        eth_delta = _compute_1min_delta(eth_state)

        # Exit: BTC reversal signal — checked before entry to block cascade entry on reversal
        if btc_delta <= self.btc_delta_exit:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.EXIT,
                confidence=0.65,
                reason=f"btc_1min_delta={btc_delta*100:+.3f}% <= {self.btc_delta_exit*100:+.3f}% (BTC reversal)",
            )

        # Alt 24h change — guard computation
        alt_24h = 0.0
        if open24h > 0 and last > 0:
            alt_24h = (last - open24h) / open24h

        # Guard 2: alt already strong → HYPO-010 territory, skip cascade
        if alt_24h >= self.alt_max_24h:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=(
                    f"alt_24h={alt_24h*100:+.3f}% >= {self.alt_max_24h*100:.1f}% "
                    f"(HYPO-010 territory — orthogonality guard)"
                ),
            )

        # Guard 3: alt in freefall → cascade hypothesis violated
        if alt_24h <= self.alt_min_24h:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=(
                    f"alt_24h={alt_24h*100:+.3f}% <= {self.alt_min_24h*100:.1f}% "
                    f"(alt 약세 — cascade violated)"
                ),
            )

        # Entry: BTC strong + ETH confirmation + alt has room to follow
        if btc_delta >= self.btc_delta_buy and eth_delta >= self.eth_delta_confirm:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.ENTER_LONG,
                confidence=0.70,
                target_size_usd=self.target_size_usd,
                reason=(
                    f"cascade entry: btc_delta={btc_delta*100:+.3f}% "
                    f"eth_delta={eth_delta*100:+.3f}% "
                    f"alt_24h={alt_24h*100:+.3f}%"
                ),
            )

        # Deadzone or ETH not confirming → HOLD
        if btc_delta >= self.btc_delta_buy and eth_delta < self.eth_delta_confirm:
            return Signal(
                timestamp_ms=ts,
                action=SignalAction.HOLD,
                confidence=0.0,
                reason=(
                    f"btc strong but eth not confirming: "
                    f"btc_delta={btc_delta*100:+.3f}% eth_delta={eth_delta*100:+.3f}%"
                ),
            )

        # BTC in deadzone (between exit and entry thresholds)
        return Signal(
            timestamp_ms=ts,
            action=SignalAction.HOLD,
            confidence=0.0,
            reason=(
                f"deadzone: btc_delta={btc_delta*100:+.3f}% "
                f"(deadzone [{self.btc_delta_exit*100:.2f}%, {self.btc_delta_buy*100:.2f}%))"
            ),
        )
