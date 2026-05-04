---
entity_type: insight
entity_id: INSIGHT-032
title: Phase 2j — AI Dynamic Sizing 부활 (모태 핵심 가치 인수)
created: 2026-05-04
phase: 2j
status: active
confidence: high
tags: [sizing, kelly, regime, dynamic, phase2j, pure, insight]
back_links: ["[[dynamic_sizing]]", "[[regime_detector]]", "[[performance_tracker]]", "[[ADR-010]]"]
expires: 2026-11-04
editable: true
last_modified: 2026-05-04
reviewed_by: codex
maturity: authoritative
authoritative_basis: Codex Round 17 87% + 모태 가치 부활
---

# INSIGHT-032: Phase 2j — AI Dynamic Sizing 부활

## 문제 인식

모태(auto_invasion_mk1)의 핵심 가치 중 하나가 Polaris 이식 과정에서 누락됨:

- 모든 HYPO `target_size_usd: 200` fixed → 모태의 dynamic sizing 무시
- `signal.confidence` 0.7~0.85 있는데 size에 반영 X
- regime / win rate / drawdown 모두 무시

SPOT에서도 leverage 없이 dynamic sizing 가능:
같은 알파, 같은 fee → **size 5x 동적 = 시간당 수익 5x**

## 구현 (Phase 2j)

### 3 pure module (P6)

**`src/risk/dynamic_sizing.py`** — 핵심 엔진
```
compute_size(SizingInputs) → SizingOutput
pipeline: Kelly × confidence² × regime_mult × dd_mult → cap → min_check
```

**`src/risk/performance_tracker.py`**
```
compute_recent_stats(closed_positions, lookback=20) → {win_rate, avg_win_pct, avg_loss_pct}
win 정의: net_pct = gross_pct - fee_round_trip > 0 (fee 차감 후)
cold start (< 5 trades): defaults {0.5, 0.6%, 0.5%}
```

**`src/risk/regime_detector.py`**
```
detect_regime(btc_candles_1d) → "crisis"|"uptrend"|"flat"|"downtrend"
crisis 우선 (24h drop >= 8% → max bet, 북극성 mandate)
sma20 vs sma50 2% gap threshold
```

### 파이프라인

```
kelly = min(max(0, (b*p-q)/b), 0.5)   # half-Kelly cap
conf² = signal.confidence²              # 0.7→0.49, 0.9→0.81
regime = crisis:1.5 | up:1.0 | flat:0.7 | down:0.3
dd_mult = max(0.2, 1 - drawdown_pct*2)
fraction = kelly × conf² × regime × dd_mult
fraction = min(0.20, fraction)          # MAX_FRACTION hard cap
size = cash × fraction (skip if < $50)
```

### Runner integration

`realtime_runner.py` ENTER_LONG 섹션에 `[DYN-SIZE]` 로그 추가:
```
[DYN-SIZE] HYPO-008-RT DOGE-USDT size=$450 regime=uptrend kelly=0.38 × conf²=0.72 × regime[uptrend]=1.0 × dd[0.0%]=1.00 = 0.090
```

## 효과 추정

| 시나리오 | cash | conf | regime | dd | size |
|----------|------|------|--------|-----|------|
| crisis + 강한 신호 | $5000 | 0.9 | crisis | 0% | $1000 (cap) |
| uptrend + 보통 신호 | $5000 | 0.8 | uptrend | 0% | ~$450 |
| flat + 약한 신호 | $5000 | 0.7 | flat | 0% | ~$164 |
| downtrend | $5000 | 0.8 | downtrend | 10% | ~$80 |
| 약한 신호 + 손실 중 | $5000 | 0.6 | downtrend | 30% | $0 (skip) |

기존 fixed $200 대비: 강한 confluence 시 $1000 (5x) → 시간당 수익 최대 5x

## 검증

- TDD 40 신규 tests (RED→GREEN)
- Hypothesis property-based: 300 samples, fraction ∈ [0, MAX_FRACTION] 보장
- runtime smoke: crisis size=$1000 (MAX_FRACTION), flat size=$164 확인
- 전체 316/316 pass (기존 276 + 40 신규)

## 관련

- [[dynamic_sizing]] [[regime_detector]] [[performance_tracker]]
- ADR-010 (risk caps, MAX_FRACTION=20% 준수)
- north_star.md (crisis escalation mandate)
- 모태 패턴: auto_invasion_mk1 AI sizing architecture
