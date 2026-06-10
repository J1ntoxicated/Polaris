---
type: digest
status: active
date_created: 2026-05-29
date_updated: 2026-05-29
tags: [digest, forensic, loss, fees, over-trading]
---

# 2026-05-29 손실 포렌식 — fee 폭주 × 과매매 (대시보드 표시버그 동반)

## 평결
Jin "로스 심하다, 이게 맞냐" → 조사 결과: 대시보드 **-$145는 가짜**(fee 미반영 gross). **실제 net = -$1.35K~-$1.5K (-1.0~1.16%, 14.5h)**. 손실의 **87%가 수수료($1.2K)** — 5초 tick마다 같은 종목 재진입하는 **과매매 × OKX demo 70bps**. 전략 가격엣지 자체는 ≈break-even(-$145) → "전략이 망가진 게 아님". cold-start 표본부족(n=60) + 구조버그 2건이 겹친 케이스.

## 숫자 (data/polaris_live.sqlite)
- 14.5h, 143 fills(OKX 129/CAP 14), 60 청산. gross close pnl -$146.92, **total fee $1,206**, net **-$1,353**.
- OKX fee 균일 **70.0bps**(demo 실제 tier, 코드버그 아님). 승률 32.8%(n 적채).
- SOL-USDT 38 opens/29 closes = fee $552 vs 가격엣지 +$40. 거래빈도 9.4 fills/min. opens 86 vs closes 69 → 16 미청산(LTC orphan 포함).
- pnl_usd 수동검산 정확(계산오류 X), 사이징 정상(~$1.2K=0.9%).

## 버그
- **P0 (수정완료 `6d77b5e`)**: 대시보드가 `starting_capital + SUM(pnl_usd)` 만, **fee_usd 미차감** (`snapshot_queries.py` `_build_equity_curve`/`_daily_realised_pnl`). → net 차감으로 수정, 68 test green. 라이브 대시보드 이제 -$1.5K 정직 표시.
- **P1 (미수정)**: 과매매 — 같은 (strategy, instrument) 미청산 상태에서 매 tick 재진입(SOL 20연속 buy). per-symbol open-중 재진입 skip/쿨다운 부재. 추정 위치 `_production_pipeline.py` 진입 게이트 + 사이징 전 dedup. **aggressive 위반 아님**(중복주문 제거 = turnover 비용 관리, P&L halt 아님 — `feedback_circuit_breaker_philosophy` 일치).

## 개선 레버 (aggressive 유지)
1. ✅ 대시보드 fee 반영 (완료).
2. ⏳ 동일심볼 open-중 재진입 skip → fee 87% churn 차단, 엣지 보존. (P1, 다음)
3. OKX demo fee tier 확인(70bps 비현실적; 10bps 가정 시 net -$312).
→ 리서치 백로그와 수렴: [[2026-05-29_profit_structure_backlog]] (universe 확장 + maker/limit + cooldown + vol-scaling).

## 후속
- P1 over-trading dedup TDD + review + commit → 봇 재시작 결정(클린 수집 위해).
- 봇 PID 96290 / 대시보드 PID 7638 / viz :8770 가동 유지.
