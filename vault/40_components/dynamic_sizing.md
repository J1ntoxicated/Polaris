---
pure: true
code_path: src/risk/dynamic_sizing.py
test_path: tests/risk/test_dynamic_sizing.py
created: 2026-05-04
phase: 2j
status: active
tags: [sizing, kelly, risk, pure, phase2j]
entity_type: component
entity_id: dynamic_sizing
expires: never
editable: true
last_modified: 2026-05-04
reviewed_by: codex
auto: false
mode: dev
back_links: ["[[performance_tracker]]", "[[regime_detector]]", "[[INSIGHT-032]]"]
---

# dynamic_sizing

Phase 2j: 모태 (auto_invasion_mk1) AI sizing 패턴 부활.

## 책임

Kelly criterion + confidence multiplier + regime multiplier + drawdown circuit breaker 조합으로 동적 포지션 사이즈를 계산하는 pure function.

## 핵심 함수

`compute_size(inputs: SizingInputs) -> SizingOutput`

### 파이프라인

1. **Kelly fraction** — recent_win_rate + avg_win_pct/avg_loss_pct 기반. cold start (<1e-9) 시 5% baseline.
2. **Confidence multiplier** — `signal.confidence²` (0.7→0.49, 0.9→0.81).
3. **Regime mult** — crisis=1.5, uptrend=1.0, flat=0.7, downtrend=0.3.
4. **Drawdown circuit breaker** — `max(0.2, 1 - dd*2)`. -25% dd → 0.5x.
5. **Hard cap** — `MAX_FRACTION = 0.20` (equity의 최대 20%).
6. **Min size skip** — size < `MIN_SIZE_USD (50.0)` → size=0, signal skip.

## 상수

| 상수 | 값 | 의미 |
|------|----|------|
| `MAX_FRACTION` | 0.20 | 현금 대비 최대 투입 비율 |
| `MIN_SIZE_USD` | 50.0 | 최소 진입 사이즈 (fee drag 방지) |
| `REGIME_MULT["crisis"]` | 1.5 | 북극성: fear = max bet |
| `REGIME_MULT["downtrend"]` | 0.3 | 하락장 70% 감소 |

## 효과 (Phase 2j)

- 기존: size $200 fixed
- 동적: crisis + high conf → $1000 (MAX_FRACTION cap); weak signal → $0 (skip)
- 같은 알파, 같은 fee → 최대 5x 시간당 수익

## P6 분류

Pure core — I/O 없음. Caller (realtime_runner shell)가 balance/regime 조회 후 전달.

## 관련

- [[performance_tracker]] — win_rate / avg_pct 공급
- [[regime_detector]] — regime 문자열 공급
- INSIGHT-032
- ADR-010 (risk caps)
