---
type: research
status: validated
date_created: 2026-07-03
tags: [research, backtest, donchian, 4h, maker, fill-model, oos, okx, revival]
---

# 4H Donchian maker 재백테스트 (fill 현실화) — VERDICT: REJECT (venue-real 바에서 부호 재반전)

DEMO/PAPER · OKX SPOT long-only · aggressive 보존 · flow_not_block (비용 실현, 게이트 아님).
backlink: [[h4_donchian_breakout_backtest_2026-06-27]] (prior: maker+conf D-20 +11bps 박빙) · [[project_validated_edge_is_slow_trend_not_scalp]].
harness: scratchpad `h4_donchian_maker_refit.py` + `reconcile.py` (프로덕션 무접촉). 데이터: `data/okx_candles_cache.sqlite` **venue-real 4H 566d, 29 syms** (prior는 yfinance 730d proxy였음) + live DB 1m 바로 체결 anchor.

## 무엇을 현실화했나 (#91 touch-ward repost 배포 반영)
- 배포 exec: 1 post + 6 repost, step 4bps (`.env` MAX_REPOSTS=6/STEP=4), attempt ~3s → **~21s 윈도, creep 0→24bps**.
- 체결 anchor (live 1m, momentum-up 4H 경계 n=381): P(touch≤60s)=0.92–0.99 → 21s 스케일 **p_fill≈0.55–0.59**; 그리드 0.50/0.70/0.85.
- adverse selection: miss = 첫 바 continuation 상위 (1−p) 분위(러너는 안 되돌아옴) — 최악측 bound. 대조군 = random-miss(낙관 bound).
- 비용: maker 진입 8bps(터치 체결, slip 0, creep 0/12/24 stress) + 엑싯 taker 10bps + slip 10/15/20. fee-in-R 사전산술: 18bps RT=0.070R, +15slip=0.128R (1R≈257bps).

## 결과 — 전 그리드 음수 (OOS = per-inst 후반분할)
| 구성 (D-20 +1D-conf) | OOS net | 비고 |
|---|---|---|
| 낙관 bound (전 신호 maker 체결) | **−0.133R (−34bps)** | 체결률 100% 가정조차 음수 |
| p=0.70 + adverse-sel, slip15/creep12 | 체결당 **−0.591R (−152bps)** · EV/신호 −0.414R | win 14% (러너만 놓침) |
| blended (miss→taker 20bps RT fallback) | −0.16R/신호 | 러너 taker 회수해도 음수 |
| slip 10–20 × creep 0–24 전 그리드 | −0.50 ~ −0.68R/체결 | 양수 cell 0개 |

발화: 신호 1.9/d (D-20 conf, 포트폴리오 전체) → 체결 ~1.1–1.3/d. **빈도는 주중 데일리 슬롯 충족, 부호가 사망.**

## 🚨 핵심: prior +11bps는 데이터소스+기간 아티팩트
prior 비용모델 그대로(maker 16RT flat + slip15, 동일 19-major 유니버스) venue-real 바 재현: **IS +88bps → OOS −41bps**.
캘린더 반분할: 2024-12→2025-08 **+91bps** / 2025-08→2026-06 **−42bps** (N=434/434). 최근 10개월 OKX 실바에서 4H 돌파 엣지 부재.
real taker 20bps RT도 OOS −46bps — taker 재평가로도 구제 불가. 1D Donchian survivor(+97bps)와 달리 4H는 regime 의존 아티팩트였음.

## VERDICT: REJECT — 4H Donchian maker 신규 빌드 No
- 체결 현실화(총 체결확률 + 미체결 기회비용) 어느 조합에서도 OOS net 음수. maker 저비용으로도 못 넘음.
- 확정 교훈: (1) yfinance proxy 백테스트는 venue-real 바로 재검 의무(부호 뒤집힘 실증), (2) 돌파+maker-at-touch는 구조적 adverse selection(체결=되돌림, 미체결=러너) — 러너 수익 의존 전략과 상성 최악, (3) 기존 1D survivor 유지가 정답, 4H 재도전은 venue-real 바 롤링 재검에서 최근-half 양수 복귀 시에만.
