---
type: debate
topic: position-lifecycle-fix
status: round-3-done
date_created: 2026-05-10
date_updated: 2026-05-10
forensic: 2026-05-10_position_lifecycle_drift.md
tags: [debate, lifecycle, dedupe, codex]
---

# Topic A debate — Position lifecycle fix

## Round 1 — codex-rescue 권고
**A1 (position_id key)**: **A1-β** `pos_{venue}_{symbol}_{strategy}_{side}_{epoch}` (close 시 epoch++)
**A2 (build_order_key)**: **A2-γ** `position_key=strat:venue:sym:side` 영속 dedupe + `intent_id=position_key:signal_ts` traceability 분리
**A3 (same-key OPEN policy)**: **A3-γ** hybrid — same strategy ⇒ scale-in, different strategy ⇒ swap. reject 경로 0 (M2 준수)
**A4 (startup GC)**: **A4-γ** `polaris/core/lifecycle/recover.py` 신규 모듈, ignite_p1 + production_paper_loop 양쪽 호출

## Cross-cutting risks (codex 식별)
- A1-β + A3-γ scale-in: epoch는 close 이벤트에서만 increment, scale-in 은 qty 만 변경 (규약 명시 필수)
- A2-γ + AllocatorFence: position_key 에 strategy_id 포함 — swap 신호 잘못 dedupe 방지
- A3-γ scale-in + M4 (9-stack): scale-in qty 가 L3 sizing chain 우회 시 multiplier 무한누적. **반드시 L3 compute_size → clip → cell mult 통과 후 적용**

## Anti-pattern verdict (codex)
| Mandate | 결과 |
|---|---|
| M1 (entry rate ≥ status quo) | PASS — drop 없음 |
| M2 (flow_not_block) | PASS — reject path 0 |
| M3 (no quick patch) | PASS — 구조 결정 |
| **M4 (9-stack ban)** | **CONDITIONAL** — scale-in 이 L3 chain 우회 안 한다는 구현 보증 필요 |
| M5 (asymmetric payoff) | PASS — conviction 방향 누적 |

## Round 2 검증 포인트 (codex 자체 제기)
- (R2-a) **positions_history 별도 테이블 vs epoch-in-same-table** tradeoff. epoch 방식은 history 쿼리 복잡도 증가
- (R2-b) **A4-γ vs A4-δ 보완관계** — recover.py = GC, SQLite view = read-only "logical position" materialization. 경쟁 아닌 dual layer
- (R2-c) **M4 scale-in L3 우회 검증** — `compute_size` 통과 보증 구체 구현 디자인
- (R2-d) **epoch increment race** — `MAX(epoch)+1` SELECT + INSERT 단일 트랜잭션 보증
- (R2-e) **recover.py stale 기준** — `fill_count=0 AND created_at<now-grace` 복합 조건, grace 외부 설정

## Round 2 — codex 권고 (실제 코드 read 후 수정)

| | R1 | **R2 최종** | 이유 |
|---|---|---|---|
| A1 | β (epoch) | **α** (deterministic PK `pos_{venue}_{sym}_{strat}_{side}`) | `strategy_swap.py` 가 `position_id` WHERE 로 row-in-place UPDATE — epoch 가변 시 swap path 파괴 |
| A2 | γ | **γ 유지** | `position_key=strat:venue:sym:side` (영속) + `intent_id=position_key:signal_ts` (traceability) |
| A3 | γ | **γ + fallback 명확화** | swap cap 도달 시 scale-in fallback. reject path 0 (M2) |
| A4 | γ | **γ + δ dual + stale 기준 전면 교체** | recover.py = session hydrate (GC 아님). v_open_positions view = dashboard read 분리 |

## R2 핵심 발견 (DB 실측 + 코드)
- **R1 stale 기준 (`fill_count=0`) 완전 무효**. `_production_pipeline.py:210` 가 INSERT 와 같은 트랜잭션 내 `persist_fill` → 모든 OPEN row fill_count=1. (DB 실측 2029/2029 확인.)
- **stale GC ≠ session hydrate**. paper loop 재시작 시 in-memory `state.open_trades` 빈 상태 → DB OPEN row 를 `SimulatedTrade` 로 복원하면 close path 정상화. "버리기" 가 아닌 "살려서 계속" — `flow_not_block` 정합.
- **A1-α + `positions_history` 별도 테이블**. close 시 history INSERT + `UPDATE status='closed'`. position_id 영속, swap path 보존.
- **scale-in M4 invariant**: `compute_size` 의 `per_symbol_remaining` 이 `existing_qty.open_risk_pct` 를 이미 차감하므로 delta 가 자동 clip. T4 통과 보증만 하면 9-stack collapse 무위험. **단** `build_sizer_payload` 가 `position_risk_state` 에서 `open_risk_pct` 정확 로드하는지 R3 검증 필요.

## 구현 PR 순서 (R2 권고)
- **PR1 긴급** — `polaris/core/lifecycle/recover.py` session hydrate (ignite_p1 + production_paper_loop 양쪽 호출)
- **PR2** — schema `positions_history` DDL + `_production_pipeline.py:183` position_id 결정적 key + 기존 2029 OPEN rows migration
- **PR3** — `order_keys.py` `build_position_key()` 분리 + AllocatorFence dedupe target 전환
- **PR4** — `_production_pipeline.py` `handle_same_key_open()` 함수 (A3-γ scale-in / swap 분기)
- **PR5** — `v_open_positions` view + `snapshot.py:415` query target 전환

## Round 3 / Jin sign-off 항목
- **R3 필요** (코드 미확인): `build_sizer_payload` 의 `open_risk_pct` 로딩 정확성 — M4 invariant 의 핵심 전제
- **R3 필요**: 기존 2029 OPEN row migration SQL 검증 (logical key 당 latest 1개만 live, 나머지 history 이전)
- **Jin sign-off**: `gate_events.position_id` FK 가 PR2 후 history 와 join 으로 변경되어야 함 — dashboard 영향 범위

## Round 3 — 코드 read 후 finalize

### 🚨 M4 invariant 이미 깨져있음 (R3-a critical)
`payload_builder.py:357 _read_portfolio_state()` 가 `position_risk_state` SELECT — 그러나 **DB 실측 0 rows**. 코드베이스 어디서도 INSERT/UPDATE 안 함. vault `2026-05-07_p0_sprint_cumulative_review.md:40` 에 이미 "hard caps non-binding in prod" 기록됨. T4 `per_symbol_remaining` 항상 cap 풀로 반환 — **현재 cap 자체가 non-binding**.
→ PR4 `handle_same_key_open` 에서 `dataclasses.replace` 로 existing position 의 `open_risk_pct` 를 `PortfolioState.open_positions` 에 수동 주입. PR4 단위 테스트로 invariant 검증 (existing 0.45 주입 → final ≤ 0.05).

### Migration SQL (R3-b 순서 버그 fix)
`scripts/migrate_lifecycle_2026_05_10.py` (manual once-only, `_apply_post_migrations` 금지). 트랜잭션 내 순서:
1. `INSERT INTO positions_history` (legacy_position_id 보존)
2. **`UPDATE fills SET contribution_id = new_pk`** (DELETE 앞에 와야 — 삭제된 row subquery 시 NULL)
3. `DELETE FROM positions` (logical key 당 latest 1개 외 제거, `ROW_NUMBER() OVER PARTITION BY`)
4. `UPDATE positions SET position_id = pos_{venue}_{sym}_{strat}_{side}`

### gate_events FK (R3-c)
FK 없음 확인 (`schema.py:399~417` `position_id TEXT` no REFERENCES). dashboard `_gate_funnel()` 도 position_id join 안 함. **Jin sign-off 불필요**.

### hydrate + swap (R3-d)
`SimulatedTrade` 에 `active_strategy_id` 없음. close path = `strategy_id` 만 사용 → P0 허용. P1 에서 필드 추가.

### Cross-cut PR 순서 (R3-e)
1. A-PR1 recover.py hydrate (긴급)
2. B-P0-1 `_read_positions` dedup (병렬)
3. A-PR2 positions_history + migration script + position_id PK
4. A-PR3 build_position_key 분리
5. A-PR4 handle_same_key_open + PortfolioState augment + M4 assert
6. A-PR5 v_open_positions view
7. B-P0-2~5 runner + panels

A-PR2 migration 은 A-PR1 merge + loop 1회 hydrate 검증 **후** 실행 권장.

## Status
round-3 완결. **/dev 진입 OK**. R4 불필요.
