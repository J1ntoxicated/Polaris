# Polaris Phase 0~1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase A/B/C 결과 정합성 검증 후, 모태 8 인수 소스를 Polaris vault로 추출 (INSIGHT/LESSON/ADR 노트화).

**Architecture:** Vault-first. 모든 산출물은 `vault/{20_decisions,30_knowledge}/` 노트로 작성, codex 외부 리뷰(ADR-004) 사이클 적용. 코드 작성 없음(Phase 2부터). FORENSIC + DEBATE + ALPHA 모드 사용.

**Tech Stack:** Python 3.11+, vault_lint v4 (already built), Hypothesis (Phase 2), Anthropic Claude + OpenAI Codex.

> **Phase 2~4는 outline만 — 별도 plan 파일에서 detailed.**
> 이유: Phase 2부터 트레이딩 코드 작성/알파 검증 시작 → 각 작업이 독립 spec 필요.

---

## Context

Polaris bootstrap Phase A/B/C 완료 (vault SSOT 정착 + lint v4 + 4 hooks + 4 agent + settings.json). 이제:
- **Phase 0**: Phase A/B/C 산출물 정합 final check + codex 외부 리뷰
- **Phase 1**: Codex 디베이트 3 라운드에서 식별한 8 인수 소스를 Polaris vault로 추출

8 인수 소스 (자세히는 [[_INHERIT_QUEUE]]):
1. `data/edge_calibration.json` (Bayesian 학습값) → INSIGHT-003
2. `data/tournament_elo.json` (전략 ELO) → INSIGHT-004
3. `data/regime_presets.json` (regime 파라미터) → INSIGHT-005
4. `data/frozen_params.json` (동결 경계값) → INSIGHT-006
5. 모태 ADR-007/009/010/011 → Polaris ADR-006~009
6. 모태 INSIGHT-032~035 → Polaris INSIGHT-007~010
7. 모태 lessons #78/#47/#46/#45/#44 → Polaris LESSON-001~005
8. demo WS URL 위험 → INSIGHT-011

---

## File Structure

### Phase 0 (검증만, 신규 파일 X)
- 검증 대상: 기존 Phase A/B/C 산출물 (vault/, .claude/hooks/, .claude/agents/, tools/vault_lint.py, .claude/settings.json)
- 산출: codex 리뷰 결과 → `vault/50_runtime/codex_review_phase_abc.md`

### Phase 1 (vault 노트 신규)
- Create: `vault/30_knowledge/insights/INSIGHT-003-edge-calibration-baseline.md`
- Create: `vault/30_knowledge/insights/INSIGHT-004-tournament-elo-top-strategies.md`
- Create: `vault/30_knowledge/insights/INSIGHT-005-regime-presets-base.md`
- Create: `vault/30_knowledge/insights/INSIGHT-006-frozen-params-boundary.md`
- Create: `vault/20_decisions/ADR-006-spot-trend-n-strategies.md`
- Create: `vault/20_decisions/ADR-007-paper-sizing-freedom.md`
- Create: `vault/20_decisions/ADR-008-vol-factor-proportional-fix.md`
- Create: `vault/20_decisions/ADR-009-perp-paradigm-shift-spot-only.md`
- Create: `vault/30_knowledge/insights/INSIGHT-007-okx-spot-fee-mathematical.md`
- Create: `vault/30_knowledge/insights/INSIGHT-008-taker-fallback-not-wired.md`
- Create: `vault/30_knowledge/insights/INSIGHT-009-fee-floor-miswiring.md`
- Create: `vault/30_knowledge/insights/INSIGHT-010-fee-unit-bug.md`
- Create: `vault/30_knowledge/lessons/LESSON-001-null-cascade-prevention.md`
- Create: `vault/30_knowledge/lessons/LESSON-002-paper-vs-live-divergence.md`
- Create: `vault/30_knowledge/lessons/LESSON-003-runtime-verify-mandatory.md`
- Create: `vault/30_knowledge/lessons/LESSON-004-grep-before-guess.md`
- Create: `vault/30_knowledge/lessons/LESSON-005-consumer-grep-evidence.md`
- Create: `vault/30_knowledge/insights/INSIGHT-011-demo-ws-url-risk.md`
- Modify: `vault/30_knowledge/insights/_INHERIT_QUEUE.md` (처리 완료 표시)
- Modify: `vault/INDEX.md` (신규 9 ADR + 9 INSIGHT + 5 LESSON 인덱스)
- Modify: `vault/_NOW.md` (Phase 0/1 진행 + 다음 액션)
- Modify: `vault/log.md` (chronological append)

---

## Phase 0 — Constitution + 운영 모델 정착 검증 (DEV/DEBATE 모드)

**Goal**: Phase A/B/C 산출물이 ADR-002/005 정합 + codex 외부 리뷰 의무 (ADR-004) 통과.
**Time estimate**: 60-90분 (대부분 codex 리뷰 라운드 시간).
**Mode**: DEBATE (Phase A/B/C 통합 review).
**Dependencies**: Phase A/B/C 모두 completed (`/Users/jinyoon/.claude/plans/valiant-baking-sutton.md`).

### Task 0.1: vault_lint full pass 재확인

**Files:**
- Read: `vault/` 전체
- Test: `tools/vault_lint.py` 실행

- [ ] **Step 1: 전체 lint 실행**

```bash
cd /Users/jinyoon/Projects/Polaris
python3 tools/vault_lint.py --report
```

Expected output:
```
[PASS] orphan (Karpathy): 0 FAIL / 0 WARN
[PASS] stale (Karpathy): 0 FAIL / 0 WARN
[PASS] contradictions (Karpathy): 0 FAIL / 0 WARN
[PASS] machine_state_leak (Polaris P1): 0 FAIL / 0 WARN
[PASS] expires_required (Polaris P2): 0 FAIL / 0 WARN
[PASS] proposed_age (Polaris P2): 0 FAIL / 0 WARN
[PASS] reviewed_by_codex (Polaris ADR-004): 0 FAIL / 0 WARN
[PASS] pure_field (Polaris P6): 0 FAIL / 0 WARN
[PASS] authoritative_basis (Polaris Governance): 0 FAIL / 0 WARN
[PASS] tag_taxonomy (Polaris Standard): 0 FAIL / 0 WARN
=== Total: 0 FAIL / 0 WARN ===
```

- [ ] **Step 2: 만약 FAIL/WARN 있으면 fix**

각 violation 한 줄씩 처리:
- orphan: 해당 노트에 백링크 추가
- stale: status를 expired/archived로
- expires-required: ADR/INSIGHT/HYPOTHESIS frontmatter `expires` 추가
- reviewed-by: 40_components 노트에 `reviewed_by: codex` (Phase 2 이후 발생)

### Task 0.2: 4 hook smoke test

**Files:**
- Test: `.claude/hooks/{pre_commit,post_edit,post_stop,pre_agent}.py`

- [ ] **Step 1: pre_commit hook (vault_lint 통과 시 0)**

```bash
python3 .claude/hooks/pre_commit.py; echo "exit: $?"
```

Expected: `exit: 0` (vault_lint 통과)

- [ ] **Step 2: pre_agent hook (Polaris 4 agent 통과)**

```bash
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"vault-curator"}}' | python3 .claude/hooks/pre_agent.py; echo "exit: $?"
```

Expected: `exit: 0`

- [ ] **Step 3: pre_agent hook (deprecated agent 차단)**

```bash
echo '{"tool_name":"Agent","tool_input":{"subagent_type":"dev-coder"}}' | python3 .claude/hooks/pre_agent.py; echo "exit: $?"
```

Expected: `exit: 2` + stderr "BLOCKED: 'dev-coder' is a legacy _DEPRECATED agent"

- [ ] **Step 4: post_edit hook (코드 변경 알림)**

```bash
echo '{"tool_name":"Edit","tool_input":{"file_path":"/Users/jinyoon/Projects/Polaris/src/spot/test.py"}}' | python3 .claude/hooks/post_edit.py; echo "exit: $?"
```

Expected: `exit: 0` + stderr "코드 변경 감지: src/spot/test.py → 40_components/test.md 갱신 권장"

- [ ] **Step 5: post_stop hook (_NOW 24h 미갱신 검사)**

```bash
python3 .claude/hooks/post_stop.py; echo "exit: $?"
```

Expected: `exit: 0` (오늘 _NOW 갱신했으면 warn 없음)

### Task 0.3: 4 agent definition 인지 테스트

**Files:**
- Test: 각 agent definition 파일 read + frontmatter parse

- [ ] **Step 1: 4 agent file 존재 + frontmatter 검증**

```bash
cd /Users/jinyoon/Projects/Polaris
for agent in vault-curator code-implementer forensic-investigator codex-debate-partner; do
  test -f ".claude/agents/$agent.md" && echo "OK: $agent" || echo "MISSING: $agent"
done
```

Expected: 4 OK lines.

- [ ] **Step 2: agent invoke sample (vault-curator)**

Claude Code에서 직접:
```
Task tool: subagent_type="vault-curator", description="Self-introduce", prompt="역할/책임/금지사항/모드를 한 문단으로 요약해라. 100단어 이내."
```

Expected: vault-curator가 본인 역할 정확히 인지 (vault read/write/lint, 백링크 강제, 코드 작성 금지, ADR-004 codex 외부 리뷰 routing 등).

### Task 0.4: codex 외부 리뷰 sample run (codex-debate-partner)

**Goal**: Phase A/B/C 코드 (vault_lint.py + 4 hooks + 4 agent definitions + settings.json)에 대해 codex 1라운드 리뷰. ADR-004 의무 사이클 작동 확인.

**Files:**
- Input: `tools/vault_lint.py`, `.claude/hooks/*.py`, `.claude/agents/*.md`, `.claude/settings.json`
- Output: `vault/50_runtime/codex_review_phase_abc.md`

- [ ] **Step 1: codex agent 호출 (codex:codex-rescue subagent_type)**

Agent invoke:
```
Task tool: subagent_type="codex:codex-rescue"
prompt:
"""
Polaris bootstrap Phase A/B/C 코드 외부 리뷰 (ADR-004 의무 — Jin mandate).

검토 대상:
- /Users/jinyoon/Projects/Polaris/tools/vault_lint.py (350+ lines, Polaris vault 정합성 검사)
- /Users/jinyoon/Projects/Polaris/.claude/hooks/{pre_commit,post_edit,post_stop,pre_agent}.py (4 hook)
- /Users/jinyoon/Projects/Polaris/.claude/agents/{vault-curator,code-implementer,forensic-investigator,codex-debate-partner}.md
- /Users/jinyoon/Projects/Polaris/.claude/settings.json

검토 기준 (4 contract + 7 영속 원칙):
1. P1 Authority 분리: vault_lint가 machine_state_leak 정확히 검출하는가?
2. P2 Lifecycle: expires_required, proposed_age 검사 정확한가?
3. P3 Write Path: pre_agent.py가 4 contract 책임 매트릭스 정확히 강제하는가?
4. P4 Validation Boundary: pre_commit + reviewed_by_codex 검사 충분한가?
5. P6 Pure Core: pure_field 검사 적절한가?
6. P7 Property-based: lint 자체에 Hypothesis test 적용 권장?
7. ADR-004 코드 리뷰 의무: codex-debate-partner agent definition이 사이클 명시적으로 routing하는가?
8. ADR-005 4 모드: agent definitions가 Mode integration 명시 충실?

빠진 risk / edge case / 폴루션 매개체 가능성 명시.
1500단어 이내, 한국어, 마크다운 헤더, 결론에 합의 % (100/95/80/...)
"""
```

- [ ] **Step 2: codex 피드백 수신 → vault에 stub 저장**

vault-curator로 routing해서 `vault/50_runtime/codex_review_phase_abc.md` 작성:
```markdown
---
entity_type: review
entity_id: codex_review_phase_abc
auto: false
last_modified: 2026-05-03
expires: never
editable: false
back_links: ["[[ADR-004]]", "[[_NOW]]"]
mode: meta
reviewed_by: codex
tags: [meta, review, polaris, mode/meta]
---

# Codex Review — Phase A/B/C Bootstrap Code

Round 1 결과: [codex 응답 그대로 인용]

합의 %: NN
잔여 gap: <명시>
```

- [ ] **Step 3: 합의 % 평가**

- 100% 합의: Phase 0 완료
- 95-99%: 잔여 gap fix → vault 노트 update → re-review (Round 2)
- 80% 이하: max 3 라운드 사이클
- 미합의 (3 라운드 후): Jin escalation → `vault/50_runtime/codex_escalation_log.md`

- [ ] **Step 4: 합의 도달 시 ADR-004 적용 사례 기록**

`vault/50_runtime/codex_review_phase_abc.md`에 라운드별 결과 + 최종 합의 % + 적용 변경 사항.

### Task 0.5: Phase 0 완료 commit

**Files:**
- Add: vault/_NOW.md, vault/log.md (Phase 0 진행 기록), vault/50_runtime/codex_review_phase_abc.md (있으면)

- [ ] **Step 1: vault/_NOW.md 갱신 (Phase 0 완료 표시)**

`vault/_NOW.md` "다음 액션" 섹션 update:
- ✅ Phase 0 완료 (vault_lint pass + 4 hook smoke + 4 agent invoke + codex Phase A/B/C 리뷰)
- ⏳ Phase 1 진행 (8 인수 소스 추출)

- [ ] **Step 2: vault/log.md 추가 entry**

```markdown
## 2026-05-03 (Phase 0 완료)
- Phase A/B/C 산출물 정합 검증 완료 — vault_lint 0 FAIL/0 WARN, 4 hook smoke pass, 4 agent invoke OK, codex 통합 리뷰 합의 NN%.
```

- [ ] **Step 3: vault_lint 재확인**

```bash
python3 tools/vault_lint.py
```

Expected: 0 FAIL / 0 WARN.

- [ ] **Step 4: Phase 0 commit**

```bash
cd /Users/jinyoon/Projects/Polaris
git init  # (필요 시 — Polaris 첫 commit)
git add vault/ .claude/ tools/ docs/ .gitignore
git commit -m "feat(polaris): Phase 0 bootstrap verified [reviewed-by: codex(N rounds)]"
```

(commit 메시지 N rounds = Task 0.4 라운드 수)

**Phase 0 Verification:**
- [ ] vault_lint --karpathy --polaris = 0 FAIL / 0 WARN
- [ ] 4 hook smoke test 4/4 PASS
- [ ] 4 agent invoke 4/4 응답
- [ ] codex Phase A/B/C 리뷰 합의 ≥ 95%
- [ ] Phase 0 commit hash 기록 (vault/log.md)

**Phase 0 Rollback:**
- vault_lint FAIL: 해당 노트 fix → re-run
- hook smoke FAIL: hook 코드 fix → re-run
- agent invoke FAIL: definition 파일 frontmatter 검증
- codex 리뷰 미합의 (3 라운드 후): Jin escalation → 운영 모델 부분 재검토 ADR

---

## Phase 1 — 8 인수 소스 추출 (FORENSIC + DEBATE 모드)

**Goal**: Codex 디베이트 식별 8 인수 소스를 Polaris vault로 정착 (INSIGHT/LESSON/ADR 노트).
**Time estimate**: 4-6 시간 (각 항목 30-45분).
**Mode**: FORENSIC (모태 read + 분석) + DEBATE (codex 합의 필요 시) + 결과는 vault-curator routing.
**Dependencies**: Phase 0 completed.

### Task 1.1: edge_calibration → INSIGHT-003 (Bayesian baseline)

**Files:**
- Read: `/Users/jinyoon/Projects/auto_invasion_mk1-main/data/edge_calibration.json`
- Create: `vault/30_knowledge/insights/INSIGHT-003-edge-calibration-baseline.md`

- [ ] **Step 1: 모태 edge_calibration 분석**

```bash
python3 -c "
import json
data = json.load(open('/Users/jinyoon/Projects/auto_invasion_mk1-main/data/edge_calibration.json'))
cells = data['cells']
print(f'Total cells: {len(cells)}')
top = sorted(cells.items(), key=lambda x: x[1]['n_samples'], reverse=True)[:10]
for k, v in top:
    wr = v['alpha'] / (v['alpha'] + v['beta'])
    print(f'{k}: n={v[\"n_samples\"]}, alpha={v[\"alpha\"]}, beta={v[\"beta\"]}, WR={wr:.3f}')
"
```

기록: top 10 cells (signal × regime × direction) + Bayesian WR.

- [ ] **Step 2: INSIGHT-003 노트 작성**

`vault/30_knowledge/insights/INSIGHT-003-edge-calibration-baseline.md`:

```markdown
---
entity_type: insight
entity_id: INSIGHT-003
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[INSIGHT-002]]", "[[_INHERIT_QUEUE]]", "[[60_alpha/_README]]"]
mode: forensic
reviewed_by: codex
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-003 — Edge Calibration Baseline (모태 인수)

> 모태 `data/edge_calibration.json` Bayesian Beta 분포 분석. Polaris 60_alpha 첫 가설 (HYPOTHESIS-001) 후보 + MTTR-alpha control band baseline.

## Evidence (직접 측정)

총 cells: NNN개 (signal × regime × direction)

### Top 10 cells by sample size
| cell | n_samples | alpha | beta | WR (Beta mean) |
|---|---|---|---|---|
| cboe_vix_term\|neutral\|0 | 254 | 133 | 123 | 0.520 |
| ... [Step 1 결과 그대로 인용]

## 활용

### Polaris 60_alpha 첫 가설 후보
- **HYPOTHESIS-001**: cboe_vix_term + neutral regime vs dual_thrust (Bayesian 비교)
  - cboe_vix_term: WR ~0.52 (n=254)
  - dual_thrust: WR ~0.24 (n=200)
  - **차이**: ~28%p (Bayesian 후험분포 비교 시 강한 signal)

### MTTR-alpha control band baseline
- 각 cell의 Beta(α,β)에서 추출한 WR이 Polaris 라이브 운영의 control band μ
- σ는 Beta variance 추출

## Risk
- 모태 학습은 멀티 거래소 + perp 환경 — Polaris SPOT-only와 fee 구조 다름
- Bayesian WR은 PnL 아님 (TP/SL ratio 가정 필요)
- INSIGHT-007 (OKX SPOT fee 수학) 적용 후 재평가 의무

## Recommendation
- [ ] Phase 2a: HYPOTHESIS-001 BACKTEST 시 fast-fail gate (fee × 2 < expected_TP)
- [ ] Phase 4: 라이브 운영 시 cell별 control band 자동 갱신

## Related
- INSIGHT-002 (MTTR-alpha 정의)
- INSIGHT-007 (OKX SPOT fee 수학) — Phase 1 인수 후
- 60_alpha/_README (워크플로)
```

- [ ] **Step 3: vault_lint 통과 확인**

```bash
python3 tools/vault_lint.py --karpathy
```

Expected: 0 FAIL.

- [ ] **Step 4: INDEX.md 갱신**

`vault/INDEX.md` Insights 표에 INSIGHT-003 한 줄 추가:
```
| [[INSIGHT-003]] | Edge calibration baseline (Bayesian) | active |
```

### Task 1.2: tournament_elo → INSIGHT-004 (전략 ELO top 5)

**Files:**
- Read: `/Users/jinyoon/Projects/auto_invasion_mk1-main/data/tournament_elo.json`
- Create: `vault/30_knowledge/insights/INSIGHT-004-tournament-elo-top-strategies.md`

- [ ] **Step 1: top 10 ELO 추출**

```bash
python3 -c "
import json
d = json.load(open('/Users/jinyoon/Projects/auto_invasion_mk1-main/data/tournament_elo.json'))
ratings = d['ratings']
top10 = sorted(ratings.items(), key=lambda x: x[1], reverse=True)[:10]
for k, v in top10:
    print(f'{v:.1f} {k}')
print(f'Round count: {d[\"round_count\"]}')
"
```

Expected top: volatility_spike (4391), crypto_specialist_g193_g338_ai (4481), crypto_specialist_g193_g350_bayes (4371) 등.

- [ ] **Step 2: INSIGHT-004 노트 작성**

```markdown
---
entity_type: insight
entity_id: INSIGHT-004
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[INSIGHT-003]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-004 — Tournament ELO Top Strategies (모태 인수)

## Evidence

`data/tournament_elo.json`: round_count=6176, 총 200+ 전략.

### Top 5 ELO
1. crypto_specialist_g193_g338_ai (4481)
2. crypto_specialist_g193_g343_struct (4205)
3. crypto_specialist_g193_g302_struct (4213)
4. volatility_spike (4391) — **Polaris 후보 (간단한 이름)**
5. crypto_specialist_g193_g350_bayes (4371)

## 활용
- Polaris HYPOTHESIS-002 후보: volatility_spike (ELO 4391) vs baseline
- 단 specialist_g193_g338_ai 같은 진화 strategy는 Polaris에 직접 이식 불가 (모태 evolver 의존)
- volatility_spike 같은 named strategy는 별도 알파로 검증

## Risk
- ELO는 모태 환경 (멀티 거래소, perp) 측정 — SPOT-only Polaris에서 재검증 필요
- specialist_* 는 evolver 산물 (Polaris는 evolver 폐기 — vault-first 운영)

## Recommendation
- [ ] HYPOTHESIS-002: volatility_spike 알파 SPOT-only 환경 검증
- [ ] 진화 strategy는 60_alpha 워크플로로 명시적 수동 진화 (자동 X)

## Related
- INSIGHT-003 (Bayesian baseline)
- 60_alpha/_README
```

- [ ] **Step 3: lint + INDEX update + commit**

### Task 1.3: regime_presets → INSIGHT-005 (regime 파라미터 base)

**Files:**
- Read: `/Users/jinyoon/Projects/auto_invasion_mk1-main/data/regime_presets.json`
- Create: `vault/30_knowledge/insights/INSIGHT-005-regime-presets-base.md`

- [ ] **Step 1: regime_presets 핵심 추출**

```bash
python3 -c "
import json
d = json.load(open('/Users/jinyoon/Projects/auto_invasion_mk1-main/data/regime_presets.json'))
print('=== scoring_thresholds ===')
for k, v in d['scoring_thresholds'].items():
    print(f'  {k}: {v}')
print('=== regimes ===')
for regime in ['RISK_ON', 'NEUTRAL', 'RISK_OFF', 'CRISIS']:
    if regime in d:
        print(f'{regime}: {d[regime]}')
"
```

- [ ] **Step 2: INSIGHT-005 노트 작성**

핵심 인용:
- VIX bands (12/17/22/30/40)
- FG bands (25/40/60/70)
- DXY bands (98/103/107)
- regime별 sizing/exit (NEUTRAL: okx_margin 10%, bep_activate 0.5; RISK_ON: 24%, no max_hold)

```markdown
---
entity_type: insight
entity_id: INSIGHT-005
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[INSIGHT-003]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
tags: [type/insight, status/active, scope/alpha, priority/p1, polaris]
---

# INSIGHT-005 — Regime Presets Base (모태 인수)

## Evidence

`data/regime_presets.json` scoring thresholds + 4 regime 파라미터.

### Scoring Thresholds (모태 검증)
- VIX: 12 (risk_on) / 17 (transition) / 22 (risk_off) / 30 (strong) / 40 (crisis)
- FG: 25 (extreme_low) / 40 (fear) / 60 (greed) / 70 (extreme_high)
- DXY: 98 (weak) / 103 (neutral) / 107 (strong)

### Regime별 (Polaris 초기값 후보)
- **RISK_ON**: okx_margin 24%, bep_activate 0.5, no max_hold
- **NEUTRAL**: okx_margin 10%, bep_activate 0.5, max_hold 1800s
- **RISK_OFF**: ... [Step 1 결과 인용]
- **CRISIS**: ... 

## 활용
- Polaris regime 분류기 초기값 (Phase 2b 컴포넌트)
- 단 fee 가정은 모태 멀티 거래소 — Polaris SPOT 재계산 필요

## Risk
- okx_margin은 perp 가정 (Polaris SPOT은 leverage 1.0)
- max_hold은 모태 cell-aware 학습값 — Polaris 초기값으로만, 학습 시작 후 갱신

## Recommendation
- [ ] Phase 2b: regime 분류기 컴포넌트 작성 시 INSIGHT-005 초기값 사용
- [ ] Phase 4: 라이브 운영 시 cell별 max_hold 학습 갱신

## Related
- INSIGHT-003 (Bayesian baseline)
- 60_alpha/_README
```

### Task 1.4: frozen_params → INSIGHT-006 (동결 경계값)

**Files:**
- Read: `/Users/jinyoon/Projects/auto_invasion_mk1-main/data/frozen_params.json`
- Create: `vault/30_knowledge/insights/INSIGHT-006-frozen-params-boundary.md`

- [ ] **Step 1: frozen_params 모든 키 인용**

```bash
python3 -c "
import json
d = json.load(open('/Users/jinyoon/Projects/auto_invasion_mk1-main/data/frozen_params.json'))
print(json.dumps(d, indent=2))
" | head -100
```

- [ ] **Step 2: INSIGHT-006 노트 작성**

frozen_params는 절대 건드리면 안 되는 경계값. 모든 키 + 값 + 의미 해석.

```markdown
---
entity_type: insight
entity_id: INSIGHT-006
auto: false
last_modified: 2026-05-03
expires: never  # 영구 — 경계값은 변경 시 ADR 필수
editable: true
back_links: ["[[principles]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-006 — Frozen Params Boundary (모태 인수)

## Evidence

`data/frozen_params.json` 동결 경계값 — 모태에서 절대 변경 금지로 표시된 값.

### 핵심 frozen 값 (인용)
[Step 1 결과 그대로 인용]

## 의미 해석
- 각 frozen 값의 trading 의미 + 변경 시 위험

## Polaris 적용
- 동일 경계값을 Polaris config 기본값으로 (Phase 2b 컴포넌트 작성 시)
- 변경 시 ADR 필수 (P3 Write Path)
- frozen_params 자체는 코드/config로, INSIGHT는 보존 이유 + 변경 protocol만 설명 (P1 — vault는 machine state 생성 X)

## Recommendation
- [ ] Phase 2b: Polaris config 기본값으로 frozen_params 인용
- [ ] config 변경 시 ADR + Jin ack 필수

## Related
- principles P1 (Authority — config는 machine SSOT)
- principles P3 (Write Path — 변경 시 ADR)
```

### Task 1.5: 모태 ADR-007/009/010/011 → Polaris ADR-006~009

**Files:**
- Read: `/Users/jinyoon/Projects/auto_invasion_mk1-main/vault/03_knowledge/decisions/ADR-007*.md`, `ADR-009*.md`, `ADR-010*.md`, `ADR-011*.md` (실제 파일명 확인 필요)
- Create: `vault/20_decisions/ADR-006-spot-trend-n-strategies.md`
- Create: `vault/20_decisions/ADR-007-paper-sizing-freedom.md`
- Create: `vault/20_decisions/ADR-008-vol-factor-proportional-fix.md`
- Create: `vault/20_decisions/ADR-009-perp-paradigm-shift-spot-only.md`

- [ ] **Step 1: 모태 ADR 파일 위치 확인**

```bash
ls /Users/jinyoon/Projects/auto_invasion_mk1-main/vault/03_knowledge/decisions/ADR-00{7,9}*.md
ls /Users/jinyoon/Projects/auto_invasion_mk1-main/vault/03_knowledge/decisions/ADR-0{10,11}*.md
```

- [ ] **Step 2: 모태 ADR-007 read + Polaris ADR-006로 적응**

모태 ADR-007 (spot trend N strategies) 핵심 read 후 Polaris ADR-006 작성:
- 모태 결정의 SPOT-only 부분만 보존
- Polaris P6 (Pure Core) + P7 (Property-based test) 통합
- frontmatter `back_links` 에 모태 출처 명시 (단 모태 ADR 직접 link X — 외부 vault)

```markdown
---
entity_type: adr
entity_id: ADR-006
auto: false
last_modified: 2026-05-03
expires: never  # superseded 또는 명시 expired까지
editable: true
back_links: ["[[ADR-001]]", "[[INSIGHT-005]]", "[[60_alpha/_README]]"]
mode: debate
reviewed_by: codex
ack_by: jin
ack_at: 2026-05-03
maturity: provisional  # Jin ack 대기
tags: [type/adr, status/provisional, scope/spot, polaris]
---

# ADR-006 — Spot Trend N Strategies (모태 ADR-007 인수)

## Status
- proposed: 2026-05-03 (모태 ADR-007 인수)
- provisional: 2026-05-03 (codex-debate Phase 1 합의 시)

## Context
모태 ADR-007 결정: SPOT trend strategy를 N개 (multi-strategy parallel) 운영.
[모태 ADR 본문 핵심 인용 + 출처: auto_invasion_mk1-main/vault/03_knowledge/decisions/ADR-007*.md]

## Decision (Polaris 적응)
- N strategy parallel — Polaris도 동일 채택
- 단 P6 Pure Core: 각 strategy는 pure function (no I/O)
- 단 P7 Property-based test: strategy 간 invariant 검증

## Consequences
[모태 결과 + Polaris 추가 고려]

## Related
- ADR-001 (SPOT-first)
- INSIGHT-005 (regime presets)
```

- [ ] **Step 3: 모태 ADR-009/010/011 → Polaris ADR-007/008/009 동일 패턴**

각 ADR 별도 step, 동일 frontmatter + 인수 패턴.

- [ ] **Step 4: vault_lint pass + INDEX update**

### Task 1.6: 모태 INSIGHT-032~035 → Polaris INSIGHT-007~010

**Files:**
- Read: 모태 `vault/03_knowledge/insights/INSIGHT-032*.md` ~ `INSIGHT-035*.md`
- Create: `vault/30_knowledge/insights/INSIGHT-007-okx-spot-fee-mathematical.md`
- Create: `vault/30_knowledge/insights/INSIGHT-008-taker-fallback-not-wired.md`
- Create: `vault/30_knowledge/insights/INSIGHT-009-fee-floor-miswiring.md`
- Create: `vault/30_knowledge/insights/INSIGHT-010-fee-unit-bug.md`

- [ ] **Step 1: 모태 INSIGHT 4 파일 read + 핵심 추출**

```bash
ls /Users/jinyoon/Projects/auto_invasion_mk1-main/vault/03_knowledge/insights/INSIGHT-03{2,3,4,5}*.md
```

각 파일 read 후 핵심 (Evidence + Root Cause + Impact + Recommendation) 추출.

- [ ] **Step 2: Polaris INSIGHT-007 (OKX SPOT fee 수학) 작성**

```markdown
---
entity_type: insight
entity_id: INSIGHT-007
auto: false
last_modified: 2026-05-03
expires: 2026-11-03
editable: true
back_links: ["[[INSIGHT-003]]", "[[60_alpha/_README]]", "[[_INHERIT_QUEUE]]"]
mode: forensic
reviewed_by: codex
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-007 — OKX SPOT Scalp 수학적 불가능 (모태 INSIGHT-032 인수)

## Evidence
[모태 INSIGHT-032 핵심 인용 — fee × 2 vs expected TP 분석]

## Root Cause
[모태 분석 — OKX SPOT fee 0.7%/side 가 scalp 전략의 expected TP 0.5% 보다 큼]

## Impact (Polaris)
- HYPOTHESIS-001 fast-fail gate 첫 적용 사례
- Polaris 60_alpha 워크플로의 BACKTEST 단계 수학적 생존성 체크 핵심 근거

## Recommendation (Polaris)
- [ ] HYPOTHESIS-001 BACKTEST: fee × 2 < expected_TP 자동 검증
- [ ] 모든 SPOT 알파 가설은 fee 수학 통과 의무

## Related
- INSIGHT-003 (Bayesian baseline)
- 60_alpha/_README (Promotion Gate)
- ADR-001 (SPOT-first 결정 — 이 INSIGHT가 SPOT-only 정당화에 기여)
```

- [ ] **Step 3: INSIGHT-008/009/010 동일 패턴 (taker fallback / fee floor / fee unit)**

각 INSIGHT 별도 step.

- [ ] **Step 4: vault_lint pass + INDEX update**

### Task 1.7: 모태 lessons #78/#47/#46/#45/#44 → Polaris LESSON-001~005

**Files:**
- Read: `/Users/jinyoon/Projects/auto_invasion_mk1-main/tasks/lessons.md` (해당 entry 인용)
- Create: `vault/30_knowledge/lessons/LESSON-001-null-cascade-prevention.md`
- Create: `vault/30_knowledge/lessons/LESSON-002-paper-vs-live-divergence.md`
- Create: `vault/30_knowledge/lessons/LESSON-003-runtime-verify-mandatory.md`
- Create: `vault/30_knowledge/lessons/LESSON-004-grep-before-guess.md`
- Create: `vault/30_knowledge/lessons/LESSON-005-consumer-grep-evidence.md`

- [ ] **Step 1: 모태 lessons.md에서 5 entry 인용**

```bash
grep -A 8 "^## #78\|^## #47\|^## #46\|^## #45\|^## #44" /Users/jinyoon/Projects/auto_invasion_mk1-main/tasks/lessons.md
```

- [ ] **Step 2: LESSON-001 (NULL cascade) 작성**

`vault/30_knowledge/lessons/LESSON-001-null-cascade-prevention.md`:
```markdown
---
entity_type: lesson
entity_id: LESSON-001
auto: false
last_modified: 2026-05-03
expires: never
editable: true
back_links: ["[[principles#P7]]", "[[INSIGHT-006]]"]
mode: meta
reviewed_by: codex
tags: [type/lesson, status/active, scope/spot, polaris]
---

# LESSON-001 — NULL Cascade Prevention (모태 lessons #78 인수)

## Trigger (모태 사건)
모태 lessons #78: Harness가 585건 중 508건을 UNKNOWN_BACKFILL 처리 → 629 NULL row → 3 downstream 연쇄 crash.

## Rule (행동 규범)
**numeric column에 NULL 입력 절대 금지. NULL 가능 column은 boundary에서 명시 coerce.**

## Why
NULL은 cascade 효과 — 한 column이 NULL이면 downstream computation이 모두 NULL/Error. 모태 lessons #78 사건이 직접 증거.

## How to Apply (Polaris)
- DB schema 설계: numeric column NOT NULL DEFAULT 0 또는 explicit None handling
- Code: numeric value read 시 `value or 0` / `Optional[float]` type 명시
- Test: P7 Property-based test로 NULL 경계값 자동 검증 (Hypothesis 라이브러리)

## Lint Enforcement
- code-implementer는 신규 numeric 함수 작성 시 NULL handling 테스트 의무
- vault_lint는 lesson 적용 검증 X (코드 패턴이라 hook으로 강제 어려움)

## Related
- principles P7 (Property-based test 우선)
- INSIGHT-006 (frozen_params boundary)
```

- [ ] **Step 3: LESSON-002~005 동일 패턴**

각 lesson 별도 step.

- [ ] **Step 4: vault_lint pass + INDEX update**

### Task 1.8: WS demo URL 위험 → INSIGHT-011

**Files:**
- Create: `vault/30_knowledge/insights/INSIGHT-011-demo-ws-url-risk.md`

- [ ] **Step 1: 모태 ws_feed_spot.py 인용 확인**

```bash
grep -n "wsuspap\|wss://" /Users/jinyoon/Projects/auto_invasion_mk1-main/invasion/spot/ws_feed_spot.py
```

Expected: `wss://wsuspap.okx.com:8443` (demo URL).

- [ ] **Step 2: INSIGHT-011 노트 작성**

```markdown
---
entity_type: insight
entity_id: INSIGHT-011
auto: false
last_modified: 2026-05-03
expires: never  # Polaris 첫 컴포넌트 작성 시 적용 후 superseded
editable: true
back_links: ["[[INSIGHT-001]]", "[[ADR-001]]", "[[40_components/_README]]"]
mode: forensic
reviewed_by: codex
tags: [type/insight, status/active, scope/spot, priority/p0, polaris]
---

# INSIGHT-011 — Demo WS URL Risk (모태 코드 인수 시 즉시 조치)

## Evidence
모태 `auto_invasion_mk1-main/invasion/spot/ws_feed_spot.py`:
```python
# wss://wsuspap.okx.com:8443  (demo URL — paper trading only)
```

Polaris ADR-001 옵션 Y 결정으로 코드 인수 X — 그러나 첫 컴포넌트 (OKX SPOT WS feed) 작성 시 같은 실수 위험.

## Root Cause
모태가 paper 모드로 시작 → demo URL 하드코딩 → live 전환 시 교체 누락 위험. config로 분리 안 됨.

## Impact (Polaris)
첫 OKX SPOT WS feed 컴포넌트 (Phase 2b) 작성 시:
- demo vs live URL을 config로 분리 (P1 Authority — config는 machine SSOT)
- 환경변수 OKX_DEMO=true|false로 분기
- pytest에서 두 모드 모두 검증

## Recommendation
- [ ] Phase 2b: OKX SPOT WS feed 컴포넌트는 URL을 config 분리
- [ ] property-based test: OKX_DEMO 모든 값에서 URL 정확히 분기
- [ ] commit pre-check: hardcoded URL grep으로 검출

## Related
- INSIGHT-001 (모태 spot 누더기)
- ADR-001 (SPOT-first fresh start)
- LESSON-002 (Paper vs Live divergence)
```

### Task 1.9: _INHERIT_QUEUE 처리 완료 표시

**Files:**
- Modify: `vault/30_knowledge/insights/_INHERIT_QUEUE.md`

- [ ] **Step 1: 처리된 항목에 ✅ 표시**

`_INHERIT_QUEUE.md`의 각 항목에 처리 완료 표시 + 결과 노트 link:

```markdown
| 파일 | 내용 | 활용 | **상태** |
|---|---|---|---|
| `auto_invasion_mk1-main/data/edge_calibration.json` | Bayesian | HYPO-001 | ✅ [[INSIGHT-003]] |
| ... [모든 항목 동일 패턴]
```

- [ ] **Step 2: 큐 만료일 reset (다 처리됐으니 archived 또는 expires 갱신)**

frontmatter `expires`를 today + 30 days로 갱신 (만약 잔여 항목 있으면), 또는 `status/expired`로 마감.

### Task 1.10: Phase 1 완료 commit + lint pass

**Files:**
- Modify: `vault/_NOW.md`, `vault/log.md`, `vault/INDEX.md`
- Run: `tools/vault_lint.py`

- [ ] **Step 1: vault/_NOW.md 갱신**

"다음 액션" 섹션 update:
- ✅ Phase 0 완료
- ✅ Phase 1 완료 (8 인수 소스 추출 — INSIGHT 9 + ADR 4 + LESSON 5 신규)
- ⏳ Phase 2 시작 (별도 plan: HYPOTHESIS-001 + 첫 컴포넌트)

- [ ] **Step 2: vault/log.md append**

```markdown
## 2026-05-03 (Phase 1 완료)
- 8 인수 소스 추출 완료 — INSIGHT-003~011 (9개), ADR-006~009 (4개), LESSON-001~005 (5개) 신규.
- _INHERIT_QUEUE 처리 완료 표시 + 만료 갱신.
```

- [ ] **Step 3: vault/INDEX.md 갱신**

신규 인덱스 추가:
- ADRs 표에 ADR-006~009 추가 (provisional 상태)
- INSIGHTs 표에 INSIGHT-003~011 추가
- Lessons 섹션 신설 (LESSON-001~005)

- [ ] **Step 4: vault_lint --report**

```bash
python3 tools/vault_lint.py --report
```

Expected: 0 FAIL / 0 WARN (orphan ≥ 2 백링크 + expires + tag taxonomy 모두 준수).

- [ ] **Step 5: codex 외부 리뷰 (Phase 1 vault 추가 콘텐츠)**

Phase 1 인수 결과 (9 INSIGHT + 4 ADR + 5 LESSON)에 대해 codex 외부 리뷰 (ADR-004):
- Task 0.4 동일 패턴
- 합의 결과는 `vault/50_runtime/codex_review_phase_1.md`로 routing

- [ ] **Step 6: Phase 1 commit**

```bash
git add vault/
git commit -m "feat(polaris): Phase 1 inherit 8 sources [reviewed-by: codex(N rounds)]"
```

**Phase 1 Verification:**
- [ ] vault/30_knowledge/insights/INSIGHT-003~011.md (9 파일) 존재
- [ ] vault/20_decisions/ADR-006~009.md (4 파일) 존재
- [ ] vault/30_knowledge/lessons/LESSON-001~005.md (5 파일) 존재
- [ ] _INHERIT_QUEUE 모든 항목 ✅ 표시
- [ ] vault_lint = 0 FAIL / 0 WARN
- [ ] codex Phase 1 리뷰 합의 ≥ 95%
- [ ] Phase 1 commit hash 기록

**Phase 1 Rollback:**
- INSIGHT/ADR/LESSON 작성 오류: 해당 노트 fix → re-lint
- codex 리뷰 미합의 (3 라운드 후): Jin escalation → 부분 인수 재검토

---

## Phase 2 — Outline (별도 plan에서 detailed)

### Phase 2a — HYPOTHESIS-001 (Bayesian 비교)
**Mode**: ALPHA
**Time estimate**: 1-2 일
**Outline**:
1. `vault/60_alpha/active/HYPOTHESIS-001-cboe-vs-dual.md` 작성
2. Fast-fail gate: fee × 2 < expected_TP (INSIGHT-007 적용)
3. BACKTEST 코드 작성 (별도 plan — TDD + property-based test)
4. PAPER (만약 BACKTEST pass) — 최소 30 trades or 7일
5. Promotion Gate (paper/live diff + sizing cap + kill criteria + rollback)
6. ADR-NNN 승격 또는 archived

**별도 plan**: `docs/superpowers/plans/2026-05-XX-polaris-hypothesis-001.md` (Phase 1 완료 후 작성)

### Phase 2b — 첫 컴포넌트 (OKX SPOT WS feed)
**Mode**: DEV
**Time estimate**: 1 주
**Outline**:
1. `vault/40_components/spot_ws_feed.md` curated note 작성
2. `src/spot/ws_feed.py` 코드 작성 (TDD + P6 Pure Core + P7 Property-based)
3. `tests/spot/test_ws_feed.py` (unit + property-based test)
4. INSIGHT-011 적용: demo/live URL config 분리
5. codex 외부 리뷰 사이클 (max 3 라운드)
6. commit `feat(spot/ws_feed): pure parser + reconnect [reviewed-by: codex(N rounds)]`

**별도 plan**: `docs/superpowers/plans/2026-05-XX-polaris-spot-ws-feed.md` (Phase 1 완료 후 작성)

---

## Phase 3 — Outline

**Goal**: Phase 2a (HYPO-001 결과) + Phase 2b (WS feed) 통합 — 첫 signal pipeline 완성.
**Mode**: ALPHA + DEV (모드 전환 explicit)
**Time estimate**: 1 주
**Outline**:
- WS feed → signal generator → cell evaluation → HYPO-001 평가 → ADR 후보
- MTTR-alpha 측정 시작 준비 (control band baseline 정착)
- 별도 plan에서 detailed.

---

## Phase 4 — Outline

**Goal**: 점진 확장 + MTTR-alpha 자동 추적 + Phase F (visualizer/dashboard) 진입 준비.
**Time estimate**: 지속적
**Outline**:
- 매주 1-2 컴포넌트 + 매주 1-2 알파 가설 cycle
- `vault/50_runtime/mttr_alpha_monthly.md` 월별 trend
- 6개월 후 vault 노트 ≤ 200 / ADR ≤ 30 / INSIGHT ≤ 50 메타 한도 모니터링
- 별도 plan들 (per quarter).

---

## End-to-End Verification (Phase 0 + 1)

1. `python3 tools/vault_lint.py --report` = 0 FAIL / 0 WARN
2. 4 hook smoke + 4 agent invoke pass
3. codex Phase A/B/C + Phase 1 리뷰 합의 ≥ 95%
4. vault/_NOW.md 갱신 (Phase 0/1 완료 + Phase 2 ready)
5. 9 INSIGHT + 4 ADR + 5 LESSON + INHERIT_QUEUE 처리 완료
6. Phase 0 + Phase 1 commit (각각 별도)
7. Phase 2 별도 plan 작성 준비 완료

---

## Self-Review

### Spec Coverage Check
- [x] Phase 0 (Constitution 검증): Task 0.1~0.5 커버
- [x] Phase 1 (8 인수 소스): Task 1.1~1.10 커버 (각 소스별 task)
- [x] Phase 2 outline: 2a/2b 분리 + 별도 plan 명시
- [x] Phase 3/4 outline: 별도 plan 명시
- [x] codex 외부 리뷰 (ADR-004): Task 0.4 + Task 1.10 Step 5
- [x] 모드 매트릭스 (DEV/ALPHA/FORENSIC/DEBATE): 각 Phase에 명시
- [x] Verification + Rollback: 각 Phase 끝에 명시

### Placeholder Scan
- [x] 모든 step에 정확한 명령 + 예상 출력 명시
- [x] Frontmatter 골격 인용 (모든 INSIGHT/ADR/LESSON 템플릿 따름)
- [x] 모태 ADR/INSIGHT 위치는 Step 1에서 ls/grep으로 찾는 형태 (정확 파일명 모를 수 있음)

### Type Consistency
- [x] INSIGHT-NNN 번호: 003~011 (Polaris 기존 001/002 다음)
- [x] ADR-NNN 번호: 006~009 (Polaris 기존 001~005 다음)
- [x] LESSON-NNN 번호: 001~005 (Polaris 신규 시작)
- [x] frontmatter 표준 동일 (operating_model + governance 정합)
