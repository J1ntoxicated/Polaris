---
type: debate
topic: conviction-pyramiding add-on notional formula
date: 2026-07-10
status: consensus
rounds: 2
related: [[ADR-013-entry-multiprobe-conviction]], [[layer-6-live-recalc]]
---

# Debate — conviction pyramiding add-on notional (2026-07-10)

DEMO/PAPER 가상자금, aggressive/flow_not_block 원칙 하 codex 2-round 검토.
Branch: `wake/conviction-pyramiding`. Sizing-change mandate → `/debate` 선행.

## Decision (consensus)

**D1 — notional 산식**: 안 A 채택 (T4 hard cap 재통과 후 `compute_stack_size_mult`
를 **사후(post-hoc)** 적용, pre-clip 체인에 절대 미주입). 단, `compute_size()`가
side-effect(있음: `record_shadow_observation` / `draw_for_signal` /
`accrue_probe_fee` / `record_shadow_fill`)를 가지므로, **이번 shadow-first
슬라이스는 `compute_size()`를 아예 호출하지 않는다** — writer는
`build_stack_signal()`로 신호 모양(dict, `signal_strength=1.0` baseline +
`layer_size_mult` 별도 필드)만 만들고, 실제 T4 재호출(=notional 확정) +
실주문 발주는 **다음 슬라이스(라이브 dispatcher)** 책임으로 명시 이연.
그 dispatcher는 side-effect-free quote seam(예: `compute_size_quote(...,
effects=False)`)을 선행조건으로 마련해야 한다 — 승인 조건, 이번 슬라이스
불가 사유는 아님.

**D2 — rollout**: shadow-first 확정. 판정(can/cannot)은 항상
`gate_shadow_events`에 로그(gate_id=6, gpt_decision=None — G6 P3에 GPT
카운터파트 없음). 실제 INSERT + add-on 발주는 `POLARIS_CONVICTION_PYRAMID_ACTIVE`
(기본 OFF)로 게이팅 — `core/sizing/r_budget_sizer.py`의 shadow-verify
선례와 동일 패턴. 무기한 관찰은 금지(기회비용) — 로그 누락 0 / 레이어 중복 0
/ cap binding 분포 확인 후 Jin이 플래그 전환.

**D3 — 상수/그룹캡**: `CONVICTION_LAYER_MULTS=(1.0,0.7,0.5)` / max 3층 그대로
재사용 (근거 부족 대비 결합부 리스크가 더 큼). 단 **그룹캡 무결성 갭** 발견:
`can_stack_conviction`이 `layer_sum_size_pct`를 "추가 전 합"으로 검사해
`single_trade_cap*2.2`를 사후 초과할 수 있음. **기존 게이트 함수는 수정하지
않고**(이미 구현+테스트 완료, 재사용 원칙), caller(writer)가
`layer_sum_size_pct`에 **projected 값**(`기존 합 + single_trade_cap_pct ×
compute_stack_size_mult(existing)`, 보수적 상한 근사)을 계산해 넘기는 방식으로
해결 — 기존 모듈 재사용 + 신규 사이징 로직 금지 원칙 동시 충족.

## Blind spots flagged (round 2)

1. Projected-sum 값은 **게이트 판단용 상한 근사치**일 뿐 — 실제 risk/notional
   로 저장 금지 (`position_conviction_layers.size_mult`는 여전히 `1.0/0.7/0.5`
   그대로 기록, projected 값은 gate-input 전용 로컬 변수).
2. `count_layers → INSERT` 사이 동시성 = 같은 `layer_index` 중복 삽입 위험
   (layer_id는 uuid PK라 PK충돌은 없음, index 중복은 별개). Writer의
   `record_conviction_layer`는 idempotent INSERT(`WHERE NOT EXISTS`)로 방어
   — DDL 마이그레이션 없이 애플리케이션 레벨에서 해소.

## Verification checklist before `POLARIS_CONVICTION_PYRAMID_ACTIVE=1` (Jin manual)

- gate_shadow_events 로그 누락 0
- (position_id, layer_index) 중복 0
- cap-binding 분포 관찰 (add-on이 어떤 cap에 자주 걸리는지)
- shadow 판정 분포가 aggressive 승자 증량 기회를 과도하게 억누르지 않는지

Raw transcripts: round1/round2 codex responses archived in session scratchpad
(not vault — advisory only, decision captured above is the durable record).
