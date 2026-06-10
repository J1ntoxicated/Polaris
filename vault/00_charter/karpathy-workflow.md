---
type: charter
status: active
date_created: 2026-05-06
tags: [charter, vault, karpathy]
---

# Karpathy LLM Wiki Workflow

Source: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

## 3-Layer

1. **Raw sources** — `data/` (immutable). 실측 가격, fill, log
2. **Wiki** (LLM-maintained) — `vault/` (1 source 가 10-15 wiki pages 갱신)
3. **Schema** — `CLAUDE.md` + `vault/.tag_taxonomy.md` + ADR

## 3 Operations

### Ingest
- Source (DB / log / external article) → wiki page
- 1 source → 10-15 page touch
- Frontmatter: `type / status / related / date_created / date_updated / source_count`
- Provenance: `^[source.md:42-58]` span citation

### Query
- Wiki search + backlink + provenance → synthesized answer
- Optional: file response back as new wiki page
- Citation 의무 (skill `polaris:querying-vault`)

### Lint
- 2-tier (light pre-commit + heavy weekly cron)
- Light: missing frontmatter / broken wiki-links / no outbound links
- Heavy: contradiction detection / stale-note (>90d untouched) / orphan
- Tool: `tools/vault_lint.py --karpathy`

## Discipline

### Wikilinks 필수
- Entities (예시 syntax — 실제 entity 등장 시 stub 노트 mint): `EURUSD` `BTC-USDT` `Capital` `ATR` `regime_quality_score`
- Decisions: [[ADR-001-vault-structure|ADR-001]] · [[ADR-002-vision|ADR-002]] · [[ADR-003-8-layer-architecture|ADR-003]] · [[ADR-004-per-gate-ai-pipeline|ADR-004]] · [[ADR-005-sizing-formula-cell-routing|ADR-005]] · [[ADR-006-cell-matrix|ADR-006]] · [[ADR-007-learner-network|ADR-007]] · [[ADR-008-7-strategies-signal-generator-role|ADR-008]]
- Strategies: [[volume_burst]] · [[tsmom]] · [[rsi_bb_pullback]] · [[spot_donchian]] · [[fx_breakout_basket]] · [[xau_indices_trend]] · [[session_breakout]]
- Components: [[layer-0-universe-discovery]] · [[layer-1-canonical-baseline]] · [[layer-2-per-gate-pipeline]] · [[layer-3-sizing-risk]] · [[layer-4-cell-matrix]] · [[layer-5-learner-network]] · [[layer-6-live-recalc]] · [[layer-7-strategy-isolation]]

### 백링크 ≥ 2
- 30_knowledge / 10_decisions / 20_strategies notes 의무
- Lint enforcement

### Append-only log
- `log.md` 1-line per work-item, NO interpretation
- Append-only by policy

### ADR creation order
- ADR-001 = Vault Structure (mint first)
- 이후 mint 순서대로 (no pre-allocation, no backfill)

### Frontmatter YAML
- hash 폐기 (git provides immutability)
- `expires` field (ADR 7d 초과 unack = warn)
- `editable: false` for charter

## Plugins (Obsidian 1.9+)
- Templater (frontmatter scaffolding + datestamp)
- Obsidian Git (auto-commit + history)
- Bases (1.9+, replaces Dataview which is dormant since 2025-04)

## Anti-pattern
- Dual vault (separate dev vault) — fragments knowledge
- Hash field — git already provides immutability
- regrets/ folder — 보수 위장 메커니즘 (Polaris 폐기)
- monthly review file — Polaris = continuous trade-driven trigger

## Cross-ref
- [[ADR-001-vault-structure|ADR-001]] Vault Structure
- `tools/vault_lint.py` (P0 build + P1 hardening 2026-05-07: inline-code strip, rejection-context recognition, `--fix` lesson scaffold)
- [[2026-05-07_p1_vault_audit]] — operational learnings (wave 1)

## Operational discipline (learned wave 1, 2026-05-07)

- **Lesson writers MUST emit frontmatter inline** at write-time. Post-hoc `--fix` is a safety net, not a primary path. (Patched in `post_trade_reflector._format_lesson_markdown`.)
- **Banned-keyword discussion** uses `Rejected-keyword scan` / `0 hits across` / `[x] No "..."` / inline-code spans — lint must skip these or every honest sweep digest fails.
- **Hub log noise** (>50% spam from start hooks) must be collapsed weekly. Source = `start_dashboard.sh` + `ignite_p1.py`. Backlog: 1-min dedup window in the hook.
- **Mature-dir backlink density** target = ≥ 2 inbound. Strategy specs achieved this only after ADR-008 + Layer-7 explicitly link to each strategy file.
