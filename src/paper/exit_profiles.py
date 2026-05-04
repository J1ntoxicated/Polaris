"""Strategy timeframe별 exit profile — pure module (P6).

Tier 정의:
- scalp:       TP 0.6%  / SL 0.35% / max 4h     (15m intraday strategies — 기존 default)
- swing:       TP 5%    / SL 2%    / max 7d      (1H/1D candle strategies — volume/funding)
- position:    TP 12%   / SL 4%    / max 30d     (multi-day momentum — TSMOM, CSMOM)
- liquidation: TP 1.5%  / SL 0.7%  / max 30min   (event-driven mean revert — LiqCascade)

Usage:
    from src.paper.exit_profiles import get_exit_profile

    profile = get_exit_profile(hypo)
    tp = profile["tp_pct"]    # e.g. 0.05
    sl = profile["sl_pct"]    # e.g. 0.02
    max_ms = profile["max_hold_h"] * 3600 * 1000

HYPO 설정 예:
    {
        "hypo_id": "HYPO-032",
        "exit_profile": "position",  # ← 이 키로 profile 선택
        ...
    }

    exit_profile 키 미존재 시 "scalp" (기존 동작 보존).

Pure: no I/O, deterministic, no side effects.
"""
from __future__ import annotations

EXIT_PROFILES: dict[str, dict] = {
    "scalp": {
        "tp_pct": 0.006,    # 0.6%
        "sl_pct": 0.0035,   # 0.35%
        "max_hold_h": 4,    # 4 hours
    },
    "swing": {
        "tp_pct": 0.05,     # 5%
        "sl_pct": 0.02,     # 2%
        "max_hold_h": 168,  # 7 days
    },
    "position": {
        "tp_pct": 0.12,     # 12%
        "sl_pct": 0.04,     # 4%
        "max_hold_h": 720,  # 30 days
    },
    "liquidation": {
        "tp_pct": 0.015,    # 1.5%
        "sl_pct": 0.007,    # 0.7%
        "max_hold_h": 0.5,  # 30 minutes
    },
}


def get_exit_profile(hypo: dict) -> dict:
    """hypo config에서 exit profile dict 반환.

    Args:
        hypo: REALTIME_HYPOS / ACTIVE_HYPOS 항목 dict.
              "exit_profile" 키가 있으면 해당 tier 사용.
              없으면 "scalp" (기존 default 동작 보존).

    Returns:
        dict with keys: tp_pct, sl_pct, max_hold_h.

    Raises:
        KeyError: exit_profile 값이 EXIT_PROFILES에 없는 경우 (silent default 금지).

    Pure function — no I/O.
    """
    profile_name = hypo.get("exit_profile", "scalp")
    return EXIT_PROFILES[profile_name]  # KeyError if unknown tier
