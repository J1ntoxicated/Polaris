---
type: research
status: active
date_created: 2026-07-02
tags: [audit, altdata, fuser, freshness]
related: ["[[ai-hooks-audit-verdict]]", "[[judge-probe-reality]]", "[[layer-6-live-recalc]]"]
---

# altdata 도달성 지도 (H2)

> 수집→fuser→소비 3층 대조. H2(write-only 무덤) = **대체로 틀림**.

## 실도달 4경로 (전부 확인)
1. regime tilt — `_production_layers.py:1140` → compose_regime_candidate → detect_regime_flip
2. entrance altdata lean — `:725-738`
3. ticker_ground → judge payload — `_production_run_signal.py:242-252` → `ai_judge._evidence_block`
4. MarketView.altdata → 전략 (weekend_funding maker / gold_riskoff vix)

## 축별 판정
| 소스 | 판정 |
|---|---|
| okx_funding | ALIVE-CONSUMED (스코어러 leg만 임계 미달 휴면) |
| binance_deriv | ALIVE-CONSUMED-**DEFECTIVE** (top_LS 1116/1116 100% BULL) |
| crypto_fg / cftc_cot / fred_macro(35시리즈) | ALIVE-CONSUMED |
| news | PARTIAL (equity 121·crypto 25 그룹만) |
| coinglass / myfxbook | INERT (.env 키 공란, 문서화됨) |

## 결함 3
1. **binance top_LS 포화**: 임계 1.30 < 구조적 min 1.401 → 극공포장(F&G 11~18)에서 bull_trend 힌트 상시 생성, regime/lean/judge label 오염 → percentile 재베이스라인 필요
2. **news_max_age_h silent drop**: 수집기 방출(`news_sentiment.py:471`)하나 `fuser._score_news`가 미복사 → judge 나이라벨(`ai_judge.py:314`)+axis-B news 신선도 leg dead read (#68/#69 재발)
3. **비crypto/equity news 미도달**: forex 0/64 · commodity 0/15 · index 0/5 심볼 매칭 — 구조적 no-op(무로그)

관련: [[ai-hooks-audit-verdict]] [[store-graveyard-census]]
