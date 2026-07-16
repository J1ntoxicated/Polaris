"""ANSI palette + visual helpers for Polaris dashboard v1.

Polaris design system:
- BORDER  = grey42 (256-color 242, "톤다운 회색" — Jin mandate)
- POSITIVE = green32 / NEGATIVE = red31 / NEUTRAL = grey
- HIGHLIGHT = bold cyan / WARNING = yellow / ERROR = red

Cursor home (``\\x1b[H``) only — fixed 55-row grid means we never need
clear-down (``\\x1b[J``) at runtime, so flicker stays at zero.
"""

from __future__ import annotations

import re
import sys
import unicodedata

_TTY = sys.stdout.isatty()


def _esc(code: str) -> str:
    return f"\x1b[{code}m" if _TTY else ""


# ---------------------------------------------------------------------------
# Core ANSI codes
# ---------------------------------------------------------------------------

RESET = _esc("0")
BOLD = _esc("1")
DIM = _esc("2")
ITALIC = _esc("3")
UNDERLINE = _esc("4")

# 256-color palette (eye-friendly, terminal-portable)
BORDER = _esc("38;5;242")     # grey42 — Jin "톤다운 회색" mandate
POSITIVE = _esc("38;5;114")   # pastel green
NEGATIVE = _esc("38;5;174")   # pastel red
WARNING = _esc("38;5;186")    # pastel yellow
HIGHLIGHT = _esc("38;5;117")  # pastel cyan (header)
INFO = _esc("38;5;110")       # pastel blue
NEUTRAL = _esc("38;5;248")    # light grey
MUTED = _esc("38;5;240")      # darker grey

# Box drawing
HLINE = "─"   # ─
VLINE = "│"   # │

# Sparkline / bar characters
SPARKS = " ▁▂▃▄▅▆▇█"
BLOCK = "█"   # █
SHADE = "░"   # ░
MED = "▒"     # ▒
DARK = "▓"    # ▓

# Cursor
HOME = "\x1b[H" if _TTY else ""
CLEAR_DOWN = "\x1b[J" if _TTY else ""
HIDE_CURSOR = "\x1b[?25l" if _TTY else ""
SHOW_CURSOR = "\x1b[?25h" if _TTY else ""

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# String width helpers (ANSI- and CJK-aware)
# ---------------------------------------------------------------------------


def vlen(s: str) -> int:
    """Visual length — strips ANSI, counts CJK as 2 cells."""
    stripped = _ANSI_RE.sub("", s)
    width = 0
    for ch in stripped:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(s: str, width: int) -> str:
    """Left-align s to exactly `width` visible chars (ANSI-safe)."""
    vl = vlen(s)
    if vl <= width:
        spaces = " " * (width - vl)
        # RESET is "" in non-TTY mode → would slice s[:0] and lose content.
        if spaces and RESET and s.endswith(RESET):
            return s[: -len(RESET)] + spaces + RESET
        return s + spaces
    return _truncate(s, width)


def _truncate(s: str, width: int) -> str:
    out: list[str] = []
    vis = 0
    i = 0
    while i < len(s) and vis < width:
        if s[i] == "\x1b" and i + 1 < len(s) and s[i + 1] == "[":
            j = i + 2
            while j < len(s) and s[j] not in "mGHJK":
                j += 1
            out.append(s[i : j + 1])
            i = j + 1
        else:
            cw = 2 if unicodedata.east_asian_width(s[i]) in ("W", "F") else 1
            if vis + cw > width:
                break
            out.append(s[i])
            vis += cw
            i += 1
    return "".join(out) + RESET


# ---------------------------------------------------------------------------
# Color wrappers
# ---------------------------------------------------------------------------


def color(text: str, code: str) -> str:
    """Wrap text in ANSI code, reset at end."""
    if not _TTY or not code:
        return str(text)
    return f"{code}{text}{RESET}"


def sparkline(data: list[float], width: int = 60) -> str:
    """Build a sparkline of given visual width from numeric data.

    Downsamples to `width` buckets (mean per bucket); empty / single-value
    series degrade gracefully.
    """
    if not data:
        return SHADE * width
    cleaned = [v for v in data if v is not None]
    if not cleaned:
        return SHADE * width
    if len(cleaned) > width:
        # Bucket-mean downsample
        bucket = len(cleaned) / width
        downsampled: list[float] = []
        for i in range(width):
            i_lo = int(i * bucket)
            i_hi = max(i_lo + 1, int((i + 1) * bucket))
            chunk = cleaned[i_lo:i_hi]
            downsampled.append(sum(chunk) / len(chunk))
        cleaned = downsampled
    elif len(cleaned) < width:
        # Pad left with first value
        cleaned = [cleaned[0]] * (width - len(cleaned)) + cleaned
    v_lo = min(cleaned)
    v_hi = max(cleaned)
    rng = v_hi - v_lo if v_hi != v_lo else 1.0
    return "".join(
        SPARKS[min(8, max(1, int((v - v_lo) / rng * 7) + 1))] for v in cleaned
    )


def bar(pct: float, width: int = 10, *, fill_color: str = POSITIVE) -> str:
    """Filled bar: 0–100 → █░ at given width."""
    pct = max(0.0, min(100.0, pct))
    filled = int(pct / 100.0 * width)
    return color(BLOCK * filled, fill_color) + color(SHADE * (width - filled), MUTED)
