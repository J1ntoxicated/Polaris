---
type: digest
status: active
date_created: 2026-05-29
date_updated: 2026-05-29
tags: [digest, dashboard, visualization, second-brain]
---

# 2026-05-29 대시보드 부활 + 동적 시각화 + 스페이스 비주얼 + 세컨브레인

## 컨텍스트
봇(PID 96290, `data/polaris_live.sqlite`, 24h 수집) 가동 중 유지. Jin = 오토모드, "봇은 두고 대시보드 부활 + 다이나믹 비주얼 + 전부 세컨브레인 트래킹".

## 한 일 (커밋)
- **`7e4cf33`** Bayesian edge-validation P1 (`posterior.py` NIG cost-adj expectancy, online≡batch rel-err 7.6e-14, display-only) + dashboard v1 overflow "+N more" 힌트(positions/cell_top/cell_bottom/trades) + regime `max_rows` 3→4 **crisis silent-drop 수정** + `liquidate_okx_orphans.py`(dry-run 기본). 666 green, fresh-Claude 리뷰 SAFE-TO-COMMIT(0 P0/P1). builder≠reviewer 준수.
- **`5940107`** dashboard_v2 **동적 시각화**: 24h 자산 스파크라인 + DD/노출/AI예산/승률 인라인 바 게이지 + 셀 히트타일. 40행 불변, 7 신규 테스트, mypy/ruff clean.

## Root cause 해결 — "봇/대시보드 실행이 Claude 창 닫던" 정체
`start_dashboard.sh` "aggressive tty cleanup"가 `$$` 셸 tty 를 Claude 창으로 오인 → Bash 도구 실행 시 Jin 창을 닫음. **기본 OFF 반전**(opt-in `AGGRESSIVE_TTY_CLEANUP=1`) + 기본 DB → `polaris_live.sqlite`. → [[feedback_never_kill_claude_session]]. 대시보드 PID 7638 라이브 가동.

## 판단 기록
- **OKX 고아 청산 `--live` SKIP**: USDT 이미 $35,815(고갈 아님), "고아" 5건은 봇 활성 포지션 → 청산 시 라이브 데이터 오염. 봇 정지 후 클린 재시작 시에만 유효.

## 진행 중 (background)
- **스페이스 비주얼라이제이션**: `~/Projects/auto_invasion_mk1-main/tools/visualizer/` "Neural Cloud"(Canvas 2D 동심원 tier, Python stdlib http+SSE+SQLite) 발견 → Polaris `tools/visualizer/` MVP 이식 중. 엔진(sphere-render.js) 재사용 + `collect_snapshot()` 기반 `polaris_graph.py` 어댑터 신규. plan: `.claude/plans/space_viz_neural_cloud_2026-05-29.md`.

## 세컨브레인 트래킹 (이 항목)
vault = 이미 Obsidian vault(`.obsidian` 존재). 세션 작업 = 본 digest + log append + _NOW 갱신으로 추적. 후속: 주기적 vault 리뷰(Karpathy 3-ops).

## 후속
- 스페이스 비주얼 빌드 검증 → 띄우기 → 리뷰.
- P2 nit: `posterior.py` slippage floor docstring 정합 / `snapshot_sections.py` ORDER BY 주석 정정.

Refs: [[ADR-010]] · [[2026-05-28_5axis_audit]] · [[feedback_never_kill_claude_session]]
