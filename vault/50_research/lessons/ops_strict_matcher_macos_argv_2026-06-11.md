---
type: lesson
status: active
date_created: 2026-06-11
tags: [ops, watchdog, process-matching, macos, review, lesson]
related: [[ops-automation]], [[zombie_close_session_gate_wrong_predicate_2026-06-04]]
---

# 리뷰 처방도 실측 검증: macOS ps argv[0]은 `.venv/bin/python`이 아니다

ops 자동화 적대 리뷰가 "stop/adopt 매처에 argv[0]=`.venv/bin/python` 요구"를
blocker 픽스로 처방했으나, **실측 ps 출력은 반대를 증명**:

```
/opt/homebrew/Cellar/python@3.13/.../Python.app/Contents/MacOS/Python -m polaris.scripts.ignite_p1 --paper ...
```

macOS venv python은 stub이 framework binary를 exec → ps의 argv[0]은 절대
`.venv/bin/python`을 포함하지 않는다. 처방 그대로 구현했다면 watchdog이 살아있는
봇을 영원히 "없음"으로 판정(silent down) — **리뷰어가 막으려던 바로 그 실패 모드**.

## 채택한 매처 (3중 게이트, 처방의 의도는 보존)
1. argv[0] basename에 `python` (대소문자 무관 — `Python` 매치)
2. 정확한 `-m polaris.scripts.ignite_p1` **인접 토큰쌍** (경로형
   `polaris/scripts/ignite_p1.py`를 든 ruff/grep/에디터는 불일치)
3. `--paper` 토큰 존재

`tests/ops/test_botctl_start.py`에 무고 프로세스 7종 비매치 + 실측 argv 매치를 고정.

## 교훈
- 적대 리뷰 blocker의 **방향**(과광폭 술어 → 오살 체인)은 옳아도 **처방**은 틀릴 수
  있다. predicate 류 픽스는 적용 전 실제 시스템 출력 1회 실측이 의무
  ([[zombie_close_session_gate_wrong_predicate_2026-06-04]]와 동형: 술어가 약속과
  다른 값을 보는 버그).
- 프로세스 매칭은 "내가 띄운 커맨드"가 아니라 "OS가 보고하는 커맨드"에 맞춘다.
