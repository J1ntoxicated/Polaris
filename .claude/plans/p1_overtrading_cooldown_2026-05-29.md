# P1 — Over-trading 차단 (fee churn 87% 원인) 2026-05-29

> 포렌식([[2026-05-29_loss_forensic_fee_overtrading]]): net -$1.35K 중 87%가 fee. 5초 tick마다 같은 (venue,symbol,strategy) 재진입(SOL 20연속 buy) → notional 부풀려 fee 폭주. 가격엣지는 ≈break-even. DEMO/PAPER, **aggressive 유지**(중복주문 제거 = turnover 비용 관리, P&L halt 아님 — [[feedback_circuit_breaker_philosophy]] 일치).

## 진단 (확정)
- `_production_tick.py::_run_tick` 진입 루프: 포커스 심볼×전략마다 매 tick `run_pipeline_for_signal`→게이트 통과 시 `reserve_and_submit` open. **이미 같은 심볼 open 중인지 가드 없음.**
- 단, `positions.row_count`/`conviction` live_recalc 존재 → 의도적 scale-in 과 틱-스팸 churn 구분 필요. 단순 "open이면 무조건 skip"은 conviction-stacking 의도와 충돌 가능.

## 설계 옵션 (— /debate 대상: 트레이딩 파라미터/아키텍처 변경)
1. **재진입 쿨다운 N-bars/seconds** (권장, 리서치 #4 cooldown 수렴): 같은 (venue,symbol,strategy) 마지막 open 이후 min_reentry_sec(예: 1 bar 또는 300s) 이내 재진입 skip. 강신호(tier amplifier 큼)는 면제 → flow 보존.
2. **open-while-open 단일화**: 같은 심볼 open 포지션 있으면 신규 open skip, conviction은 기존 포지션 size 조정으로만.
3. **conviction-aware**: conviction layer가 명시적으로 add 결정한 경우만 추가 open 허용, 그 외 skip.

## 보조 레버
- maker/limit 실행(리서치 #2)로 fee 0.06→0.02% + 슬리피지 절감 (별도, 병행 효과 큼).
- OKX demo fee tier 70bps 비현실 — tier 조정 가능 여부 확인.

## 실행 (승인 후)
- [ ] /debate 로 쿨다운 vs 단일화 vs conviction-aware 교차검증 (트레이딩 파라미터).
- [ ] TDD: 같은 심볼 연속 tick 재진입이 skip 되고, 강신호/명시 conviction은 통과하는 테스트.
- [ ] 구현 위치: `_production_tick.py` 진입 루프 또는 `reserve_and_submit` 직전 가드 + state.reentry_skips 텔레메트리.
- [ ] fresh-Claude/codex review.
- [ ] **봇 재시작 결정**: 적용은 봇 restart 필요(현 PID 96290은 기동시 코드 로드). 현 수집은 fee-churn 노이즈라 클린 재시작이 더 나은 데이터 → Jin 승인 후 restart.

## 영향
fee의 87% churn 차단 시 같은 엣지로 net 크게 개선(추정 net -$1.35K → fee 급감). 거래 기회 자체는 보존(쿨다운/강신호 면제).
