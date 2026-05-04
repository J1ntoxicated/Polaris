---
entity_type: insight
entity_id: INSIGHT-030
auto: false
last_modified: 2026-05-04
expires: 2026-11-04
editable: true
back_links: ["[[INSIGHT-029]]", "[[INSIGHT-023]]", "[[INSIGHT-024]]", "[[ADR-014]]", "[[ADR-013]]"]
mode: dev
reviewed_by: codex
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-030 — Phase 2g 종료 결정 — tick-driven scalp 비활성, 1d trend portfolio 강화

## 결정 (Jin 판단 2026-05-04, Round 15)

tick-driven scalp 전략 5개 전부 deprecated. 1d trend portfolio 강화.

## 데이터 요약

| HYPO | n | win% | PnL | 결정 | 이유 |
|---|---|---|---|---|---|
| HYPO-010-TICK | 95 | 43% | -$14.98 | deprecated | 변질 진행 (Round 14 수정 후에도 EV 음수) |
| HYPO-013-MTA | 1 | 100% | +$0.46 | deprecated | sample 부족, 빈도 0 (60분 실측) |
| HYPO-014-BLEAD | 1 | 0% | -$0.20 | deprecated | vol threshold 미달, cross-exchange lead 미확인 |
| HYPO-016-OFI | 37 | 24% | -$3.92 | deprecated | 사전 trigger — 사후 momentum 추종 실패 |
| HYPO-017-CASCADE | 0 | - | - | deprecated | BTC 1min +0.30%+ETH +0.10% 동시 조건 빈도 부족 |

유지:
| HYPO-007-RT | 0 | - | - | 유지 | RSI15m cron-style, rare-trigger 정상 |
| HYPO-008-RT | 29 | 55% | +$3.50 | 유지 | 유일한 양수 EV scalp |

## 패턴 (Lessons)

1. **tick-driven scalp = regime-sensitive**: HYPO-010이 Round 4~14 반복 수정 후에도 EV 음수 유지 → 전략 자체가 아니라 tick noise에 edge 없음을 시사.
2. **sample 부족 = 결론 불가 = deprecate**: HYPO-013/014 n=1 — 검증 불가 상태에서 자원 낭비. 60분 실측으로도 신뢰 구간 0%.
3. **사전 trigger 실패**: HYPO-016 OFI는 Chordia 2021 이론 기반이나 실 market microstructure와 불일치 — signed volume이 단기 price prediction 선행 지표가 되지 않음.
4. **구조적 희소 조건**: HYPO-017 BTC cascade의 1min +0.30% 조건은 실 trading 시간 내 거의 발생 안 함 → trigger 빈도 0.

## 1d Trend Portfolio 강화 근거

- HYPO-003 SMA 50/200 1D: backtest Sharpe viable, SPOT-only 최적
- HYPO-004 Donchian: 2 variants (entry/exit asymmetry 활용)
- 8 ticker basket: 단일 ticker 집중 위험 분산
- fee 0.0014 × daily 진입 빈도 = fee drag 최소화

## 관련 ADR

- [[ADR-014]] Polaris alpha portfolio v1 — backtest 검증 viable strategies only

## 코드 변경

- `src/paper/realtime_runner.py`: REALTIME_HYPOS = 2 (HYPO-007/008만)
- `src/paper/cron.py`: ACTIVE_HYPOS = 3 entry (SMA 8ticker + Donchian 2 variants)
- `tests/paper/test_realtime_runner.py`: `test_realtime_hypos_only_007_008` 신규
- `tests/paper/test_cron.py`: 신규 (8 ticker SMA + Donchian variants 검증)
