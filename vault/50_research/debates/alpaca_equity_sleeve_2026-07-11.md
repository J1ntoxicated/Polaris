---
type: debate
status: converged-r2
date_created: 2026-07-11
participants: [claude-fable-conductor, gpt-codex]
tags: [alpaca, equity, strategy-roster, virtual]
---

# 알파카 에쿼티 슬리브 확장 (Jin "스톡 전략이 저것밖에 없는게 말이 안된다")

**진단**: 데이터 정상(1m/15m/1H/1D × 4,200심볼 신선) vs 전략 3개(전부 1D) — 병목=로스터.
가드: gross-negative 킬 3종(equity_rsi_bb_pullback/gap_go/tsmom) 부활 금지(volume_burst 재출혈 교훈).

## 수렴 로스터 (R2 5/5 CONVERGED, virtual-only dispatch)
| # | id | tf | 핵심 | 근거 |
|---|---|---|---|---|
| 1 | equity_donchian55_breakout | 1D | Donchian 55/20 + ATR trail, slots 8 | okx_donchian net +217, 신고가 10%내 794심볼. w40/80 shadow tags |
| 2 | equity_xsect_52w_momentum | 1D | 252d 고가 2연속 종가 persistence + ATR 후속확인 | index_52w net +191. 죽은 52wk(-190)와 진입로직 상이 |
| 3 | equity_etf_trend_pullback | 1D | macd_ema per-venue 클론, SPY/QQQ/GLD | EQUITY_ETF_LEG inert 해소를 클론으로. tsmom_12_1 shadow comparator 내장 |
| 4 | equity_bb_meanrev_15m | 15m | rsi_bb shape 클론, BB미드 타깃, RTH 내부 게이트 | OKX gross +831/WR39.6%/유일 킬러=수수료→virtual 0. 100-trade gross<0 무조건 킬 |
| 5 | equity_opening_range_breakout | 15m | 캘린더 앵커 ORB 09:30-09:45, vol 2×median | Wave 1.5 최후 투입. 동일 킬 트리거 |

## 공유 인프라 (Phase 0)
0a equity cluster-cap 2종(0.99, env 조절) + resolve_cluster_id strategy_id kwarg ·
0b us_equity_rth_interior(NY zoneinfo, 고정 UTC 금지) · 0c loss_cooldown_bars(심볼-국소
재진입 페이싱, DINO -374 교훈; connors=2) · 0d _equity_liquidity.py 값-컷($30M=p84,
env 하향으로 T3 확장) · 0e spread ingest 버그 별도 티켓.

## GPT BREAKS 수용
원샷 틱 진입 금지(#2 persistence) · 12-1 모멘텀 독립전략→shadow comparator 강등 ·
개장/마감 30분 제외(#4) · per-symbol 손실 쿨다운 · 100-trade gross 킬 트리거 ·
corr_group 전역 unique. 발화경로 적대검증 5축(등록≠발화 INERT 전례) 스펙화.

빌드: wf_184cbfca (infra→1D→15m→적대리뷰 3렌즈). 머지 후 봇 즉시 재기동(Jin mandate).
스펙 원문: 세션 scratchpad alpaca_sleeve_spec.md → 랜딩 시 vault digest로 승격.
