---
pure: true
code_path: src/paper/exit_profiles.py
test_path: tests/paper/test_exit_profiles.py
created: 2026-05-04
phase: 2P
entity_type: component
entity_id: exit_profiles
expires: never
editable: true
last_modified: 2026-05-04
reviewed_by: codex
auto: false
mode: dev
back_links: ["[[INSIGHT-021]]", "[[realtime_runner]]"]
---

# exit_profiles — Strategy Timeframe Exit Profile System

## 목적

`TP_PCT_INTRADAY = 0.006` (0.6%) single-tier 구조를 timeframe별 4-tier로 교체.

HYPO-032 TSMOM (30d momentum backtest exp +5-15%/trade)에 0.6% TP 강제 시
99% 알파 잠식 → position profile TP 12% 복원.

## EXIT_PROFILES

| tier        | TP    | SL    | max_hold | 적용 HYPO                        |
|-------------|-------|-------|----------|----------------------------------|
| scalp       | 0.6%  | 0.35% | 4h       | 007-RT, 024, 028, 033, 034       |
| swing       | 5%    | 2%    | 7d       | 008-RT, 027                      |
| position    | 12%   | 4%    | 30d      | 032 (TSMOM)                      |
| liquidation | 1.5%  | 0.7%  | 30min    | 023 (LiquidationCascade)         |

## API

```python
from src.paper.exit_profiles import get_exit_profile

profile = get_exit_profile(hypo)  # reads hypo["exit_profile"], default="scalp"
tp  = profile["tp_pct"]
sl  = profile["sl_pct"]
max_ms = int(profile["max_hold_h"] * 3600 * 1000)
```

## Pure 함수 속성

- I/O 없음 — dict lookup only
- KeyError 발생: 알 수 없는 tier (silent default 금지)
- default "scalp" — 기존 동작 완전 호환

## 변경 파일

- `src/paper/exit_profiles.py` — 신규 (pure module)
- `src/paper/realtime_runner.py` — import + REALTIME_HYPOS exit_profile 키 + `_eval_and_act` 수정
- `tests/paper/test_exit_profiles.py` — 25 tests (TDD, 전부 pass)

## 즉시 효과 (예상)

- HYPO-032 TSMOM: TP 0.6% → 12% (20x 알파 회복)
- HYPO-008-RT VolumeBurst 1H: TP 0.6% → 5% (8x)
- HYPO-027 FundingCarry: TP 0.6% → 5% (8x)
- HYPO-023 LiqCascade: TP 0.6% → 1.5% (2.5x), max 4h → 30min (더 빠른 exit)

## Links

[[ADR-015]] [[HYPO-032]] [[INSIGHT-032]]
