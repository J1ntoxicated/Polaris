---
type: handover
status: active
date_created: 2026-05-30
date_updated: 2026-05-30
tags: [handover, 3-stream, autonomous]
---

# HANDOVER 2026-05-30 — 자율 3-스트림 완성 (Jin 취침)

다음 세션 시작: `_NOW.md` 최상단 HANDOVER + `.claude/plans/stream_architecture_redesign_2026-05-30.md`(plan SSOT) + 태스크 #23-28 + debate doc 읽고 **체인 이어서 완성**.

## Mandate
3-스트림 아키텍처(OKX 크립토 spot / Capital CFD 롱숏 / Alpaca 주식 spot)를 끝까지 자율 완성 + 대시보드 재구성 + 교차통합 검증. 각 단계 = 워크플로우(build TDD → adversarial review → 거동게이트) → 커밋 → 보고. OKX 봇 무중단(최종 재기동만 graceful). 비용(fee/slip/AI$) 모니터링 + 지속 개선 루프.

## 확정 사실
- **AI**: GPT(OpenAI)+Gemini 사용가능, **Claude/Anthropic 차단**(라우팅 금지). /debate=GPT+Gemini.
- **/debate `b565392`**: Capital 레버리지 flat30→per-market(FX30/지수20/원자재20/cCFD2, 0-폴백 교체); Track C buying_power gross 3.0/per-sym 0.99/daily 0.99, PDT 별도 카운터.
- **파운데이션 `e932af6`**: StreamConfig SSOT+product_class+분기 9곳→resolve_stream+schema additive+대시보드1. 815 green, 거동 동일, OKX 봇 무중단.
- 크립토 파생 불가(OKX 검증=spot only, Binance 선물 막힘→Binance 철회 `22fffd9`).

## 남은 체인 (tasks)
- **#23 Phase2**: T6 Capital 3전략 숏 미러 + T7 per-market 레버리지 + T14 net-edge 비용.
- **#24 Phase3**: T8 Track C + T9-13 Alpaca(어댑터/universe/전략 equity_tsmom·rsi_bb 리스킨+equity_gap_go 신규). Alpaca paper키 검증됨 $79.7k BP.
- **#25 Phase4**: 대시보드 단계2 + 3-스트림 봇 클린재기동 + 교차통합 live-audit(3 venue 각자 레짐·전략·엑싯 맞물림, cross-contam 0).
- **#26 Phase5**: 사용성검증 + 익스체인지별 분류/오픈·클로즈 포지션 + 은하(글로브) 확장(스트림 colony).
- **#27 (read-only /debate)**: AI 멀티소스 의사결정경로(auto_invasion mk1) + alt-data wire(CoinGlass/FRED/Quandl/MyFxBook) + 게이트별 AI/all-PASS 실태 + AI콜 효과 + 전체설계 재검증.
- **#28**: 비용 모니터링(fee+slip+AI$ stream별 → 대시보드).

## .env toolkit
AI=OpenAI/Gemini(가용)·Anthropic(차단) · venue=OKX/Capital/Alpaca/IG(또다른 CFD)/Binance(철회) · data=CoinGlass(펀딩/OI/청산)/FRED(매크로)/Quandl/MyFxBook(센티먼트).

## 불변
9-stack 봉쇄 · hard-MAX(headroom_min+0.09) · AGGRESSIVE(방어throttle X) · DEMO/PAPER · builder≠reviewer · workflow-first · OKX 봇/Claude 창 kill 금지.

## 운영
OKX 스팟 봇 pid 67774 `data/polaris_live.sqlite` 수집중 · 웹 :8770(3:7) · 버그: .gitignore `data/`가 `polaris/core/data/`까지 잡음→신규파일 `git add -f`.
