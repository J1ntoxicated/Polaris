---
type: research
status: active
date_created: 2026-07-03
tags: [debate, codex, ladder, profit-sweep, sizing, alpaca, ledger]
related: ["[[ADR-005-sizing-formula-cell-routing|ADR-005]]", "[[layer-3-sizing-risk]]", "[[project_validated_edge_is_slow_trend_not_scalp]]"]
---

# Profit-Sweep 3단 사다리 디베이트 (2026-07-03)

> codex CLI(gpt-5.5, effort=xhigh) 3라운드, 합의 조기 종료. R2 = fresh-세션 적대검증. DEMO/PAPER 명시, 거부 키워드 0건. 안건 출처: venue 활동 부활 조사(report.ladder_design).

## 라운드 요약
- **R1** (초안 제시): ①MODIFY→50%(sweep은 회계 이동이라 X는 1단 복리를 안 깎음 — 30/40은 불필요하게 느림) ②AGREE 청산 즉시 ③AGREE auto-draw + reservation 모델 ④AGREE 불가침(손실 debit = base equity 반영과 이중 감산). 신규쟁점 3: USD 정규화 / 멱등 키 / credited·reserved·available 분리.
- **R2** (fresh 적대): BREAK 3 — 집계형 예약상태는 crash/replay 복구 불가 · 청산 핫패스 same-tx SQLite write = hidden blocker · stale BP+pending-blind cap → 동시신호 dead order. DEGRADE 3 — per-fill gross sweep은 net-PnL 의미 표류 · X는 risk param 아닌 unlock-rate 노브(장식화 위험) · Tier-2는 현재 명목층(attribution 모호).
- **R3** (해소안 검증): CONFIRM 6 + BREAK 1(RELEASE 술어에 live/pending 주문 생존 체크 추가 — 픽스 수용) → 잔여 이견 0.

## 쟁점별 합의
| 쟁점 | 합의 | 값 |
|---|---|---|
| ① X% | 50% — X=3단 headroom unlock-rate 노브(재정의). binding-cap 텔레메트리로 반증 가능하게, N≥50 사이징에서 ledger cap이 한 번도 binding 아니면 재디베이트 | `sweep_pct=0.50` |
| ② 트리거 | 포지션 최종 청산 시점(=즉시), 단 credit은 파생 프로젝션 — 3단 draw 시점 on-demand + ~5분 sweeper가 materialize. 핫패스 신규 write 0 | `on_close, projection` |
| ③ 인출 | threshold=hidden blocker 기각 → 3단 신호마다 auto-draw. BEGIN IMMEDIATE 직렬 tx, pending-aware cap, fresh BP clamp | `threshold=0` |
| ④ 손실 | 불가침 CONFIRM — debit은 base equity 하락과 이중 감산(feedback dampener). RELEASE는 open draw 해제만, credit 생성 금지 | `loss_debit=0` |

## 유효 반박 (설계 반영)
1. credit 기준 = per-fill gross → **per-position net**(부분청산 내부 상계; +100/-150 → credit 0). 포지션 간 비대칭은 의도적 유지.
2. ledger = **append-only 이벤트 행**(CREDIT source_position_id unique / DRAW / RELEASE draw_id unique), 잔고=SUM 파생. 집계 컬럼 상태 없음.
3. RELEASE 술어 = open position 부재 **AND live/pending 주문 부재**(R3 BREAK — pending fill replay로 bucket 이중 사용 방지).
4. 핫패스 결합 절단: credit은 closed positions 스캔 프로젝션(checkpoint는 insert 성공 후만 전진) — [[feedback_db_lock_is_architecture_signal]] 정합.
5. cap 산식: `cap_effective = cap_base + draw_used`, `draw_used = max(0, min(bucket_available, needed, fresh_BP−pending))` — 기존 단일 min() 내부, 신규 multiplier 0 (9-stack 무결).
6. Tier-2 스코프 분리: sweep 배관은 1단→3단만. 2단 = 기존 T4 amplifier/judge SIZE_UP + 전략 공급(부활 플랜 P0/P1), ledger 무접촉. attribution = 3단 주문에 draw_id 기록 + ladder_tier 태그 + 중간단(1h-1d) 체결비중 메트릭 독립 측정.

## 빌드 스펙 (1줄씩)
1. `ladder_ledger` 테이블: append-only CREDIT/DRAW/RELEASE 이벤트 행 + unique 제약(source_position_id, draw_id) + USD 정규화(청산시점 FX).
2. credit 프로젝션 materializer: closed-position net PnL>0 × 0.50, on-demand(3단 draw 전) + 5분 sweeper 폴백, checkpoint 후진 금지.
3. draw 경로: G5 3단 사이징에서 BEGIN IMMEDIATE tx로 available 판독+DRAW insert, cap_base+draw를 기존 min()에 투입.
4. RELEASE reconcile: 재기동 시 open position·live 주문 모두 부재인 DRAW만 해제(멱등), reject/cancel/expiry 경로도 RELEASE.
5. 텔레메트리: 3단 사이징마다 binding cap term + unlocked notional 로그 → X 장식화 여부 데이터 판정.
