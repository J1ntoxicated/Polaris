---
type: research
status: validated
date_created: 2026-06-27
date_updated: 2026-06-27
tags: [research, strategy, weekend, microstructure, maker, okx-spot, reject, flow-not-block, crypto-microstructure]
---

# weekend thin-book microstructure VARIANTS — VERDICT: 0 new build (flush 보완 edge 없음)

DEMO/PAPER · aggressive 보존 · flow_not_block · OKX SPOT long-only · maker(post-only) · real-fee(8bps/side).
backlink: [[weekend_liquidity_range_maker_family_2026-06-27]] · [[weekend_dip_maker_revert_backtest_2026-06-27]] · 검증빌드 `polaris/strategies/weekend_thin_book_flush_maker.py`

## 임무
flush(검증·배포됨) 외 주말 thin-book 마이크로구조 변종에서 OKX-spot-long-only+maker로 fee 이기는 추가 엣지 0~2개 발굴(또는 정직히 0). 변종: range-compression break · liquidity-vacuum pop · depth-imbalance drift · stop-run reclaim.

## 방법 (게싱 X, 실데이터 RAW probe)
yfinance 1H, 12 OKX 메이저/알트/thin, 728일(~104 주말). **RAW frictionless 신호-존재 probe 먼저** — 선행 리서치 2건이 "fill-risk가 아니라 신호부재가 binding"을 증명했으므로, 비용/체결 모델 전에 진입신호 자체의 방향성 엣지부터 측정. forward drift(ATR단위)·MFE/MAE·pos%·주말vs평일·IS/OOS 시간반분할. 스크립트: `/tmp/weekend_micro_variants_probe.py` · `/tmp/weekend_v2_decompose.py` · `/tmp/weekend_flush_complement_probe.py`.

## 결과 — 4 변종 전멸 (RAW 신호 부재)
| 변종 | 주말 RAW 판정 | 근거 |
|---|---|---|
| V1 range_compression_break | REJECT | drift +0.10~0.32ATR이나 pos<50%·IS h6 +0.49→**OOS h6 −0.006** 붕괴. 평일과 차이 미미. 오버핏 향. |
| V2 liquidity_vacuum_pop | REJECT (artifact) | raw +1.3ATR·pos89%로 보였으나 **entry_ref=pre-pop close 측정 artifact**. pop-bar **종가**에서 재측정(실제 maker join 지점)하면 drift→~0, OOS NEGATIVE(d_h4 −0.20). vol_z 그리드 **monotonicity 0**(저볼륨=continuation 가설 기각). 평일과 동일. |
| V3 depth_imbalance_drift | REJECT | drift~0·MFE/MAE 1.0·pos<50% 전 horizon. 순수 노이즈. |
| V4 stop_run_reclaim | REJECT | h2 +0.028(pos53.5%) 잠깐 후 h6~h12 **−0.35로 decay**. reclaim 바운스가 곧 흐름. MFE/MAE<1. IS-positive/OOS-flip. |

핵심: 4개 모두 (a) 트리거 바 자체 움직임 재측정(V2), (b) IS-only 오버핏이 OOS 반전(V1/V4), (c) 순수 노이즈(V3). **신호부재 = binding**이라는 선행 결론에 **3번째 독립 수렴**.

## flush 보완 가능성도 직접 테스트 = 보완 edge 없음
base(RSI<25+wick) / deep(RSI<18) / cluster(2바 연속 oversold) / runlet(같은 신호 let-run 측정) — 4 sibling 전부 **동일 신호의 변주**일 뿐 distinct pocket 아님(runlet=base byte-동일, MFE≥0.30R 적중 ~88% 어디나 = 이미 검증전략이 하베스트하는 +0.30R revert). 미수확 up-tail 없음.

## 🔑 load-bearing 발견 — flush revert는 레짐의존·시간불안정 (검증전략 exit 형태의 정당성)
flush의 forward drift가 **IS/OOS에서 부호가 뒤집힘**:
- IS(전반, ~2024-11~2025-09): d_h8 **−1.84**, d_h12 −2.38, pos39%, MAE −4.76 → 플러시가 **계속 하락**(continuation, bear half).
- OOS(후반, ~2025-09~2026-06): d_h8 **+0.74**, d_h12 +1.21, pos69%, MFE/MAE 1.3 → 플러시가 **강하게 revert**(relief half).

= 주말 flush "revert 엣지"는 항상-참 아님. 하락레짐 반엔 continuation(밟힘), relief레짐 반엔 revert. **검증전략의 bounded +0.30R target + −1.0R rail이 정확히 이 불안정성에 맞는 risk shape** — rail이 IS-레짐 손실 캡, bounded target이 OOS-레짐 revert 수확. let-run(runlet) 형태가 원 리서치에서 죽고 bounded만 산 이유를 RAW가 재확인. → flush를 let-run으로 바꾸거나 더 깊게/길게 hold하는 sibling은 **거동개악**.

## 결론 (정직)
**0 new build.** 주말 thin-book에서 검증된 capitulation flush(bounded revert) 너머 long-only maker가 fee를 이기는 추가 마이크로구조 엣지 = **발견 안 됨**. 4 변종 RAW 신호부재(평일과 무차별 or OOS 반전), flush sibling 4종 비-distinct. maker는 여전히 fee 수학 이김(16bps flat)이나 **신호가 binding constraint** — 선행 2 REJECT와 동일 근본에 독립 수렴. 거동변경 가치 없음 → /debate·빌드 불필요.

교훈 누적(3회): 주말 크립토 long-only 패시브-bid 형태는 (1)dip-revert (2)fade-the-spike (3)4 microstructure 변종 모두 신호부재로 REJECT. **검증된 weekend_thin_book_flush_maker 1개가 주말 OKX의 유일 long-only maker 엣지**라는 결론이 강화됨. 추가 주말 수입원은 마이크로구조 변종 사냥이 아니라 (a)flush 운영 최적화(maker bid depth/체결률 실측 튜닝) 또는 (b)다른 자산축(Capital 주말 FX/금은 미개장이라 OKX 한정)에서 찾아야 함.
```
StructuredOutput: family=weekend_thin_book_microstructure_variants, candidates=[] (0건, 정직한 null result)
```
