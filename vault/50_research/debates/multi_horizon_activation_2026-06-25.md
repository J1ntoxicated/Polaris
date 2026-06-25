---
type: research
status: debate-converged
date_created: 2026-06-25
tags: [debate, architecture, multi-horizon, scalp-swing-position, regime, exit]
---

# 멀티-호라이즌 활성화 — Debate (GPT-5.2 + Gemini-2.5-pro)

## 문제 (실측 wndh50swf)
봇 사실상 스캘핑 only (51 closed 전부 <60분, swing/position 0). 토대 ~80% 존재. 장벽 2개: (A 진입라우팅) 틱엔진이 OKX/Capital 소유 → 바 스윙전략 통째 양도 → signals 0. (B 엑싯) horizon-버킷 캘리브+EOD opt-out 누락 → 1H 스윙도 틱속도 청산.

## 수렴 (양측 "수정 채택")
- **D1 진입라우팅**: carve-out 확장 = 버그수정(설계의도 복원, 주석:157). 단 per-symbol risk cap 단독 불충분. **blind netting 금지** — 틱-스캘프·바-스윙을 `(strategy_id,symbol)` **독립 논리포지션**(per-strategy attribution)으로 공존. **호라이즌별 risk 예산 배분**(예: swing/pos 60% / scalp 40% — 단순배분, 9-stack 곱셈기 아님). [GPT: OKX netting이 틱롱 위 바숏을 강제반전 → 가상슬롯/sub-account. Gemini: 자원잠식 방지 위해 horizon-budget.]
- **D2 엑싯 horizon-hold**: `held<horizon AND thesis 건강 → harvest/trail-tighten 보류`. 단 **necessary-not-sufficient**: 손절(-1.0R)+thesis-broken(RED)이 horizon 무관 **항상 최우선**. trail "발동"은 유지하되 폭만 swing 3.0/pos 4.0. **trail_mult를 진입고정 아닌 라이브 regime 동적**(chop 전환→3.0→1.5 즉시, whipsaw 방어). 비대칭(익절측만 run) 유지.
- **D3 regime→horizon**: trend=swing+pos+scalp / chop=scalp / crisis=fast-scalp. 라벨스위치 아닌 efficiency_ratio·vol_scale 연속스코어→horizon_seconds(버킷 마지막). **히스테리시스 필수**(N-bar 지속=regime 확정, 깜빡임 방어). 포지션 오픈 후 regime 깜빡여도 horizon 즉시 안 바꾸고 thesis 전이 때만.

## 발산 — 빌드 순서
- **GPT**: entry-first **L1→L2→L3(regime refine)**. 병목이 "안들어감/들어와도 바로나감"이라 매핑부터면 원인-결과 뒤섞임.
- **Gemini**: **D3→D1→D2** (regime이 진입/엑싯의 전제·컨텍스트).

## 종합 (reconcile — regime 감지는 이미 존재)
regime DETECTION은 이미 있음(regime_state 4라벨). 양측 #1 위험=whipsaw. → **순서: ⓪ regime 히스테리시스(작은 전제, 기존감지 활용) → ① L1 진입라우팅(독립포지션+horizon-budget, no-netting) → ② L2 엑싯(necessary-not-sufficient + 라이브-regime 동적 trail_mult)**. per-ticker efficiency/vol 세분은 후속 최적화.

## 안전장치 (양측 합의, 빌드 필수)
1. no blind netting (독립 per-strategy 포지션) 2. horizon risk 배분(단일cap 아님) 3. 손절+thesis-RED가 horizon 항상 우선 4. trail_mult 라이브-regime 동적(chop→tighten) 5. regime 히스테리시스. flow_not_block/9-stack/비대칭 payoff 정합. 거부키워드 0.

## 관련
[[multi_horizon_architecture_design]] · [[north-star]] · [[feedback_no_block_filter_architecture]] · 전문=/tmp/debate_mh_out.txt
