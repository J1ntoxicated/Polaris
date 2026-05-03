#!/usr/bin/env bash
# vault symlinks — 기존 markdown 자산을 vault 에 흡수 (단방향 sync via symlink).
# Idempotent — 재실행 안전. broken symlink 자동 정리.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VAULT="$PROJECT_ROOT/vault"
HOME_MEMORY="$HOME/.claude/projects/-Users-jinyoon-Projects-auto-invasion-mk1-main/memory"

# Helper: ln -sf only if source exists
link_if_exists() {
    local src="$1"
    local dst="$2"
    if [[ -e "$src" ]]; then
        ln -sf "$src" "$dst"
    else
        echo "WARN: source missing: $src"
    fi
}

# 00_north_star (project-relative)
link_if_exists "$PROJECT_ROOT/.claude/docs/north_star.md" "$VAULT/00_north_star/north_star.md"
link_if_exists "$PROJECT_ROOT/.claude/docs/canonical_cell_matrix.md" "$VAULT/00_north_star/canonical_cell_matrix.md"
link_if_exists "$PROJECT_ROOT/.claude/docs/canonical_files.md" "$VAULT/00_north_star/canonical_files.md"
link_if_exists "$PROJECT_ROOT/docs/GOVERNANCE.md" "$VAULT/00_north_star/governance.md"

# 10_lessons (project-relative)
link_if_exists "$PROJECT_ROOT/tasks/lessons.md" "$VAULT/10_lessons/lessons.md"
link_if_exists "$PROJECT_ROOT/tasks/harness_items.md" "$VAULT/10_lessons/harness_items.md"

# 19+ feedback_* (HOME-relative, env var)
if [[ -d "$HOME_MEMORY" ]]; then
    for f in "$HOME_MEMORY"/feedback_*.md; do
        [[ -e "$f" ]] && ln -sf "$f" "$VAULT/10_lessons/feedback/$(basename "$f")"
    done
    link_if_exists "$HOME_MEMORY/MEMORY.md" "$VAULT/10_lessons/MEMORY.md"
else
    echo "WARN: $HOME_MEMORY not found — feedback symlinks skipped"
fi

# 20_architecture
link_if_exists "$PROJECT_ROOT/docs/ARCHITECTURE.md" "$VAULT/20_architecture/ARCHITECTURE.md"
link_if_exists "$PROJECT_ROOT/docs/STRATEGY_INVENTORY.md" "$VAULT/20_architecture/STRATEGY_INVENTORY.md"
link_if_exists "$PROJECT_ROOT/docs/gate_matrix.md" "$VAULT/20_architecture/gate_matrix.md"
link_if_exists "$PROJECT_ROOT/.claude/docs/alert_routing.md" "$VAULT/20_architecture/alert_routing.md"

# 20 agents
for f in "$PROJECT_ROOT"/.claude/agents/*.md; do
    [[ -e "$f" ]] && ln -sf "$f" "$VAULT/20_architecture/agents/$(basename "$f")"
done

# 20_architecture/skills (slash commands = skill files)
mkdir -p "$VAULT/20_architecture/skills"
for f in "$PROJECT_ROOT"/.claude/commands/*.md; do
    [[ -e "$f" ]] && ln -sf "$f" "$VAULT/20_architecture/skills/$(basename "$f")"
done

# 20_architecture — comprehensive .claude/docs/ (Jin: 모든 md 통합 mandate)
for f in "$PROJECT_ROOT"/.claude/docs/*.md; do
    base="$(basename "$f")"
    # Skip already-symlinked critical docs (avoid duplicate links)
    case "$base" in
        north_star.md|canonical_cell_matrix.md|canonical_files.md|alert_routing.md) continue;;
    esac
    ln -sf "$f" "$VAULT/20_architecture/$base"
done

# 20_architecture — comprehensive docs/ (project-level)
for f in "$PROJECT_ROOT"/docs/*.md; do
    base="$(basename "$f")"
    # Skip already-symlinked
    case "$base" in
        ARCHITECTURE.md|GOVERNANCE.md|STRATEGY_INVENTORY.md|gate_matrix.md) continue;;
    esac
    # Skip stale MODULE_REVIEW (per plan)
    [[ "$base" == MODULE_REVIEW_* ]] && continue
    ln -sf "$f" "$VAULT/20_architecture/$base"
done

# 10_lessons — comprehensive tasks/ (active analysis docs only)
for f in "$PROJECT_ROOT"/tasks/T13_START_HERE.md \
         "$PROJECT_ROOT"/tasks/audit_log.md \
         "$PROJECT_ROOT"/tasks/audit_t13_code_state.md \
         "$PROJECT_ROOT"/tasks/audit_t13_plan_vs_code.md \
         "$PROJECT_ROOT"/tasks/audit_t13_units.md \
         "$PROJECT_ROOT"/tasks/design_t13_stage2.md \
         "$PROJECT_ROOT"/tasks/forensic_t13_d115_to_9.md \
         "$PROJECT_ROOT"/tasks/forensic_t13_d14_drop_sites.md \
         "$PROJECT_ROOT"/tasks/harness_audit_t13.md \
         "$PROJECT_ROOT"/tasks/harness_hourly_log.md \
         "$PROJECT_ROOT"/tasks/next_plan_t14_performance_classification.md \
         "$PROJECT_ROOT"/tasks/observation_log_t12.md \
         "$PROJECT_ROOT"/tasks/anomaly_snapshot_t12.md ; do
    [[ -e "$f" ]] && ln -sf "$f" "$VAULT/10_lessons/$(basename "$f")"
done

# 10_lessons/plans — T13 plan iterations (current + history)
mkdir -p "$VAULT/10_lessons/plans"
for f in "$PROJECT_ROOT"/tasks/plan_*.md "$PROJECT_ROOT"/tasks/prep_*.md "$PROJECT_ROOT"/tasks/t13_*.md "$PROJECT_ROOT"/tasks/scale_*.md "$PROJECT_ROOT"/tasks/self_adapt_design.md ; do
    [[ -e "$f" ]] && ln -sf "$f" "$VAULT/10_lessons/plans/$(basename "$f")"
done

# Cleanup broken symlinks (idempotent — 재실행 시 stale links 제거)
find "$VAULT" -type l ! -exec test -e {} \; -delete 2>/dev/null || true

echo "[symlinks] done"
echo "  Total symlinks: $(find "$VAULT" -type l | wc -l | tr -d ' ')"
