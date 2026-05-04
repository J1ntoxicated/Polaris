---
entity_type: insight
entity_id: INSIGHT-025
auto: false
last_modified: 2026-05-04
expires: 2026-08-04
editable: true
back_links: ["[[INSIGHT-007]]", "[[INSIGHT-019]]", "[[INSIGHT-024]]"]
mode: forensic
reviewed_by: codex
maturity: authoritative
authoritative_basis: Codex Round 9 8% gap 요청 → forensic audit 직접 코드 검증 (runner.py 3건 + metrics.py 1건 + realtime_runner.py 주석 1건). HYPO-008/010 net_usd PnL = TRUE NET 확인.
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-025 — fee 0.014 Latent Bug 4건 (Forensic Audit, Round 9 8% gap)

## 발견 경위

**Codex Round 9 8% gap**: "break-even 184% (fee 1.4% 가정이면)" — HYPO-008/010 양수 PnL이
실제 fee를 반영한 진짜 NET인지 의심. forensic audit 의뢰.

## Forensic 결과: HYPO-008/010 PnL = TRUE NET

realtime trading 경로 검증:

| 경로 | fee 적용값 | 검증 |
|---|---|---|
| `realtime_runner.py` position close | `LIVE_FEE_ROUND_TRIP` 상수 직접 참조 | 정확 |
| `realtime_runner.py` net_usd 계산 | `gross - fee_cost` (LIVE_FEE_ROUND_TRIP 기반) | 정확 |
| paper_log_*.md 기록값 | net_usd = TRUE net (fee 0.0014 적용 후) | 확인 |

**HYPO-008 VolumeBurst + HYPO-010 TickMomentum 양수 PnL = 실제 fee 0.0014 차감 후 순수익.**
Codex 8% gap 의문 해소 — HYPO-008/010 size 결정 보류 해제.

## Latent Bug 4건 (즉시 fix 필요)

| # | 파일 | 위치 | 버그 내용 | Impact |
|---|---|---|---|---|
| 1 | `src/paper/runner.py` | line 59 | fallback fee `0.014` (hardcoded) | daily cron 오류 위험 |
| 2 | `src/paper/runner.py` | line 74 | fallback fee `0.014` (hardcoded) | daily cron 오류 위험 |
| 3 | `src/paper/runner.py` | line 136 | `run_cycle` default `fee=0.014` | backtest 결과 오염 위험 |
| 4 | `src/paper/metrics.py` | line 15 | `DEFAULT_FEE_ROUND_TRIP = 0.014` | 모든 metrics 집계 오류 위험 |

추가 (주석 오탈자):

| # | 파일 | 위치 | 버그 내용 |
|---|---|---|---|
| 5 | `src/paper/realtime_runner.py` | line 85 | 주석 오탈자 (`LIVE_FEE_ROUND_TRIP` 설명 부정확) |

## Impact 분석

| 영역 | 영향 | 설명 |
|---|---|---|
| realtime trading | **영향 없음** | `LIVE_FEE_ROUND_TRIP` 상수 직접 참조 — fallback 미사용 |
| daily cron backtest | **오류 위험** | runner.py fallback 0.014 = 10× 과대 fee → 전략 음의 EV 판정 오류 |
| metrics 집계 | **오류 위험** | DEFAULT_FEE_ROUND_TRIP 0.014 → Sharpe/EV 계산 오염 |
| 과거 backtest 결과 | **재실행 필요** | 0.014 적용 결과는 신뢰 불가 (HYPO-003/004 walk-forward 포함) |

## 권고 조치

1. **즉시 fix** (code-implementer 위임): 4건 `0.014` → `0.0014` 교체 + 주석 정정
2. **fix 후 backtest 재실행**: HYPO-003/004 walk-forward 결과 재검증 (EV 방향은 동일 예상, 수치 변화 확인)
3. **상수 단일화**: `LIVE_FEE_ROUND_TRIP` 를 모든 경로에서 단일 소스로 사용 (fallback 제거)

## 연결

- [[INSIGHT-007]] OKX SPOT fee 함정 — 0.7% scalp 불가 원본 발견 (fee 정확성의 중요성 배경)
- [[INSIGHT-019]] Codex Round 3 Phase 2c~e 4 CRITICAL fix — legacy 0.014 첫 fix (realtime 경로)
- [[INSIGHT-024]] HYPO-009 deprecate — Round 9 8% gap 원인 (이 forensic의 트리거)
