---
entity_type: queue
entity_id: inherit_queue
auto: false
last_modified: 2026-05-03
expires: 2026-08-03
editable: true
back_links: ["[[_NOW]]", "[[INDEX]]"]
mode: meta
reviewed_by: codex
tags: [meta, queue, inherit, polaris, scope/external]
---

# _INHERIT_QUEUE — 모태 인수 처리 완료 (Phase 1)

> Codex 디베이트 3라운드에서 식별한 8 인수 대상 모두 Phase 1에서 처리 완료. 이 큐는 archived.

## 1. 학습값 JSON ✅ 처리 완료

| 파일 | 활용 | 결과 노트 |
|---|---|---|
| `data/edge_calibration.json` | Bayesian — 132 cells, top 10 추출 | ✅ [[INSIGHT-003]] |
| `data/tournament_elo.json` | Strategy ELO — top 5 (volatility_spike 4391) | ✅ [[INSIGHT-004]] |
| `data/regime_presets.json` | VIX/FG/DXY thresholds + 4 regime | ✅ [[INSIGHT-005]] |
| `data/frozen_params.json` | 동결 경계값 (spot_crypto/global) | ✅ [[INSIGHT-006]] |
| `data/evolution_state.json` + `bayesian_state.json` | 연속 학습 상태 | ⏸ (모태 evolver 폐기 결정으로 미인수) |

## 2. ⚠️ 위험 ✅ 처리 완료

| 위험 | 결과 |
|---|---|
| Demo WS URL 하드코딩 (`wss://wsuspap.okx.com:8443`) | ✅ [[INSIGHT-011]] — Phase 2b 컴포넌트 작성 시 config 분리 의무 |

## 3. 모태 ADR ✅ 처리 완료

| 모태 | Polaris | 결과 |
|---|---|---|
| ADR-007 (spot trend N strategies) | ADR-006 | ✅ [[ADR-006]] |
| ADR-009 (paper sizing freedom) | ADR-007 | ✅ [[ADR-007]] |
| ADR-010 (vol_factor proportional fix) | ADR-008 | ✅ [[ADR-008]] |
| ADR-011 (perp paradigm shift) | ADR-009 | ✅ [[ADR-009]] (SPOT-only 유지 + 3개월 후 재validate) |

## 4. 모태 INSIGHT ✅ 처리 완료

| 모태 | Polaris | 결과 |
|---|---|---|
| INSIGHT-032 (OKX SPOT scalp 수학적 불가능) | INSIGHT-007 | ✅ [[INSIGHT-007]] |
| INSIGHT-033 (taker fallback unwired) | INSIGHT-008 | ✅ [[INSIGHT-008]] |
| INSIGHT-034 (fee floor miswire cascade) | INSIGHT-009 | ✅ [[INSIGHT-009]] |
| INSIGHT-035 (fee_paid base units bug) | INSIGHT-010 | ✅ [[INSIGHT-010]] |

## 5. 모태 lessons 핵심 5개 ✅ 처리 완료

| 모태 | Polaris | 결과 |
|---|---|---|
| #78 (NULL cascade) | LESSON-001 | ✅ [[LESSON-001]] |
| #47 (Paper vs Live 격차) | LESSON-002 | ✅ [[LESSON-002]] |
| #46 (Runtime verify) | LESSON-003 | ✅ [[LESSON-003]] |
| #45 (Grep-before-guess) | LESSON-004 | ✅ [[LESSON-004]] |
| #44 (소비자 grep 증거) | LESSON-005 | ✅ [[LESSON-005]] |

## 처리 통계

- **신규 INSIGHT**: 9 (003~011)
- **신규 ADR**: 4 (006~009)
- **신규 LESSON**: 5 (001~005)
- **합계**: 18 vault 노트 + _INHERIT_QUEUE update

## Status

**ARCHIVED 2026-05-03** — 모든 인수 완료. 향후 신규 인수 발생 시 별도 큐 생성.
