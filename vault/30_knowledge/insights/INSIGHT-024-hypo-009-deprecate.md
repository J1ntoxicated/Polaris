---
entity_type: insight
entity_id: INSIGHT-024
auto: false
last_modified: 2026-05-04
expires: 2026-08-04
editable: true
back_links: ["[[INSIGHT-023]]", "[[INSIGHT-021]]", "[[ADR-004]]"]
mode: dev
reviewed_by: codex
maturity: authoritative
authoritative_basis: Codex Round 9 직접 측정 (n=16, EV 계산, TP<SL 비대칭 구조 분석). 92% 합의 — deprecate 결정.
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-024 — HYPO-009 Deprecate 근거 (Codex Round 9, 92% 합의)

## 측정 결과 (직접 데이터)

| HYPO | n | win% | TP | SL | signal_exit | total PnL | EV/trade (paper) | EV/trade (live) |
|---|---|---|---|---|---|---|---|---|
| HYPO-009-RT (BreakoutMomentum) | 16 | 44% | 7 | 9 | 0 | -$2.47 | -1.33% | -0.07% |

## EV 계산

**Paper fee (0.014 round-trip):**
- EV = 0.6% × 0.44 - 0.35% × 0.56 - 0.14% × 2 (entry+exit) ≈ -1.33%/trade

**Live fee (0.0014 round-trip):**
- EV = 0.6% × 0.44 - 0.35% × 0.56 - 0.14% ≈ -0.07%/trade

양쪽 모두 음수 — paper fee는 구조를 더 선명하게 드러냄.

## 구조적 실패 원인

### BreakoutMomentum (HYPO-009)
- **TP < SL 비대칭**: TP 7회 / SL 9회 — win rate 44%로는 음의 EV 탈출 불가.
- `TP_PCT = 0.6%` / `SL_PCT = 0.35%` — 손실 크기가 수익보다 작지만, 빈도 비대칭이 구조적.
- n=16은 확정적이지 않으나 Round 8 패턴(HYPO-011/012)과 동일: TP<SL, parameter tuning으로 구제 불가.
- Breakout 1H: 채널 돌파 직후 reverse 빈번 (noise vs signal 구분 불가 — 1H OHLC candle 기준).

## 결정: Deprecate (Rule)

**REALTIME_HYPOS에서 HYPO-009-RT 제거.**

- 전략 파일(`src/strategies/breakout_momentum.py`) **삭제 X** — 학습 아카이브 보존.
- deprecated comment 4줄 보존 (n/EV/원인/파일 위치).
- Round 10 불필요: 92% ≥ 80% ADR-004 기준 충족.

## TDD 증거

`tests/paper/test_realtime_runner.py::test_hypo_009_not_in_realtime_hypos` — `REALTIME_HYPOS`에서 `HYPO-009-RT` 부재 확인. **157/157 pass** (RED→GREEN TDD 순서 준수).

## 잔여 이슈 (8% gap)

Forensic audit 진행 중: `net_usd` fee 차감 검증 — HYPO-008/010 size 결정 보류.
결과에 따라 별도 INSIGHT 또는 ADR 작성.

## 패턴 누적 (HYPO-009/011/012)

| 공통 패턴 | 설명 |
|---|---|
| TP<SL 비대칭 | 빈도 비대칭 × 크기 비대칭 = 이중 손해 |
| 단기 snapshot 지표 | book imbalance / taker ratio / breakout 1H = noise dominant |
| signal_exit 또는 SL 지배 | TP 도달 전 exit — fee 2회 이상 흡수 |

## 연결

- [[INSIGHT-023]] HYPO-011/012 deprecate — 동일 Round 패턴 (Round 8 → Round 9)
- [[INSIGHT-021]] flip-flop fee bleed fix — min_hold/hysteresis 배경
- [[ADR-004]] 코드 리뷰 codex 외부 의무 (Round 9 92% 합의 근거)
