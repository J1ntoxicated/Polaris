# gold_breakout_1h 기하 확장 백테스트 — SILVER/US100/DE40/UK100 (후보③)

2026-07-03 · DEMO/PAPER · scratch 백테스트 (프로덕션 무접촉) · [[layer-2-gates]] [[ADR-008]]

## 방법
- 기하 동일: 1H D-55 prior-high 돌파 LONG 엔트리(현재봉 제외, no look-ahead) → D-20 prior-low 트레일 엑싯(Turtle-2 let-run), 동시 1포지션, fee-floor K=3 R-unit(`max(2×ATR14%, 3×왕복cost)`).
- 데이터: yfinance 1H — SI=F(풀세션, GOLD 원형과 동일 관례) / ^NDX·^GDAXI·^FTSE(현물 세션봉만 = 세션창 내 발화).
- 비용: Capital 스프레드 실측(quote_ticks 2026-07-03 스냅샷: DE40 0.58 / UK100 0.94 / US100 1.36bps; SILVER DB부재→알려진 최소스프레드 ~10bps) + 슬리피지 스트레스 10/15/20bps 왕복.
- OOS 반분할(후반) 판정 + 9-combo 지터(entry 45/55/65 × exit 15/20/25) no-cliff 검사.
- 하네스 검증: GC=F 컨트롤 OOS net@15 +45.6bps (스펙 검증치 NET +34bps 재현·상회) ✓

## 결과 (OOS 후반, net@슬리피지10/15/20)
| 심볼 | n(OOS) | gross | net@10/15/20 bps | netR@15 | fee-in-R | 지터 | IS 전반 | 판정 |
|---|---|---|---|---|---|---|---|---|
| SILVER | 47 | +109.4 | +89.4/+84.4/+79.4 | +0.99R | 0.07 | 9/9 | @20만 -1.8 | **PASS** |
| US100 | 18 | +121.5 | +110.2/+105.2/+100.2 | +0.44R | 0.01 | 9/9 | 전부 양수 | **PASS** |
| UK100 | 26 | +53.6 | +42.7/+37.7/+32.7 | +0.77R | 0.02 | 9/9 | 음수(-9~-19) | **PASS(유보)** |
| DE40 | 26 | +35.1 | +24.6/+19.6/+14.6 | +0.48R | 0.01 | **4/9 cliff** | 전부 양수 | **REJECT** |

- DE40: 55/20 이웃 콤보 5/9 음수 = 원형 스펙의 no-cliff 기준 자체로 탈락(커브핏 신호).
- UK100: 파라미터 무적합 이식이라 전구간이 사실상 OOS — 전기간 net@15 ≈ +12bps 양수 + 지터 9/9이나 전반 음수 = 최근 국면 의존, 소형 프로브 배분 권고.
- 발화 빈도: SILVER ~0.13/일, UK100 ~0.05/일, US100 ~0.03/일 — 저빈도 추세, churn 아님([[project_validated_edge_is_slow_trend_not_scalp]] 정합).

## 빌드 스펙 초안 (PASS 3종)
`gold_breakout_1h.py` 패턴 그대로 심볼별 신규 모듈(또는 SUPPORTED_SYMBOLS 확장 대신 심볼별 metadata 분리 — per_ticker_tailored): D-55/D-20, TTL 3, warmup 60, TREND correlation_group(reversion substring 금지), hold_overnight=True, profit_target_r=None, venue=capital, 세션창 = 심볼 활성 세션(US100/UK100 현물 세션, SILVER 풀세션). 사이징 캡·fee-floor K=3은 기존 G5/G7·exit_strategy_config 공용 경로 그대로. 빌드 후 라이브 발화경로 적대검증 의무([[feedback_verify_firing_after_build]]).

스크립트: scratchpad `cand3_breakout_ext.py` / `cand3_jitter.py` (세션 스크래치, 재현용 사본 보존 안 함 — 방법론 본문 기재로 충분).
