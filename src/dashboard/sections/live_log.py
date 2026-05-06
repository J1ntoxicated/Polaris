"""Polaris Intel — LIVE LOG section.

Legacy intel_live_log.py 패턴: ALL log lines stream. Latest at bottom (ticker-tape).
Grey default, important colored. SKIP_PATTERNS only filter (no allowlist).

User mandate: "모든 로그 다 출력이 원칙" — only filter clearly internal noise.
"""
from __future__ import annotations

import re
from pathlib import Path

from src.dashboard.ansi import (
    B, P_GRN, P_RED, P_YLW, P_CYN, P_WHT, P_GRY, P_DIM, P_MAG, P_BLU,
    POLARIS_BLUE,
    c, pad, hline,
)


REALTIME_ERR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "paper" / "realtime.err"


# Skip patterns — clearly internal noise only (per legacy principle)
_SKIP_PATTERNS = (
    "[FUNDING-POLL]",  # 60s heartbeat — replace with derived metric in header
)

# Critical / colored event patterns
_REAL_ERROR_RE = re.compile(
    r"\b(Traceback|NameError|AttributeError|ImportError|TypeError|"
    r"KeyError|ValueError|CRITICAL|FATAL|RuntimeError|Exception)\b"
)
_REJECT_RE = re.compile(r"REJECT|FAIL|BLOCK|SKIP")


def _classify(line: str) -> str:
    """Classify log line → ANSI color string."""
    up = line.upper()
    # Critical errors
    if _REAL_ERROR_RE.search(line):
        return P_RED + B
    # Trade events
    if "[OPEN]" in line:
        return P_YLW + B
    if "[CLOSE]" in line and "net=+" in line:
        return P_GRN + B
    if "[CLOSE]" in line and "net=-" in line:
        return P_RED + B
    if "[CLOSE]" in line:
        return P_GRN
    # PM events
    if "[PM-ROTATE]" in line or "[PM-ADD]" in line:
        return P_CYN + B
    if "[PM-CLOSE]" in line:
        return P_MAG
    if "[PM-CYCLE]" in line:
        return P_CYN
    if "[POLICY-UPDATE]" in line:
        return P_YLW
    # Portfolio
    if "[PORTFOLIO-HALT]" in line:
        return P_RED + B
    if "[PORTFOLIO-SNAP]" in line:
        return P_BLU
    if "[PORTFOLIO]" in line and "initialized" in line:
        return P_CYN + B
    # Broker
    if "[BROKER]" in line:
        return P_CYN + B
    # Entry signals
    if "[NFI-DIP]" in line and "ENTRY" in up:
        return P_GRN + B
    if "[CARRY-ENTER]" in line:
        return P_GRN + B
    if "[LIQ-CASCADE]" in line and "ENTRY" in up:
        return P_GRN + B
    if "[DYN-SIZE]" in line:
        return P_WHT
    # Skips / blocks (less interesting)
    if "[SPREAD-SKIP]" in line or "[REGIME-BLOCK]" in line or "[LIQ-SKIP]" in line:
        return P_DIM
    if _REJECT_RE.search(up):
        return P_DIM
    # HOLD logs (verbose but kept per "ALL logs" principle)
    if "-HOLD]" in line:
        return P_DIM
    # Connection / WS
    if "WS error" in line or "TimeoutError" in line:
        return P_YLW
    if "subscribe" in line or "subscribed" in line:
        return P_BLU
    return P_GRY


def render(W: int, n: int = 19) -> list[str]:
    """Live log panel — latest at bottom, color-coded."""
    lines: list[str] = [hline("LIVE LOG — ALL EVENTS", W, POLARIS_BLUE)]
    data_n = n - 1

    if not REALTIME_ERR.exists():
        lines.append(pad(c("    (realtime.err not found)", P_DIM), W))
        while len(lines) < n:
            lines.append(pad("", W))
        return lines[:n]

    try:
        # Read tail efficiently
        all_lines = REALTIME_ERR.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        all_lines = []

    # Filter from newest backward
    filtered: list[str] = []
    for ln in reversed(all_lines):
        if any(p in ln for p in _SKIP_PATTERNS):
            continue
        ln = ln.strip()
        if not ln:
            continue
        filtered.append(ln)
        if len(filtered) >= data_n:
            break

    # display order: oldest top → newest bottom (ticker-tape)
    display = list(reversed(filtered))

    for ln in display:
        # Strip standard log prefix to maximize signal width
        # "2026-05-06 14:35:17,809 [INFO] [TAG] message" → "14:35:17 [TAG] message"
        m = re.match(
            r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})[,.]?\d* \[(\w+)\] (.+)$",
            ln,
        )
        if m:
            ts = m.group(2)
            level = m.group(3)
            msg = m.group(4)
            level_color = (
                P_RED + B if level in ("ERROR", "CRITICAL")
                else P_YLW + B if level == "WARNING"
                else P_DIM
            )
            line_color = _classify(ln)
            disp = f"  {c(ts, P_DIM)} {c(level[:4], level_color)} {c(msg, line_color)}"
        else:
            disp = f"  {c(ln, _classify(ln))}"

        # Truncate to W with safety margin
        from src.dashboard.ansi import vlen
        if vlen(disp) > W - 2:
            # Walk-truncate ANSI-aware
            from src.dashboard.ansi import pad as _pad
            disp = _pad(disp, W - 2) + "…"
        lines.append(pad(disp, W))

    while len(lines) < n:
        lines.append(pad("", W))
    return lines[:n]
