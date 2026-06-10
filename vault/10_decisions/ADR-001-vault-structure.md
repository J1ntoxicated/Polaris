---
type: ADR
adr_id: ADR-001
aliases: [ADR-001]
status: active
date_created: 2026-05-06
date_updated: 2026-05-06
tags: [adr, vault, structure]
related: [[karpathy-workflow]], [[INDEX]]
reviewed_by: codex+jin (round 2 T6, round 3 D3 + Jin sign-off)
---

# ADR-001 — Vault Structure

## Decision

**6 top-level dirs + 3 root files**:

```
vault/
├── _NOW.md                 (Tier 0, mandatory read, hybrid auto+manual)
├── INDEX.md                (catalog, auto-generated)
├── log.md                  (append-only 1-line, NO interpretation)
├── 00_charter/             (constitution: north-star, aggressive-bias, vision, conventions, karpathy)
├── 10_decisions/           (ADRs, creation order numbering)
├── 20_strategies/          (per-strategy spec, 1 file per strategy)
├── 30_components/          (per-layer code module docs)
├── 40_ops/                 (daily/, incidents/, digests/, lever_changes/)
├── 50_research/            (forensic/, debates/, lessons/)
├── _attachments/           (binaries)
├── ._meta/                 (hidden auto-generated artifacts: lint cache, INDEX cache)
└── .templates/             (5 templates: ADR, INSIGHT, STRATEGY, COMPONENT, LESSON)
```

## ADR numbering rule
- ADR-001 = vault structure (this doc, mint first)
- 이후 ADRs minted in CREATION order, never backfilled by narrative chronology
- No pre-allocation

## regrets/ 폐기
- `40_ops/regrets/` 디렉토리 = 보수 위장 메커니즘 (Goodhart 우려)
- 대체: B' continuous lever_change log + D forensic on checkpoint + C winner-only ELO
- See [[ADR-002-vision|ADR-002]] §regrets-replacement

## Frontmatter YAML
```yaml
---
type: ADR | strategy | component | runtime | research | charter
status: active | superseded | abandoned
related: ...
date_created: 2026-05-06
date_updated: 2026-05-06
tags: [...]
reviewed_by: codex|jin|none
---
```
- `hash` field 폐기 (git provides immutability)
- `expires` field (ADR 7d 초과 unack = warn)

## Lint (2-tier)
- **Light** (pre-commit): missing frontmatter / broken wiki-links / no outbound links / no inbound links in mature dirs
- **Heavy** (weekly cron): contradiction detection / stale-note (>90d untouched + status:active) / orphan

## Bases (Obsidian 1.9+)
- One Base per top-level dir
- Replaces Dataview (dormant since 2025-04)
- Example: `Active ADRs` base = `from "10_decisions" where status = "active"`

## Provenance
- Daily logs cite raw data: `^[ingestion-id:row-range]`
- ADRs cite codex round: `^[t6-r2-codex]` or `^[round-3-d1-codex]`

## Sources / Round
- Round 2 T6 codex consensus (`/tmp/polaris_debate_round2/t6_consensus.md`)
- Round 3 D3 lever_change replacement (continuous trade-driven)
- Jin sign-off (regrets/ 폐기)
