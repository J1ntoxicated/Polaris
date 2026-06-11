---
type: lesson
date_created: 2026-06-11
tags: [instrumentation, g3, g4, g7, counterfactual, shadow, altdata, regime]
status: built
related: [[MOC-A1-design-dev]], [[layer-2-per-gate-pipeline]], [[regime_layered_synthesis_2026-05-31]]
---

# 계측 웨이브 — gate→outcome / G7 섀도 / altdata hint (거동 0)

## 무엇이 막혀 있었나
- G3/G4 GPT가 1,854건(20.7%) KILL했지만 counterfactual 측정 불가 — gate_events에
  signal→position 링크도, KILL 이후 가격 추적도 없었음. GPT 가치 판정 불능.
- G7 P1 GPT가 HOLD 러버스탬프 의심(46/46)인데 결정적 레일(Q9) 대비 발산율 데이터 0.
- altdata fuse_evidence의 `_hint` 폐기가 버그인지 의도인지 코드만으로 판독 불가.

## 무엇을 깔았나 (전부 측정, 트레이딩 결정 라인 0 수정)
1. **`gate_kill_counterfactuals`** — G3/G4 결정마다 1행(KILL+PASS 동일 추정량,
   리뷰 B2). mark=신선 WS 틱 우선/bar close 폴백+`mark_source` 감사(B3).
   `cost_r = 2×real_fee_usd(venue,mark)/atr_usd` — fwd_r와 동일 per-unit ATR-R,
   가격레벨 독립(B1; 구설계 $50명목/per-unit 분모는 차원 불일치였음).
   전방 마킹 스위프(60s 스로틀·LIMIT 200·이미 인제스트된 1m bars만): 호라이즌
   이후 첫 바, long/short 부호, 24h+3d 유예 초과 시 `unresolvable` 종결(B5 기아 방지).
   읽기는 트랜잭션 밖+yield, UPDATE는 단일 동기 txn(공유 conn BEGIN 충돌 0).
2. **PASS 링크** — 오픈 성공 시 run_id 조인으로 gate_events.position_id 백필
   (tick-engine generic signal_id 모호성 원천 차단) + 풀클로즈 기존 UPDATE에
   `positions.pnl_r` 스탬프(부분청산/reconciled는 NULL 유지). 과거분은
   `backfill_gate_outcome_links.py`(segments→pnl_r + 1:1 signal_id만, 오프라인).
3. **`v_g34_cohort_outcomes`** — KILL fee-adj 전방 R vs PASS 단일 SELECT
   (fwd_lag_sec 병기, post-migration 생성). /debate #G3G4 자료 직공급.
4. **G7 발산 섀도** — `_p1_decide` 순수 추출이 **raw GPT 라벨을 직접 반환**(B4;
   reason 자유텍스트라 사후 복원 불가였음). gate_shadow_events(gate_id=7)에
   레일 vs GPT + `site:`/`gpt_raw:` flags. 사이트: live_recalc/orchestrator만 —
   entry_exercise 합성 proposal은 로깅 생략(러버스탬프율 왜곡 방지, 리뷰 nit).
5. **hint 계측** — `compute_and_flip_regime(hint_stats=)` 인메모리 카운터 3종
   (total/tilt_lost_to_price/final_mismatch), DB 쓰기 0, 루프 summary 1줄.

## hint 판정 (조사 결론)
**의도된 폐기, 버그 아님** — hint는 compose가 소비하는 동일 evidence scores의
argmax 잉여 투영(2026-05-31 regime_layered 디베이트 AGREE-CHG #2). COT(#3)는
scores 경유로 정상 유입. 단 fuser.py 헤더 docstring 말미가 구 binary-override
배선을 설명하는 stale 문서(별도 doc-fix 대상). 재배선=라벨 변경=거동 변화이므로
필요 시 반드시 /debate 선행 (COT 캘리브레이션 FLAGGED와 묶어서).

## 교훈
- counterfactual 비용은 **분자·분모 동일 단위**가 생명: $수수료/단위ATR 혼합은
  BTC에선 소멸·저가코인에선 폭발 — fee_rate/atr_pct 꼴로 차원 소거가 정답.
- 코호트 비교는 **추정량 공유**가 우선(PASS도 같은 고정-호라이즌 fwd_r) —
  실현 pnl_r은 엑싯엔진 효과가 섞인 보조 지표로만.
- KILL fwd_r는 체결현실(슬리피지/부분체결) 미반영 **상한 추정** — /debate 명시.
