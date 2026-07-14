---
type: debate
status: decided
date_created: 2026-07-12
tags: [debate, maker-fill, virtual-account, execution-economics, fee-model]
---

# Debate — 버추얼 계정 maker 체결 경제 도입 R1 (2026-07-12)

> DEMO/PAPER 버추얼 계정(전 fill 내부 원장 시뮬). GPT(gpt-5.5, xhigh) 1렌즈 + 산수 독립 재검증.
> 발단: 주말 포렌식 — OKX 체결 1,323건 전량 10bps taker 균일, "maker" 명명 전략도 taker 처리,
> maker_fill_shadow 0행. weekend_thin_book_flush = 유일 gross+(+$396)인데 fee $618 → net -$222.

## 수렴 권고 — **Hybrid: C 즉시 + A shadow-only 병행** (운영 체결 변경 없음)

1. **즉시**: 현행 전량 taker 10bps 유지(운영 판정 무변경) + `maker_fill_shadow` 계측 실배선
   (0행 = 미발화 근본원인 수리 포함).
2. **동시**: (A) price-through 규칙을 shadow-only 계산 — 동일 주문에 대해
   current / price-through-maker(8bps) / missed-opportunity 3열 병렬 적재. 운영 의사결정 미사용.
3. **승격 조건** (데이터 축적 시 A를 **진입 limit에만** 활성화, 청산은 taker 유지):
   traded-through 비율 · 미체결 후 가격 이동 분포 · 진입 미체결의 net 영향 · maker 가정 시
   평균 가격개선 bps. (B) 체결확률 모델은 shadow 실데이터로 큐/부분체결 분포 확인 후에만 검토.

## 근거 산수 (독립 재검증 일치)

- notional ≈ $618k (fee $618 ÷ 10bps). gross +$396.
- (i) 전 레그 maker 8bps: fee $494.40 → net **-$98.40** (여전히 적자)
- (ii) 진입 maker/청산 taker(평균 9bps): fee $556.20 → net **-$160.20**
- (iii) 손익분기 유효 fee = 396/618k ≈ **6.41bps** < maker base 8bps
- **결론: 2bps 절감만으로 thin_book_flush 못 살림.** 살리려면 maker 가격개선(스프레드 미교차)이
  gross에 ≥1.59bps(전 레그) / ≥2.59bps(진입만) 정직 반영돼야 — 이것이 shadow 계측 대상.

## BREAK 결과 요약

- **가짜 엣지 경로**: 터치=체결·큐 앞자리 가정 = 과체결, thin book adverse selection(스치고
  역행하는 나쁜 터치만 체결)이 gross 부풀림. (A) price-through가 대부분 차단하나 부분체결/취소
  잔량까지는 못 막음 → 즉시 운영 도입 대신 shadow 선행.
- **기회소실 왜곡**: (A) 즉시 도입 시 미체결 상태가 재시도/시그널 stale/쿨다운·재진입·학습 기록
  전제("체결됨")를 깨서 lifecycle 전체 재정합 필요 — 충실도 역행 시나리오 실재. 청산 레그 maker화는
  exit stale·반대신호 충돌 위험 최대 → 부분 도입(진입만)이 왜곡 최소.
- **R4 real-wire 정합**: C가 재작성 비용 최저 + 시뮬-라이브 괴리 실측 가능. B는 파라미터 게싱 시
  가짜 정밀도. 순서 = C 계측 고정 → A shadow replay 비교 → 진입만 승격.

## Aggressive bias 정합

체결 차단/스로틀 없음 — 운영 흐름 그대로, 추가되는 것은 계측(shadow)뿐. flow-not-block 준수.
판정축은 이미 gross_bps(fee-split flip 랜딩)라 fee 왜곡이 전략 선별을 막지 않음.

## 원자료

- codex 원문: scratchpad `maker_sim_r1.txt` (session 019f5657, 30,418 tokens)
- 관련: [[fee_split_flip_r2_2026-07-12]] · 주말 포렌식 wf_de752dd0
