---
type: plan
status: next-session
date_created: 2026-07-15
tags: [dashboard, flow, open-positions, todo]
---

# 대시보드 TODO — 오픈 포지션 인스펙트 (토큰 리셋 후)

Jin 2026-07-15 스펙 (데이터 전부 스냅샷에 이미 있음 — spark 30pt·entry_price·
stop_price·last_price·mfe_atr_r·mae_atr_r·regime·entry_regime. **서버 변경 0, 렌더만**):

## 1. 포지션 줄 인라인 스파크라인
각 포지션 행에 미니 트렌드(spark 30pt), uPnL 부호로 색(그린/레드). 인라인 ~40×12px,
블룸버그 밀도 유지(카드 X). renderPositions(wall_side.js).

## 2. 액티비티 절반 축소 + 차트 패널 신설
좌측 컬럼: OPEN POSITIONS(위) → **ACTIVITY 절반 축소** → **CHART 패널**(빈 절반).
flow.html: `.side-act` height 축소 + `.side-sec.side-chart` 추가.

## 3. 차트 = 가벼운 인스펙트 (깊은 차트는 보드에 이미 있음 — Jin)
선택된 포지션의 spark 라인 + **진입선(점선)·스톱선(레드 점선)·현재 마커** + MFE/MAE 밴드
+ 헤더(심볼·전략·side·베뉴)·uPnL·레짐변화. 딥차트 재구현 금지(보드 것 활용/링크로 족함).

## 4. 선택 로직
- **기본 = 가장 최근 거래 자동추종** (min held_sec / max opened_ts). 새 거래 들어오면 자동 전환.
- **포지션 클릭 → 그 포지션으로 핀** (다음 새 거래 오면 다시 자동 최근으로).
- 상태: `_chartSel`(핀 키) + `_topKey`(최근); topKey 바뀌면(새 거래) 핀 해제→최근.

## 참고
MFE/MAE 밴드가 방금 발견한 **giveback 문제(프로브 1R 반납)를 보드에서 시각화** — 어떤
포지션이 +2.5R 갔다 반납 중인지 즉시. 랙 미미(클릭 시 1개만 렌더). 리셋 대시 작업에 포함.
