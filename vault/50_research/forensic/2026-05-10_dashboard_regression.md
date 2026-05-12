---
type: forensic
status: open
date_created: 2026-05-10
date_updated: 2026-05-10
trigger: manual_jin ("이건 뭔소리야 / 대시보드 저게 맞아 / mk3 봐봐")
tags: [forensic, dashboard, ux-regression]
---

# Dashboard regression — Topic B

## Incident
v2 dashboard (`polaris/scripts/dashboard_v2.py`, 371 LOC, 5/8 redesign) 가 사용자 mental model 과 어긋남. 같은 심볼 중복 4 row 박혀도 인지 불가, "+2037" 같은 모호 숫자, AI 자가진화 / pipeline / weight 시각화 0.

## 기준점 — auto_invasion mk1 dashboard
`/Users/jinyoon/Projects/auto_invasion_mk1-main/invasion/dashboard/` (19 + 50+ section 파일, 5521 LOC)
- **Per-position rotating chart** (line + RSI + MACD + BB) — `ai_position_chart.py`, `chart.py`
- **Pipeline funnel** — `intel_pipeline*`, `signal_flow*`, `pipeline_flow*`
- **Weight resolver** — `weight_panel` (cell × atr × spike × taker × quar × crisis amplifier)
- **Autonomy panel** — Crisis / 6h Learner / Evolver tournament / Elo
- **Cell matrix glyphs** — ★ ✦ ✧ 등급화 시각 인지
- **Strategies stats** per-strategy 패널
- **Multi-window** TUI (left + right hjoin + 별도 live log)

## v2 결함 매핑
| 사용자 호소 | v2 동작 | mk1 대응 |
|---|---|---|
| "활성 포지션 4개나 있는거야?" | 5 LIMIT 슬롯에 같은 심볼 그대로 박힘 | 회전 chart per position — 중복 시각 인지 즉시 |
| "+2037 은 뭐고" | `_fmt_money` 라벨 모호, 단위 K 변환 일관성 X | 명확 라벨 + glyph + color band |
| "이건 뭔소리야" | 6 단순 텍스트 sections | 19+ 패널 정보 밀도 |

## 근본 진단
5/8 v2 redesign mandate "Jin readable / 한국어 큰 글자 6 sections" 자체가 **사용자 의도 오독**. 사용자는 단순화 (정보 축소) 가 아니라 mk1 수준 **정보 밀도 + 시각화 (chart/funnel/glyph)** 를 원함.

## Fix 후보 (Topic B 디베이트 대상)
- **B1** v2 폐기 + mk1 포팅 / v2 보강 / 새 v3 디자인 중 어느 쪽
- **B2** 패널 우선순위 — chart per position / pipeline funnel / weight resolver / autonomy 어느 것부터
- **B3** 파일 구조 — single file (현 v2) vs sections/ 분할 (mk1) vs 중간 hybrid
- **B4** Polaris v2 8-layer 아키텍처 (L0~L7) 와 dashboard 패널의 매핑 — mk1 가 진화한 8-layer 모델 반영해야

## Status
open · 1차 진단 완료. /debate Topic B 대상.
