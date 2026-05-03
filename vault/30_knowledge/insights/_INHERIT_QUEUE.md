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

# _INHERIT_QUEUE — Codex 식별 8개 모태 인수 대상 (Phase D 이후 추출)

> Codex 디베이트 3라운드에서 모태 직접 read해서 식별한 인수 대상. 실제 추출은 Phase D `writing-plans` 산출 plan에서.
>
> 만료: 2026-08-03 (3개월 내 모두 처리 또는 폐기 결정 필요).

## 1. 학습값 JSON (즉시 추출 가능, 60_alpha 첫 가설 후보)

| 파일 | 내용 | 활용 |
|---|---|---|
| `auto_invasion_mk1-main/data/edge_calibration.json` | Bayesian Beta(α,β) — cboe_vix_term n=254, dual_thrust n=200 | HYPO-001 후보 |
| `auto_invasion_mk1-main/data/tournament_elo.json` | 전략 ELO — volatility_spike 4391, crypto_contrarian_swing_g11_bayes 3476 | HYPO-002 후보 |
| `auto_invasion_mk1-main/data/regime_presets.json` | regime별 파라미터 (NEUTRAL/RISK_ON 등) | Polaris 초기값 base |
| `auto_invasion_mk1-main/data/frozen_params.json` | 동결 경계값 | 절대 건드리면 안 되는 값 |
| (보조) `auto_invasion_mk1-main/data/evolution_state.json` + `bayesian_state.json` | 연속 학습 상태 | 활용 검토 |

## 2. ⚠️ 위험 (코드 작성 시 즉시 조치)

| 위험 | 위치 | 조치 |
|---|---|---|
| Demo WS URL 하드코딩 | `auto_invasion_mk1-main/invasion/spot/ws_feed_spot.py` (`wss://wsuspap.okx.com:8443`) | Polaris 첫 컴포넌트 작성 시 live URL로 교체. INSIGHT-003 신설 후보. |

## 3. 모태 ADR 인수 (Polaris 20_decisions/로)

| ADR | 제목 | 인수 사유 |
|---|---|---|
| 모태 ADR-007 | spot trend N strategies 아키텍처 | Polaris 첫 컴포넌트 설계 base |
| 모태 ADR-009 | paper sizing freedom | sizing 정책 base |
| 모태 ADR-010 | vol factor proportional fix | sizing 정책 보강 |
| 모태 ADR-011 | perp paradigm shift (SPOT-only 결정) | 옵션 Y 정당성 근거 |

## 4. 모태 INSIGHT 인수 (Polaris 30_knowledge/insights/로)

| INSIGHT | 제목 | 인수 사유 |
|---|---|---|
| 모태 INSIGHT-032 | OKX SPOT scalp 수학적 불가능 (fee 분석) | Polaris 알파 검증 base |
| 모태 INSIGHT-033 | taker fallback 미연결 | execution 설계 시 |
| 모태 INSIGHT-034 | fee floor 오배선 | sizing 검증 시 |
| 모태 INSIGHT-035 | fee 단위 버그 | property-based test 시 |

## 5. 모태 lessons 핵심 5개 (Polaris 30_knowledge/lessons/로)

| Lesson | 핵심 |
|---|---|
| 모태 #78 | NULL cascade — numeric column NULL 금지. property-based test 필수. |
| 모태 #47 | Paper vs Live 행동 격차 = 치명적. promotion gate에 paper/live diff audit 명시. |
| 모태 #46 | Runtime verify 의무. import 통과 ≠ runtime 통과. |
| 모태 #45 | Grep-before-guess. 제안 전 기존 코드 확인 의무. |
| 모태 #44 | 소비자 grep 증거 없는 feature commit 금지. |

## 처리 워크플로

1. Phase D `writing-plans` 산출 plan에서 각 항목 명시적 추출 단계 정의
2. 추출 시 vault-curator agent로 노트 작성 (백링크 ≥ 2 강제)
3. 처리 완료 시 이 큐에서 제거 + log.md 기록
4. 만료(2026-08-03)까지 처리 못 한 항목은 폐기 결정 (Jin escalation)
