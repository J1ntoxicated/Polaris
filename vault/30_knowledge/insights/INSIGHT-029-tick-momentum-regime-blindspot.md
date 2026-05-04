---
entity_type: insight
entity_id: INSIGHT-029
auto: false
last_modified: 2026-05-04
expires: 2026-11-04
editable: true
back_links: ["[[INSIGHT-028]]", "[[INSIGHT-027]]", "[[ADR-013]]", "[[INSIGHT-021]]"]
mode: forensic
reviewed_by: codex
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-029 — TickMomentum Regime Blindspot (Round 14 Forensic)

> Round 13 deploy 후 60분 측정: HYPO-010 win 57%→49%, PnL +$8.25→-$3.83 — silent size cap bug + regime change cluster + 구조적 부적합 ticker 3 root cause 확인. Codex Round 14 (88% 합의) 4 fix 적용.

## Context

Phase 2g Round 13에서 HYPO-010 size $300 결정 (INSIGHT-028 기반) 후 60분 실측 재측정. 예상: PnL ×1.5 상승. 실제: PnL 역전 (-$3.83). FORENSIC 모드로 3 root cause 진단 → Codex Round 14 submit.

## Evidence

### 60분 실측 비교 (Round 13 vs Round 14 직전)

| 지표 | Round 13 (n=70) | Round 14 직전 (n=?) | 변화 |
|---|---|---|---|
| HYPO-010 win% | 57% | 49% | -8pp |
| HYPO-010 PnL | +$8.25 | -$3.83 | -$12.08 역전 |

### Root Cause 1 — HYPO-010 size $300 silent override bug

Round 13 결정: `DEFAULT_TARGET_SIZE_USD = 300`.

그러나 `realtime_runner.py` HYPO-010-TICK 등록 시:
```
max_position_pct = 0.04  # 4% of $5,000 capital
```
포지션 cap = `0.04 × $5,000 = $200` → size $300 의도가 $200으로 **무음 차단**.

**의도-코드 불일치**: Round 13 코드 구현은 `DEFAULT_TARGET_SIZE_USD 200→300` 변경 + `realtime_runner HYPO-010-TICK params override target_size_usd:300` 기록되었으나, `max_position_pct=0.04` cap이 $200 hard ceiling을 유지 → 실질 size = $200 그대로.

**Fix**: `target_size_usd=200` 복원 (cap과 정합) — size-up 효과는 실제로 없었으므로, win 하락 = cap 효과가 아닌 regime 변화임을 확인.

### Root Cause 2 — 14:36 이후 13 trade 전부 SL (regime change cluster)

14:36 이후 연속 13 trade 전부 SL 도달. Multi-ticker 동조 하락 패턴 confirmed:
- BTC, ETH, SOL, DOGE 동시 하락 (correlated sell-off)
- TickMomentum은 단일 ticker 24h OHLC slow momentum — cross-ticker regime 변화 감지 불가
- 동일 시간대 HYPO-008/017 포지션도 손실 → 시장 전체 risk-off event

**SL cluster**: 5분 이내 3+ ticker SL = regime change signal. 현 전략은 이 신호 인지 없음.

### Root Cause 3 — TRUMP/ORDI 구조적 부적합

**TRUMP-USDT**: price range 0.42% (일 변동) < SL 0.35% × 2 = 0.70% 최소 필요. 틱당 변동이 SL 내에 수렴 → TP 도달 수학적 불가능 구간 다수.

**ORDI subcause — 24h_change 추격 패턴**: `24h_change >= +1.5%` 조건이 오전 급등 종목 필터링. 오전 급등 → intraday top 추격매수 패턴 → 오후 하락 시 SL 집중 (mean reversion에 취약).

### Codex Round 14 결정 (88% 합의)

| Fix | 내용 | 근거 |
|---|---|---|
| Fix 1 | `target_size_usd 300→200` 복원 | max_position_pct=0.04 cap 정합 회복 — 실질 size unchanged, 불일치 해소 |
| Fix 2 | TRUMP-USDT 제거 | price range < SL×2 구조적 부적합 — fee 수학 불가능 |
| Fix 3 | Cross-ticker SL cluster guard | 5분 내 3+ ticker SL 시 10분 pause (ticker-global cooldown 확장) |
| Fix 4 | HYPO-016 trigger 재정의 | `n>=30 AND win<33%` (기존 `n=10 TP=0`) — 소표본 오판 방지 |

## Root Cause (종합)

HYPO-010 성과 역전의 직접 원인은 **regime change** (14:36 동조 하락): TickMomentum은 단기 모멘텀 추종이라 시장 전환 시 cluster SL 필연적. size cap bug는 추가 손실을 증폭시키지 않았으나, 설계 불일치로 Round 13 size-up 효과가 실현되지 않았음. TRUMP 구조적 부적합은 기회비용(losing trade 소진).

**Pattern**: scalp 전략 + regime blind = crisis multiplier. ADR-013 HARNESS 확장 — cross-ticker SL guard가 regime filter 역할 수행.

## Impact

- 직접: HYPO-010 ticker list 축소 (TRUMP 제거), size 정합 복원, regime guard 신설
- 간접: HYPO-016 trigger 재정의 → 소표본 오판 패턴 차단 (HYPO-011/012 조기 deprecate 교훈)
- 시스템: cross-ticker SL cluster guard → 모든 realtime HYPO에 적용 (ticker-global cooldown 확장 형태)

## Test Results

- 230/230 pass
- vault lint 0/0

## Recommendation

- [x] `target_size_usd 300→200` 복원 (code-implementer — Round 14)
- [x] TRUMP-USDT HYPO-010 ticker list 제거 (code-implementer — Round 14)
- [x] Cross-ticker SL cluster guard 구현 (5분 3+ SL → 10분 pause) (code-implementer — Round 14)
- [x] HYPO-016 deprecate trigger `n>=30 AND win<33%` (code-implementer — Round 14)
- [ ] 24h_change filter 대안 검토 — intraday top 추격매수 패턴 차단 조건 재설계 (vault-curator — Round 15)
- [ ] HYPO-010 regime-aware 확장 — multi-ticker correlation filter (Round 15+ 검토)

## Related

- [[INSIGHT-028]] Round 13 결정 (HYPO-010 size ×1.5 원안) — 이 INSIGHT의 직접 선행
- [[INSIGHT-027]] HYPO-010/017 신호 직교성 확인
- [[ADR-013]] HARNESS Meta Mode — cross-ticker guard 결정 dispatch
- [[INSIGHT-021]] flip-flop fee bleed fix — ticker-global cooldown 원형 (Round 4)
