---
type: research
status: active
date_created: 2026-07-10
tags: [audit, dormant, triage, table]
related: ["[[dormant-census-triage_2026-07-10]]"]
---

# 삼분류 판정표 (전 항목)

## WAKE (깨우면 거래 이로움 — 구체 소비경로 확인)
| 항목 | 소비경로/효과 | 우선 |
|---|---|---|
| position_conviction_layers | G6 open pnl_r≥0.5 → conviction layer INSERT + 독립 add-on(1.0/0.7/0.5) 발주. 승자 피라미딩=비대칭 상방 | **HIGH** |
| gate_kill_counterfactuals(15.7k행) | (gate,cohort)별 KILL fwd-R 집계 reader → 이긴 신호 죽이는 게이트 loosening 증거(shadow/debate-gated). 피드백 레그 부활 | **MED-HIGH** |
| benchmark_results+replay_runs(0행) | run_replay nightly 스케줄 → confidence 대시 패널 가동. **display-only 확정**(게이트 아님) | MED |
| learner_prune.py(orphan) | daily 유지보수에 스케줄 → dead-strategy learner/cell row sweep. posterior 오염 방지 | LOW |

## DELETE (의도 소멸/대체/데이터소스 부재 — 아카이브 후 제거)
| 항목 | 근거 | 안전확인 |
|---|---|---|
| market_events(43k행) | regime_state가 라이브 SSOT, 이건 zero-reader 중복 event log·WAL 경합 | reader repo 0·regime_state UPDATE 무손상 |
| orders(0행) | writer 영구 부재, fills가 역할 흡수 | reader=삭제될 dashboard_v0 dead fallback뿐 |
| REFINE_TIMING stamp(1,560건) | submitter 미구현, 소비자 0, 거동상 proceed-at-market와 동일 | stamp 제거=거동무변(/debate=프롬프트 vocab 별건) |
| dashboard_v0.py(449) | 웹 대시보드로 대체, 웹서버도 미참조 | AST 도달 0 |
| shadow_acceptance.py(417) | 목적(G3/G4 cutover 결정)이 ADR-011(6/11)로 소멸, 라이브 인라인 미러 | 7/07 터치=mass-rename뿐 |
| normalize.py(169) | T11 API, 5주+ orphan, 라이브 L1 미사용 입증 | AST 도달 0 |
| knowledge_loop.py(107) | `FROM probe_decisions` **DB 미존재** — write-side 미빌드 | slice-2 재개 시 재작성(Jin veto) |
| calibration.py(273) | `FROM v_probe_outcomes` **DB 미존재** | slice-2 재개 시 재작성(Jin veto) |

## KEEP-DORMANT (정당 대기 — 깨어나는 조건)
| 항목 | 깨어나는 조건 |
|---|---|
| ai_lessons(353행) | lessons 디제스트 reader 신설 시(현 의도적 archival SSOT) |
| maker_fill_shadow(0행) | 실-와이어 flip + OKX post-only 실제 resting(현 47체결 taker 폴백) |
| loop_rotation_events / learner_blocks / capital_reopen_pending(0행) | 자연 트리거 발화(로테이션·트리플블록·Capital 재개) — self-contained lifecycle |
| G6_PROBE_TIGHTEN(OFF) | shadow가 tighten 개선 입증 후 supervised opt-in(단 tighten=리스크스로틀, aggressive 긴장) |
| PROBE_ENGINE trail_only/full | Slice 2 배선(현 observe만 live, 2값 placebo — 혼란 flag) |
| okx/alpaca adapter·EOD_FLATTEN·ORPHAN_RECONCILE·RECONCILE_VENUE_IMPORT | 실-와이어 flip(`POLARIS_VIRTUAL_ACCOUNT=0`) — [[feedback_virtual_account_first_then_real_wire]] |
| coinglass/myfxbook 컬렉터 | `.env` API 키 주입(현 EMPTY) |

## KEEP(비-동면)·RESOLVED
- **KEEP**: AI_FREE(ADR-011 의도, CLAUDE.md gating-pipeline 문서 stale 수정 필요) · correct_*/backfill/recalc/liquidate(정당 수동 ops툴) · env_gated C(CANDIDATE/LADDER/SESSION_WARM/DBWRITER/ALPACA_SNAPSHOT = alive)
- **RESOLVED**: weekend_shadow_orders(11k행, weekend_data.py 소비 → ALIVE-display 재분류) · EIA/USDA(CFTC COT 무료 스왑 완료 설계결정)
- **제외**: evolve/P0a 생성기(candidate-factory-wire 진행중) · strategy_risk_state(어제 깨움)
