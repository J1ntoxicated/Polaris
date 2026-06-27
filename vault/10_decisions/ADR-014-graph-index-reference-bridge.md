---
type: ADR
adr_id: ADR-014
aliases: [ADR-014]
status: active
date_created: 2026-06-27
tags: [adr, vault, graph-index, codebase-memory, reference-bridge, code-anchors, dev-time]
related: [[ADR-001-vault-structure|ADR-001]], [[ADR-003-8-layer-architecture|ADR-003]], [[harness-collab-protocol]]
reviewed_by: design workflow (graph-bridge synthesis, builder≠reviewer) + Jin (위임 "내가 쓸 도구니 합리적으로 빌드")
---

# ADR-014 — Graph Index ↔ Vault REFERENCE-BRIDGE

## Decision
Adopt a **reference-bridge**, NOT data-merge, between the vault and the codebase graph index (`codebase-memory` MCP, machine-extracted from code: nodes/functions). The bridge is three thin links, never a copy:
1. **Vault→code anchor** (one-way): notes may name code symbols via `code_anchors` (below).
2. **Agent routing**: "graph로 LOCATE, vault로 JUDGE" (see [[harness-collab-protocol]]).
3. **Graph→vault drift** (candidate-only): graph deltas surface as drift *signals*, never auto-writes.

## Cache-vs-source asymmetry (충돌 시 vault 승)
- **Vault = SOURCE / SSOT** — WHY · decisions · lessons · mandates · human-authored, 대체불가.
- **Graph = CACHE** — mechanically re-derivable from code; disposable, READ-MOSTLY; regenerate via `index_repository`.
- On any conflict the **vault always wins**. The graph never overrides a vault decision; it only helps *locate* the code a decision concerns.

## code_anchors convention (P2)
Optional frontmatter field, list of fully-qualified symbol names a note concerns:
```yaml
code_anchors: [polaris.gates.g1_universe.run_tick, polaris.sizing.t4.continuous_scalar]
```
- **점진 채움** — backfill 불필요; add anchors only when a note is about specific code. Absence is fine.
- **정방향** — anchor = seed for `trace_path` / `search_graph` (decision → its live code).
- **역방향** — `grep -r "code_anchors:" vault/` maps code symbols back to the notes that govern them.
- Convention lives HERE only. No `vault/.templates/` exists, so creating a speculative template dir is rejected (simplicity); this ADR record suffices.

## Dev-time-only (trade path 무관)
The graph is **dev-time tooling**: the running bot never touches it. It is irrelevant to the trade path, the 9-stack sizing chain, the execution rail, and `flow_not_block` — it neither blocks nor sizes nor routes any order. When in doubt about a graph claim, re-confirm against the real file via `get_code_snippet` / direct read.

## 🚨 정직 기록 (honest limits)
- `manage_adr` is **blob-only**: one project-wide ARCHITECTURE blob (6 sections), NOT a per-ADR node. Mirroring each vault ADR into the graph as its own node is **impossible** — so data-merge was discarded; reference is the whole design.
- Graph's only write slot = that single `manage_adr` blob; `query_graph` is read-only Cypher.

## Scope (this build = text only)
Built: harness-protocol revision (P1) · this ADR (P5) · code_anchors convention (P2). **Held**: `tools/vault_graph_sync.py` (P3) + drift-audit (P4) — both need the graph live, which requires `.mcp.json` registration (**P0 = Jin action**, self-mod guard). No code shipped here.

## Sources
Graph-bridge design workflow synthesis. Honest-limits verified against the live MCP tool surface (14 tools; `manage_adr` blob-only confirmed). Generalizes [[ADR-001-vault-structure|ADR-001]] vault SSOT to a cache-vs-source boundary.
