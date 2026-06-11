---
type: debate
status: resolved
date_created: 2026-06-11
tags: [debate, conviction-stack, rotation, strategy-swap, trading-params]
related: [[MOC-A1-design-dev]], [[layer-6-live-recalc]], [[capital_rotation_2026-05-30]]
---

# 보유연장·자본회전 기관 3종 활성화 — /debate 타결 (2라운드)

DEMO/PAPER. 소스: Claude 2계층(설계 wf_3c225790 + 적대검토 5블로커) + codex(GPT) 2라운드. Gemini 경로 미연결(기록).
전제 기결: 스택 게이트 +0.5R/quartile/2.2×/3레이어, rotation margin $5/LCB n≥20/age 300s/cooldown 300s/PER_TICK 1, swap cap 1, 어트리뷰션 40/60 — 무변경.

## 타결 (11/11)
| 안건 | 결정 |
|---|---|
| R0 트리거3 도착지 | 기본=차순위 신호. clamped 이름은 **restoration 이벤트**로만 재진입: 결핍분 상한·포지션당 1회·신호 재검증(나이<1 tf-bar)·event_type='restoration' 별도 기록·가드 면제의 유일 경로 (R2 상한 합산) |
| R1 클램프 임계 | 체결<요청×**0.5** (검출=체결 후 실측비) |
| R2 회전 상한 | **4/h** (restoration 합산, per-symbol/group 중복 회전 동일 카운트) — 빈도 레일, P&L 정지 아님 |
| R3 스택 자식 | rotation **victim 선정에서 면제** (부모 생존 중만; 엑싯 면책 아님 — 부모 엑싯/그룹 무효화 종속) |
| C1 스택 트리거 | **래더**: L1=+0.5R, L2=+1.0R (증축마다 새 가격 진전 요구 — 터틀 원형) |
| C2 레이어 베이스 | 부모 레이어0 **실체결 notional 고정** (비복리) |
| C3 venue | **OKX 선행**, Capital은 웨이브2 실노출 캡 바인딩 검증 후 별도 플래그 |
| S1 스왑 페어 | tsmom↔rsi_bb / donchian↔rsi_bb / fx_breakout↔fx_range_fade — 섀도 측정 후 apply 승격 |
| S2 tf 방향 | **고tf→저tf 스왑 금지** (트레일 래칫 점프·timeout 4배 단축·fade 즉발의 은닉 3중 조임 봉쇄 — 보유연장 방향만 개방) |
| S3 다이얼 승계 | 즉시 발효 (S2로 잔여=완화 방향 — 자동 해소). 섀도 로그에 스왑 직후 trail/timeout/TP 델타 기록 |
| O1 롤아웃 | 순차 3-웨이브(rotation→conviction+swap섀도→swap apply) + 이벤트 수 게이트 + 단계별 env 플래그(기본 off) |

## 빌드 필수 수정 (적대검토 블로커 — debate 비대상)
B1 conviction 자식 open_trades append(좀비 차단) · B2 swap 섀도 G6 payload 주입 금지(widen 정지 함정 — 별도 seam) + apply 시 swap_count==0 가드 · B3 from≠to 자기스왑 가드 · B4 후보 correlation_group=포지션 underlying_group_id 정합 · B5 레이어0 lazy seed(첫 증축 2배 방지).

## codex 핵심 기여
R0 자기강화 루프 적발→restoration 격리(채택) · "R3·S2 없으면 stack=churn 루프, swap=은닉 엑싯 조임" 독립 수렴 · R2에 중복 회전 합산 조건.
