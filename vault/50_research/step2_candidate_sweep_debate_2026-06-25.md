---
type: research
status: design-converged
date_created: 2026-06-25
tags: [debate, architecture, candidate-sweep, focus, dynamic-universe, north-star]
---

# Step② 후보 스윕 설계 — Debate (GPT-5.2 + Gemini-2.5-pro) + Jin 결정

## 문제 (라이브 실측)
봇이 1882 active 중 focus 120(merit-rank 고정)만 WS구독→틱→거래 → "맨날 들어오는 애들만"(~53개). tick_inflow 51k틱/10분(시장 살아있음)인데 focus 심볼 calm. Step①이 1650 티커에 정적 그라운드(ticker_ground) 깔아둠 → 동적 후보선정의 토대.

## 수렴 (양측)
- **D1 스코어 = 2단**: `score = Activation × (0.5+0.5×Edge)`. **움직임(Activation)이 1순위**, 센티/셋업(Edge)은 방향 보조. (내 초안의 "센티 동등"을 양측이 교정 — 병목이 calm focus라 *움직일 종목* 먼저.) look-ahead 방지(확정 바만), 과최적화 회피(컴포넌트 고정+linear).
- **D2 로테이션 = 듀얼**: micro(2-3분 activation급등) + macro(15-30분 전수재랭킹) + 시장템포 동적주기 + 히스테리시스(점수차 enter/exit) + 오픈포지션 강제유지 + 이벤트 트리거.
- **D3 focus = 버킷**: anchor(merit ~40) + dynamic(sweep ~140) + event_hot(~10) + exploration(랜덤 ~10, anti-blindness+신선도). venue 배분(OKX 24/7 / Capital 세션 / Alpaca 미국장). cold-start=vol penalty.
- **D4 cap/blindness**: scan-then-subscribe — 1650 전수 스캔(무료, ground)은 cap 무관, WS만 focus. fast-track(이상치→즉시 승격)으로 "왕건이 놓침" 방지.

## 발산 → Jin 결정
- focus cap: GPT 200+scan, Gemini 400 공격적. **Jin 결정 = 200 + scan/fast-track** (균형, WS한계 내, 부하 ~2배).

## 핵심 (내 초안 → debate 교정)
"focus를 안 바뀌는 merit top120 → 매 사이클 움직이는 후보 top200"(scan-then-subscribe). 스코어 핵심 = **Activation(움직임) gate × Edge(방향)**. flow_not_block: 스캔 전체커버 + fast-track 놓침방지 = 차단 아님.

## 관련
[[north-star]] · [[static_ground_step1_2026-06-25]] · [[feedback_no_block_filter_architecture]] · 전문=/tmp/debate_step2_out.txt
