---
type: debate
topic: dashboard-redesign
status: round-2-done
date_created: 2026-05-10
date_updated: 2026-05-10
forensic: 2026-05-10_dashboard_regression.md
tags: [debate, dashboard, ux, codex]
---

# Topic B debate — Dashboard 재설계

## Round 1 — codex 권고
- **B1**: **B1-γ** (v3 from scratch). mk1 패턴 재사용 + Polaris v2 8-layer L0~L7 명시 매핑. v1/v2 archive.
- **B2**: P0 패널 7개 — Header / Open Positions (logical-key dedup) / Pipeline Funnel G1~G8 / Weight Panel T4 분해 / Autonomy / Cell Matrix (★ ✦ ✧) / Live Log
- **B3**: **B3-γ** 그룹 모듈 6~8 파일. `polaris/scripts/dashboard/{runner,panel_header,panel_positions,panel_pipeline,panel_weight,panel_autonomy,panel_cells,panel_log}.py` + 기존 `snapshot.py` / `ansi_palette.py` 유지. ~870 LOC across 10 files, 각 ≤500 LOC.
- **B4 8-layer 매핑**: L0 → Header+LiveLog / L1 → Positions / **L2 → Pipeline Funnel** / **L3 → Weight Panel** / **L4 → Cell Matrix** / **L5 → Autonomy** / L6 → Positions(uPnL 실시간) / L7 → Header HALT badge

## 화면 size 권고
**220×55** (LG OFFHOURS profile). 140×40 v2 는 weight + cell + pipeline 동시 표시 불가. hjoin LEFT 110 / RIGHT 108. mk1 `spot.py:887` `_hjoin` 패턴 그대로.

## M check 결과
| Mandate | 상태 |
|---|---|
| M1 정보 밀도 ≥ mk1 | PASS — 7 패널 |
| M2 8-layer 모두 시각화 | PASS — B4 표 완성 |
| **M3 중복 즉시 인지** | **현재 FAIL** — `snapshot.py:_read_positions` logical-key dedup 부재. P0 1번 작업 |
| M4 per-position chart | P1 (Unicode chart 포팅 분량 큼) |
| M5 AI gate funnel | PASS — Pipeline Funnel (c) |
| M6 weight resolver T4 | PASS — Weight Panel (d), mk1 `_render_weight_panel:665` 재사용 |
| M7 거부 키워드 sweep | PASS (설계 단계) |

## Round 2 검증 포인트 (codex 자체 제기)
- (R2-1) `gate_events` 테이블 실존 — Pipeline Funnel 데이터 소스. 미존재 시 패널 DARK
- (R2-2) logical-key dedup 정의 — 방향 반대 hedge 가 같은 심볼이면 별개? Topic A `(venue, symbol, strategy, side)` 정합?
- (R2-3) T4 컴포넌트 DB 기록 — `components.cell/atr/spike/taker/quar/crisis` 가 어디 저장? 별도 `weight_events` 테이블 필요?
- (R2-4) 220×55 OFFHOURS profile 실제 해상도 — `scripts/start_dashboard.sh` 실제 geometry
- (R2-5) Elo 테이블 — Autonomy panel 의 Elo 카운트 가능 여부

## 구현 순서
**P0 (즉시)**:
1. `snapshot.py:_read_positions` logical-key dedup (M3 fix, incident 근본)
2. `runner.py` 220×55 hjoin skeleton + B3-γ 파일 구조
3. `panel_positions.py` (dedup + uPnL 색상 + held_sec)
4. `panel_pipeline.py` (G1~G8 funnel)
5. `panel_weight.py` (T4 분해, mk1 `_render_weight_panel` 포팅)

**P1**:
6~10. cells / autonomy / log / header / per-position chart

**Archive 선행**: `dashboard_v2.py` → `polaris/scripts/dashboard_v2_archived_20260510.py` + `log.md` 1-line

## Round 2 — 코드 read 후 결정 (R1 수정)

| R1 | **R2 최종** | 근거 |
|---|---|---|
| 220×55 hard-code | **반응형 + 최소 160×44** | OFFHOURS profile 픽셀 1170×800 → ~167×44 (Monaco 13pt). 220 오버 |
| Weight Panel schema 선행 여부 불명 | **schema 변경 0** | `entry_sizer.py:88~98` 가 SIZED 시 `continuous_scalar/tier_amplifier/cell_routing_mult/listing_watchdog_mult/binding_cap` 다 `gate_events.payload_json` 에 INSERT 중 |
| gate_events 실존 불명 | **존재 확정** + `snapshot.py:541~563 _gate_funnel()` 이미 구현 → `snap.gate_funnel` 소비만 | |
| Autonomy 데이터 소스 불명 | **Crisis + Learner P0 가능 / Evolver + Elo 미구현 (mk1 only)** | `regime_bars` 와 `learners` 이미 snapshot 노출. evolver/elo 는 Polaris v2 schema 부재 |

## R2 핵심 발견 (DB / 코드 실측)
- **모든 P0 단계 schema 변경 0**. dedup SQL 교체 (`_read_positions`), `_weight_stream()` 추가 함수 1개, file tail 1개. snapshot.py 확장 2 필드만 (`PositionRow.row_count`, `weight_stream: list[WeightRow]`).
- **dedup SQL 확정**: `GROUP BY venue, symbol, strategy_id, side` + `COUNT(*) AS row_count`. `row_count>1` → 라벨 `BTC-USDT [×4]` WARNING 색상.
- **logical key Topic A 와 정합**: `(venue, symbol, strategy_id, side)`. 방향 반대 hedge 별개 row, 다른 strategy 같은 (venue, symbol, side) 도 별개 row.

## P0 5단계 (모두 schema 변경 0)
1. `snapshot.py:_read_positions` SQL GROUP BY + row_count
2. `runner.py` 반응형 hjoin 프레임 (left_w = max(80, (W-2)//2))
3. `panel_positions.py` row_count 뱃지 + uPnL 색상
4. `panel_pipeline.py` `snap.gate_funnel` 소비
5. `panel_weight.py` `_weight_stream()` payload_json 파싱

## Round 3 → 불필요. Jin sign-off 4건
- (S1) row_count>1 표시 정책 — scale-in 표시 vs drift 경보. Topic A `handle_same_key_open` 결과와 정합 필요
- (S2) 반응형 vs 고정 width (160 또는 167)
- (S3) Autonomy 패널의 Evolver/Elo — placeholder 표시 vs 항목 자체 제거
- (S4) Live Log 소스 — `data/paper/polaris_runtime.log` file tail vs `alerts` DB

## Status
round-2 완결 — codex R3 불필요. 4 Jin sign-off 후 /dev 진입 가능.

## 관련
- [[2026-05-10_dashboard_regression]]
- [[2026-05-10_topic_a_lifecycle_fix]]
- [[dashboard]]
- [[2026-05-07_p1_dashboard_v1_redesign]]
