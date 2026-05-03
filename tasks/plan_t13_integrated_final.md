# Plan T13 — Integrated FINAL (Harness 확정 스냅샷)

> **상태**: 2026-04-24 Fri 11:XX AEST. Jin "auto mode 다 하라" 지시. Harness 자율 확정 (v2.2 D4 Jin 승인 gate 대체).
> **Parent**: v2.2 본문 (`plan_t13_integrated_v2_2.md`, 265 lines) + v2.3 delta (`plan_t13_integrated_v2_3.md`, 54 lines).

## I. 완료 (커밋 SHA)

### Phase 0~5 (결정 + MVP 실행)
```
Phase 0    b2f637a0  코드 전수조사 (7 결함)
Phase 0.5  ee18cb04  Plan vs Code 정합
Phase 1    bcfaffa7  Plan v2.1
Phase 2    70e78d69  Data 실측 (E16/E17/E18)
Phase 3    305d186c  Harness audit
Phase 4    15aa463a + 818562d0 + d4a7f165  Plan v2.2 + agent 5 stub + hook
```

### Phase 5 신규 10항 (D-A~J) + 원안 10항
```
8e87f803  D-A hourly_stats *100 중복곱 제거
70254876  D-B signal_blocks.jsonl
d8844704  D-D alpaca asset_group populate
1481e856  D-H feature_disable_flush_days + ProactiveExit filter
2a989477  D-J fsm_harvest_trail_mult amplify-only
4912bc77  Phase 5 docs (v2.3 + briefing)
7edfa07b  원안 #3 paper_to_live preg
2f0d3d25  v2.3 재업데이트 (원안 4항 + D-E 확정)
```

### v2.2 단계 1 (D0~D2)
```
6ef41ffd  D0.5 mult bounds 11건 amplify-only migrate
ecbadba1  D0 yaml + D1 delta + D2 audit
```

### v2.2 단계 3 (D6~D10 MVP)
```
321aea19  D6 H.5 Kill Switch (file + DD 통합)
7eac1ca1  D7+D7.5 H.10 Backup snapshot + Restore 리허설
c0d29970  D8 H.1 signals.trade_id write + trade_events
ebede747  D9 H.2 duplicate_open guard (+reconcile)
2fe92e29  D10 G1 DB 11 테이블 schema
```

### 단계 3.5 Forensic (D11.5~9)
```
e128f96b  tasks/forensic_t13_d115_to_9.md (70 lines)
```

### 단계 2 (D3/D3.5/D4/D5)
```
(본 commit)  design_t13_stage2.md + plan_t13_integrated_final.md + canonical sync
```

## II. 잔여 D# (단계 4 이후)

| D# | 주제 | 성격 | 우선도 |
|---|---|---|---|
| D11 | M1~M3 Cell API wrap (6-dim read/learn shim) | 중 | 🟡 D16a 앞단 |
| D12 | H.4 Canary + KPI Guard + Hook 확장 4 | 중 | 🟡 |
| D13 | Phase 1.5 Event Bus + AI audit (원안 #8 preg 결정) | 대 | 🔴 |
| D14 | Phase 1.3 Signal hygiene + signal_queue consumer | 대 | 🔴 (E17 TIME 연결) |
| D14.5 | Lag KPI 집계 | 소 | 🟢 |
| D15 | H.3 Auto Data QA + null_strategy filter | 중 | 🟡 |
| D16a/a.5/b | Cell API 8-dim 확장 (ticker + liquidity_tier) + session_axis | 대 | 🔴 |
| D17 | G3+G4 Tier 1 프로세스 분리 | 대공사 | 🔴 |
| D18~D18.7 | Liquidity layer + Flow Amplifier + Multi-factor + Peak capture KPI | 대 | 🔴 |
| D19~D19.8 | Phase 2.5 PHS skeleton + Loss Attribution + K.10 flush window | 대 | 🔴 E17 근거 |
| D20~D22 | 관측 48h + KPI | 관측 | 🟢 |

## III. 원칙 재확인

- `feedback_no_single_review_verdict` / `feedback_no_quick_patch_ever` / `feedback_flow_not_block` 매 변경 자가검증
- Per-Change Gate 4축 (A 북극성 / B 타당성 / C Feedback / D 구조 결함)
- 모든 숫자 → preg 이관 (또는 cell axis 이관, Phase 6)
- MD 60줄 상한

## IV. Phase 6 — 100% 메트릭스화 Pivot

Plan: [`.claude/plans/cell-matrix-100pct-pivot.md`](../.claude/plans/cell-matrix-100pct-pivot.md) (5 phase, 30-40h). P1 sizing mult 단일화 → P2 exit cell-aware → P3 direction → P4 provider → P5 Elo. **의존**: D11 (Cell API wrap) + D16a (8-dim) 선행 필수.

---

**다음 착수**: 봇 restart → 24h 관측 → D11+D16a → Phase 6 착수.
