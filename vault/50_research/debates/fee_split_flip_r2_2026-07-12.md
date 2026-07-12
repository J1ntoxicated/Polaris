---
type: debate
status: converged
date_created: 2026-07-12
round: R2 (flip 거동변화 의무 라운드)
participants: [claude-fable-conductor, gpt-5.5-codex]
tags: [score_f, fee-split, flip, gross-bps, trading-metric]
links: ["[[fee_split_judgment_2026-07-10]]"]
---

# Fee-split flip R2 — 적대 검증 수렴 (variant A + 수정 5)

**배경**: R1 수렴 variant A(판정=gross_bps · fee_drag=보고축 · 백분위 이관 · rails 불변).
원장 포렌식: 7d gross_sum -$751(엣지≈0) vs fee_sum +$9,887 — 수수료가 로스의 본질, flip=판정왜곡 제거 정공.
**게이트 실측**(886청산·743 eval-pts): 행동발산 92건 12.4% ≤15% **통과** · isotonic 전 트랙 보존 ·
가상 재판정 24트랙 flip 2건 전부 강등방향, EARN 처너 오승격 0. gross<0 100-trade kill·admission rails 불변.

## R2 판정 (GPT 5×BREAK → 컨덕터 수용/수정/기각)
- **T1 remap 고정테이블 취약(BREAK)** → **수정 수용**: track×venue×regime 전면 층화는 n=886에서 셀 붕괴 → 기각.
  대신 재도출 케이던스(250청산마다 또는 주1회) + stale 플래그(PSI>0.20 / KS>0.15) 채택. 트랙명이 venue-scoped라 per-track 매핑은 이미 venue 반영.
- **T2 처너 잔존경로(BREAK: gross_bps 미미+ · n대량 · fee_drag 대형 → 라벨 획득 후 1×로 fee 소진)** → **수용**:
  EARN 라벨 2축화 — `gross_EARN`(생존/라벨)은 gross축, **economic 자격(tier amp 1.5/2/3× + 슬롯 확장)** = `gross_LCB > best_case_friction + edge_margin`(R1 SCALE 게이트를 슬롯 확장까지 연장). baseline 1× 무손상·차단 0 — flow 유지.
- **T3 크로스베뉴(BREAK: CFD는 spread가 체결가 내장 → gross 의미 상이, 글로벌 pooled fallback=venue bias)** → **수정 수용**:
  n<30 fallback을 글로벌 pooled → **venue-pooled**로 교체. 실측이 BREAK 확증: +2.0 anchor okx +19.9 vs capital +5.6 (3.5×).
- **T4 window=20 재사용(BREAK: 임계 0-근방 밀집, 라벨 진동 우려)** → **수정 수용**: window=20/partial=8 유지(발산 12.4% 실측 근거),
  label-churn 플래그 추가 — 트랙별 flips>3/100청산 · median dwell<20청산 · 전이율 old축 대비 >1.5×.
- **T5 롤백 트립와이어 부재(BREAK)** → **수용**: 아래 수치 그대로 채택 (auto-flag=리뷰 소집, auto-block 아님).

## 최종 flip 스펙 — 임계값 (score anchor → gross_bps)
| 매핑 | n | -4.0 | -3.0 | -1.0 | 0.0 | +2.0 |
|---|---:|---:|---:|---:|---:|---:|
| okx/rsi_bb_pullback | 380 | -36.03 | -29.35 | -9.67 | -0.05 | +19.89 |
| capital/session_breakout | 126 | -11.40 | -7.88 | -3.00 | -0.04 | +4.47 |
| okx/weekend_thin_book_flush_maker | 78 | -36.13 | -28.28 | -9.47 | -0.23 | +19.23 |
| okx/supertrend | 45 | -24.63 | -24.63 | -9.29 | -0.31 | +12.18 |
| okx/weekend_funding_capitulation_maker | 31 | -39.69 | -24.12 | -10.28 | -0.66 | +15.07 |
| okx/ema_crossover | 31 | -39.33 | -27.62 | -6.90 | -0.25 | +16.13 |
| fallback okx-pool | 650 | -39.69 | -29.35 | -9.67 | -0.05 | +19.89 |
| fallback capital-pool | 205 | -11.51 | -8.67 | -3.00 | -0.04 | +5.56 |
| fallback alpaca-pool | 31 | -5.89 | -5.89 | -1.49 | -0.37 | -0.37 |

규칙: 트랙 n≥30=개별 매핑, n<30=자기 venue-pool. isotonic 순서 강제. 250청산/주1회 재도출 + PSI/KS stale 플래그.

## 롤백 리뷰 트립와이어 (auto-flag only)
① 섀도우 발산 rolling 200 eval >18% 또는 7d >20% ② EARN 클래스 집계 rolling 250청산 gross_sum<0 **AND** gross_LCB95<0
③ kill 발화율 pre-flip 14d 대비 >2× 또는 ≥3트랙/일 ④ remap PSI>0.20 / KS>0.15 / 0-근방 임계 drift >3gbps ⑤ T4 label-churn 플래그.

## 미수렴 → Jin 위임
① CFD embedded-spread 분해(raw_price_edge_bps vs spread_bps — mid 기준가 계측 신설 필요, v1) ② notional-weighted LCB Schmitt 재설계
③ alpaca-pool n=31 얇음(+2 anchor 퇴화 -0.37=0.0과 동일) — 잠정 사용 + 재도출 케이던스로 갱신, 두터워질 때까지 EARN 승격은 pooled 아닌 bootstrap 유지 여부.

원문: scratchpad fee_flip_r2.txt · 증거 fee_flip_evidence.md · 분석 fee_flip_analysis{,2,3,4}.py

> [!note] 발산 12.4% 수치의 측정 기저 (리뷰 continuity NIT, 2026-07-12)
> 수용 게이트에 쓰인 '발산 92건/12.4%'는 evidence 단계의 혼합 기저(대조 실험용 dual-score) 측정치. 랜딩된 트립와이어 지표(compute_shadow_divergence + remap 임계)는 동일 데이터에서 ~0.0-0.11%를 읽는다 — 연속성이 주장보다 강하다는 뜻이며, 라이브 트립와이어가 0% 근방인 것은 정상이다.
