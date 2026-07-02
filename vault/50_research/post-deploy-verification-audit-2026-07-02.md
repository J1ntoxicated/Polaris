---
type: research
status: active
date_created: 2026-07-02
tags: [audit, verification, reachability, sizing, exit, fixwave]
related: ["[[trade_mess_full_audit_2026-07-02_verdict]]", "[[ai-hooks-audit-verdict]]", "[[ADR-005-sizing-formula-cell-routing|ADR-005]]"]
---

# 배포 후 검증 감사 + Fix Wave C (2026-07-02 오후)

> Workflow `wf_780e55cf`(감사, 29 confirmed) → `wf_acf9db2c`(fix wave C, 9그룹 전부 APPROVE). 평결: **"Wave A 배선은 살아있으나 '한 경로 고치면 다른 경로가 샌다' 3곳 라이브 재발"** — 전부 당일 수정 배포.

## 대어 3 (P0, 당일 fix)
1. **무음 $5k 주문 클램프** — `max(10,min(x,5000))` 로그 0줄, 05-29부터 T4 사이징 표현력 통소거(cont 0.75=1.50 동일 수렴, tier 3× 무효). → 삭제 + venue-aware `notional_ceiling_pct`를 T4 headroom_min 단일 슬롯으로(binding= 구조화 로그) + schema ceiling 기본 무상한(env 수동 오버라이드만).
2. **부팅마다 refire** — anti-churn novelty anchor(`last_entry_by_key`)가 in-memory only → 재기동 소실 → 무조건 novel. 07:30 일일 재기동이 병리 트리거였음. → boot 시 positions에서 hydrate(closed 포함), 새 bar/side-flip 면제 보존(flow).
3. **momentum-drift 성숙도 우회** — 게이트가 corroborated만 커버, J225/AU200AU 48s 재발. + drift floor(0.0015)가 1m 캘리브 그대로 1D drift(15.5×)에 상시 관통. → drift 신호를 전략 timeframe bar로 측정(`timeframe_bar_rows`) + floor timeframe-스케일(`EXIT_THESIS_DRIFT_FLOOR_RATIO`) + **성숙도 게이트 양경로 통일**(5% of horizon — 21일 전략 ≈1일 발달 보장; -1.0R/trail/G6 crisis 별도 계층 불변).

## P1/P2 (전부 당일 fix)
- **세션레일**: us_equity 강제청산이 strategy-blind+주말맹 → 1D equity 전략이 성숙도 게이트에 구조적 도달 불가(보유 6.5h<18h floor). → hold_overnight 면제+weekday/holiday-aware.
- **recover.py**: Alpaca 부팅 reconcile 실패 근본 = event-loop cross-bind(`ex.submit(asyncio.run, coro)`) → async 전환.
- **T4 equity venue 정합**: Capital에 OKX $79k 상수 주입(1.55× 과대) → `equity_usd_for_venue`.
- **fee-floor × rung 기하 역전**: floor-bound(18×)에서 BEP arm 5.5ATR > trail 2ATR = 이익보호 사다리 dead → rung/trail 동일 R-unit 스케일 + floored mult positions 스탬프(관측성).
- **학습 폴드**: 셀 귀속을 close-시점 regime → entry_regime으로(6/14 오귀속) + pnl_r_net cost 항 단위 통일(Capital 스크래치 일률 −0.30R 핀 = 아티팩트였음).
- **tick 정합**: 가짜 horizon(max(held,60)) 제거·leverage venue-aware·캡 대칭. **tick 엔진 6일 decisions=0 RCA = 06-26 전략 KILL로 `_SIGNAL_FNS={}` 공로스터**(silent 공회전 — 시그널 재도출 or ENABLED 명시 OFF는 오픈).
- **shadow cost_r 단위**(per-unit→whole-position, GBPUSD 92.3R 파손) · **close_reason 계보**(stop_hit/EXIT_NOW/rotation 'exit' 뭉개짐 19건 해소).

## 발화 스코어카드 (Wave A 10픽스)
FIRING 5 (fee-floor bar·drift timeframe·venue-drift·NET표시·quote-ccy) / ARMED 4 (tick floor·reject-anchor·G7 payload·judge deadline — 이벤트 대기) / 신규병리化 1 (drift×flat-floor → 당일 재수정). G7 judge 첫 콜·reject-anchor 첫 행은 라이브 관찰 항목.

## 머지 중 잡은 교차 회귀 (오케스트레이터 수동)
브랜치 구base發: ①drift 브랜치가 A.1 corroborated 게이트 의미 역전 시도(모순 테스트 2건 삭제, SSOT 방향 유지) ②-6이 fee-floor kwarg 누락 호출 지참(스레딩 복원) ③-5가 adopt-by-default를 구식 gated-OFF로 되돌림(async fix만 채택) ④-8/-3 동일 픽스 이중 구현(엔진 소유로 단일화). **교훈: 병렬 worktree 빌드는 base 신선도가 생명 — 머지자가 의미 대조 의무.**

## 남은 큐
Wave B(/debate): R-budget 조준 사이징(42× 갭)·strength_scalar 산식·성숙도 frac 튜닝 · tick 시그널 로스터 재건 or 명시 OFF · counterfactual 리더·binance top_LS·REFINE_TIMING·market_events(이전 감사分).
