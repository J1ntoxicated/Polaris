"""ANSI terminal utilities — colors, padding, visual elements.

Palette:
  GRN / GRN+B : profit, long, positive
  RED / RED+B : loss, short, danger
  YLW / YLW+B : warning, transition
  CYN / CYN+B : signal, AI, info
  MAG / MAG+B : YOLO, regime, special
  BLU / BLU+B : CAP platform
  WHT / WHT+B : key numbers, primary text
  D           : secondary / dim text
  ORG         : borders, dividers (256-color orange)

Modern UI helpers:
  metric_box                         — inline bordered metric cell
  progress_bar                       — labeled progress bar with fill
  status_badge_v2                    — icon + colored badge
  gradient_bar                       — multi-color gradient fill bar
  pnl_inline_bar                     — centered pnl inline bar (± axis)
  dim_row                            — alternate row dimming helper
  bg_alert(row, level)               — apply bg color to whole row (red/yellow/green/orange)
  blink(s)                           — slow-blink ANSI wrap (TTY-safe)
  dim_conditional(s, condition)      — dim if condition else passthrough
"""
import re
import sys

_TTY = sys.stdout.isatty()

def _e(code):
    return f"\033[{code}m" if _TTY else ""

R   = _e("0")
B   = _e("1")
D   = _e("2")
I   = _e("3")   # italic
U   = _e("4")   # underline
RED = _e("91")
GRN = _e("92")
YLW = _e("93")
BLU = _e("94")
MAG = _e("95")
CYN = _e("96")
WHT = _e("97")

# 256-color extras
ORG   = _e("38;5;208")   # orange — legacy accents
ORG_D = _e("38;5;24")    # Polaris theme — aliased to P_NAVY (was "38;5;130" dark orange)
GRY   = _e("38;5;240")   # dark grey

# Pastel palette (256-color, eye-friendly)
P_GRN  = _e("38;5;114")   # pastel green (profit, long)
P_RED  = _e("38;5;174")   # pastel red (loss, short)
P_CYN  = _e("38;5;117")   # pastel cyan (signal, AI)
P_YLW  = _e("38;5;186")   # pastel yellow (warning, transition)
P_MAG  = _e("38;5;183")   # pastel magenta (regime, strategy)
P_BLU  = _e("38;5;110")   # pastel blue (CAP, info)
P_ORG  = _e("38;5;216")   # pastel orange (accent, borders)
P_WHT  = _e("38;5;253")   # bright white (key numbers)
P_GRY  = _e("38;5;248")   # light grey (secondary text)
P_DIM  = _e("38;5;242")   # dimmer (background elements)
GHOST  = _e("38;5;241")   # ghost (faint dividers)
P_NAVY = _e("38;5;24")    # Polaris brand accent — deep navy (border / divider)
POLARIS_BLUE = _e("38;5;67")   # Polaris section divider — steel blue (toned-down, eye-friendly)

# Background colors
BG_R   = _e("41")
BG_G   = _e("42")
BG_Y   = _e("43")
BG_BLU = _e("44")
BG_MAG = _e("45")
BG_CYN = _e("46")
BG_W   = _e("47")
BG_BLK = _e("40")

# Box drawing
HL  = "\u2500"   # ─
VL  = "\u2502"   # │
TL  = "\u250c"   # ┌
TR  = "\u2510"   # ┐
BL  = "\u2514"   # └
BR  = "\u2518"   # ┘
LT  = "\u251c"   # ├
RT  = "\u2524"   # ┤
TT  = "\u252c"   # ┬
BT  = "\u2534"   # ┴
CR  = "\u253c"   # ┼
DHL = "\u2550"   # ═ double horizontal
DVL = "\u2551"   # ║ double vertical
DTL = "\u2554"   # ╔
DTR = "\u2557"   # ╗
DBL = "\u255a"   # ╚
DBR = "\u255d"   # ╝

# Spark / bar characters
SPARKS = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
BLOCK  = "\u2588"   # █
SHADE  = "\u2591"   # ░
MED    = "\u2592"   # ▒
DARK   = "\u2593"   # ▓

# Arrows / indicators
UP_ARR   = "\u25b2"   # ▲
DOWN_ARR = "\u25bc"   # ▼
BULL     = "\u25cf"   # ●
DASH_DOT = "\u00b7"   # ·

# Polaris brand symbols (★ ☆ ✦ ✧ — BMP, vlen() safe = 1 char each)
STAR     = "\u2605"   # ★ active / LIT indicator
STAR_O   = "\u2606"   # ☆ idle / dim indicator
STAR_4   = "\u2726"   # ✦ accent / section divider
STAR_4O  = "\u2727"   # ✧ offline / DARK / extinguished

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


# ── Core utilities ──────────────────────────────────────────────────────────

def vlen(s: str) -> int:
    """Visual length — strips ANSI escape codes, CJK aware (W/F = 2 cells).

    한글/한자/일본어 등 east_asian_width='W' or 'F' 문자는 터미널에서 2 cells 차지.
    `len()` 단순 카운트는 overflow → wrap → 시각적 blank row 발생시킴.
    """
    import unicodedata
    stripped = _ANSI_RE.sub('', s)
    width = 0
    for ch in stripped:
        width += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    return width


def pad(s: str, w: int) -> str:
    """Left-align s, padded to exactly w visible chars. ANSI-safe truncation.

    Spaces are inserted before any trailing ANSI reset so strip()+rstrip() sees
    the full visible width (trailing spaces are not stripped away by rstrip when
    they precede the reset code).
    """
    vl = vlen(s)
    if vl <= w:
        spaces = " " * (w - vl)
        # Insert padding before trailing ANSI reset so rstrip() doesn't eat it.
        _RESET = "\033[0m"
        if spaces and s.endswith(_RESET):
            return s[: -len(_RESET)] + spaces + _RESET
        return s + spaces
    # ANSI-aware truncation: walk chars, skip escape sequences, CJK-aware width
    import unicodedata
    out = []
    vis = 0
    i = 0
    while i < len(s) and vis < w:
        if s[i] == '\033' and i + 1 < len(s) and s[i + 1] == '[':
            # ANSI escape — copy entire sequence (zero visual width)
            j = i + 2
            while j < len(s) and s[j] not in 'mGHJK':
                j += 1
            out.append(s[i:j + 1])
            i = j + 1
        else:
            cw = 2 if unicodedata.east_asian_width(s[i]) in ('W', 'F') else 1
            if vis + cw > w:
                break  # don't partially render a CJK char
            out.append(s[i])
            vis += cw
            i += 1
    result = ''.join(out) + '\033[0m'  # reset at end
    return result


def rpad(s: str, w: int) -> str:
    """Right-align s to w visible chars. No truncation."""
    vl = vlen(s)
    if vl >= w:
        return s
    return " " * (w - vl) + s


def center(s: str, w: int, fill: str = " ") -> str:
    """Center s in w chars."""
    vl = vlen(s)
    if vl >= w:
        return s
    left_pad = (w - vl) // 2
    right_pad = w - vl - left_pad
    return fill * left_pad + s + fill * right_pad


def c(t, color: str) -> str:
    """Wrap text in color, reset at end."""
    return f"{color}{t}{R}" if _TTY else str(t)


def badge(text: str, bg_color: str) -> str:
    """Status badge with background color, e.g. badge('LIVE', BG_G+B+WHT)."""
    return c(f" {text} ", bg_color)


# ── Color selectors ─────────────────────────────────────────────────────────

def pnl_c(v: float) -> str:
    return GRN + B if v > 0 else RED + B if v < 0 else D


def wr_c(v: float) -> str:
    return GRN + B if v >= 50 else YLW if v >= 45 else RED + B


def pf_c(v: float) -> str:
    return GRN + B if v >= 1.0 else YLW if v >= 0.8 else RED + B


def regime_c(regime: str) -> str:
    return {
        "RISK_ON": GRN + B,
        "RISK_OFF": RED + B,
        "NEUTRAL": YLW + B,
        "TRANSITION": CYN + B,
        "CRISIS": RED + B,
    }.get(regime.upper() if regime else "", D)


# ── Visual elements ──────────────────────────────────────────────────────────

def spark(data: list, width: int = 20) -> str:
    """Sparkline from data list."""
    if not data:
        return SHADE * min(width, 8)
    data = [v for v in data if v is not None]
    if not data:
        return SHADE * min(width, 8)
    data = data[-width:]
    lo, hi = min(data), max(data)
    rng = hi - lo if hi != lo else 1
    return "".join(SPARKS[min(8, max(1, int((v - lo) / rng * 7) + 1))] for v in data)


def bar(pct: float, width: int = 10) -> str:
    """Filled bar: 0-100 -> █░."""
    filled = max(0, min(width, int(pct / 100 * width)))
    return BLOCK * filled + SHADE * (width - filled)


def threat_bar(pnl_pct: float, age_s: float, stop_pct: float = -1.5) -> str:
    """5-char threat indicator. More filled = more dangerous."""
    threat = 0
    if pnl_pct < 0:
        threat += 1
    if pnl_pct < -0.5:
        threat += 1
    if pnl_pct < -1.0:
        threat += 1
    if age_s > 1800:
        threat += 1
    if age_s > 3600:
        threat += 1
    threat = min(5, threat)
    chars = DARK * threat + SHADE * (5 - threat)
    if threat >= 4:
        return c(chars, RED + B)
    if threat >= 2:
        return c(chars, YLW)
    return c(chars, GRN)


def hline(label: str, w: int, color: str = None, label_color: str = None) -> str:
    """Full-width horizontal divider with optional label.

    Polaris theme — color default = POLARIS_BLUE (soft cyan/steel blue, star color).
    Label format: --- TITLE --- (plain, no decorative symbols).

    color: line color (default POLARIS_BLUE)
    label_color: label text color (default P_GRY)
    """
    col = color if color else POLARIS_BLUE
    lcol = label_color if label_color else P_GRY
    if label:
        lbl = f" {label} "
        lbl_w = vlen(lbl)  # CJK-aware
        side = max(3, (w - lbl_w) // 2)
        rest = w - side - lbl_w
        return c(HL * side, col) + c(lbl, lcol) + c(HL * max(0, rest), col)
    return c(HL * w, col)


# ── Formatting helpers ───────────────────────────────────────────────────────

def fmt_size(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes < 1000:
        return f"{size_bytes}B"
    if size_bytes < 1_000_000:
        return f"{size_bytes / 1000:.0f}KB"
    return f"{size_bytes / 1_000_000:.1f}MB"


def fmt_age(age_s: float) -> str:
    """Human-readable time age."""
    if age_s < 0:
        return "N/A"
    if age_s < 60:
        return f"{age_s:.0f}s"
    if age_s < 3600:
        return f"{age_s / 60:.0f}m"
    return f"{age_s / 3600:.1f}h"


def age_color(age_s: float, warn: float = 60, bad: float = 300) -> str:
    """Color string for file/data age."""
    if age_s < 0:
        return RED + B
    if age_s < warn:
        return GRN
    if age_s < bad:
        return YLW
    return RED


def age_label(age_s: float, warn: float = 60, bad: float = 300) -> str:
    """Status label for file age."""
    if age_s < 0:
        return c("MISSING", RED + B)
    if age_s < 10:
        return c("LIVE", GRN + B)
    if age_s < warn:
        return c("OK", GRN)
    if age_s < bad:
        return c("STALE", YLW)
    return c("OLD", RED)


# ── Modern UI helpers ─────────────────────────────────────────────────────────

# Spinner frames — cycles through on each tick
SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

# Half-block chars for finer resolution bars
LOWER_HALF = "\u2584"   # ▄
UPPER_HALF = "\u2580"   # ▀
LEFT_HALF  = "\u258c"   # ▌
RIGHT_HALF = "\u2590"   # ▐

# Box top/bottom connector characters (mixed single/double)
LT_D = "\u255e"   # ╞ — left T joining double horizontal
RT_D = "\u2561"   # ╡ — right T joining double horizontal


def metric_box(label: str, value: str, val_color: str = "") -> str:
    """Inline metric box: [LABEL value] with optional color."""
    val_s = f"{val_color}{value}{R}" if val_color and _TTY else value
    return f"{c('[', POLARIS_BLUE)}{c(label, D)} {val_s}{c(']', POLARIS_BLUE)}"


def progress_bar(value: float, maximum: float, width: int,
                 label: str = "", show_pct: bool = True) -> str:
    """Labeled progress bar.  ████░░░░ 73%  label"""
    pct   = max(0.0, min(1.0, value / maximum if maximum else 0))
    filled = int(pct * width)
    empty  = width - filled
    fill_c = GRN if pct >= 0.6 else YLW if pct >= 0.35 else RED
    fill_s = c(BLOCK * filled, fill_c)
    empty_s = c(SHADE * empty, D)
    pct_s   = f" {pct * 100:.0f}%" if show_pct else ""
    lbl_s   = f" {c(label, D)}" if label else ""
    return f"{fill_s}{empty_s}{pct_s}{lbl_s}"


def status_badge_v2(text: str, level: str = "info") -> str:
    """Colored badge with icon.  level: info/ok/warn/danger/off"""
    level_map = {
        "ok":     (BG_G   + B, WHT, "●"),
        "warn":   (BG_Y   + B, WHT, "▲"),
        "danger": (BG_R   + B, WHT, "▼"),
        "info":   (BG_CYN + B, WHT, "◆"),
        "off":    (BG_BLK + D, GRY, "○"),
        "live":   (BG_G   + B, WHT, "◉"),
    }
    bg, fg, icon = level_map.get(level, (BG_BLK, D, "?"))
    return c(f" {icon} {text} ", bg + fg)


def gradient_bar(pct: float, width: int) -> str:
    """Multi-segment gradient bar 0-100 using color zones.
    0-33%: RED  34-66%: YLW  67-100%: GRN  │ at 50% mark"""
    if width < 3:
        return bar(pct, width)
    filled = max(0, min(width, int(pct / 100 * width)))
    empty  = width - filled
    # Divide filled into thirds
    t1 = max(0, min(filled, width // 3))
    t2 = max(0, min(filled - t1, width // 3))
    t3 = max(0, filled - t1 - t2)
    result = list(
        c(BLOCK * t1, RED) +
        c(BLOCK * t2, YLW) +
        c(BLOCK * t3, GRN) +
        c(SHADE * empty, D)
    )
    # 50% marker — insert │ at midpoint (visual only, doesn't affect ANSI)
    mid = width // 2
    if mid < width and width >= 6:
        # Build without marker first, then overlay
        bar_plain = BLOCK * filled + SHADE * empty
        chars = list(bar_plain)
        if mid < len(chars):
            chars[mid] = "│"
        # Re-colorize
        parts = []
        for i, ch in enumerate(chars):
            if ch == "│":
                parts.append(c("│", WHT))
            elif i < t1:
                parts.append(c(ch, RED))
            elif i < t1 + t2:
                parts.append(c(ch, YLW))
            elif i < filled:
                parts.append(c(ch, GRN))
            else:
                parts.append(c(ch, D))
        return "".join(parts)
    return (c(BLOCK * t1, RED) +
            c(BLOCK * t2, YLW) +
            c(BLOCK * t3, GRN) +
            c(SHADE * empty, D))


def pnl_inline_bar(pnl_pct: float, width: int = 12) -> str:
    """Centered inline bar for +/- PnL. Center=0, right=profit, left=loss."""
    half = width // 2
    clamp = max(-5.0, min(5.0, pnl_pct))   # scale ±5%
    norm = clamp / 5.0
    if abs(norm) < 0.01:
        # Zero PnL — empty bar with center mark
        return " " * (half - 1) + c("│", D) + " " * half
    if norm > 0:
        filled = max(1, int(norm * half))
        left_s = " " * half
        right_s = c(BLOCK * filled, GRN + B) + " " * (half - filled)
    else:
        filled = max(1, int(-norm * half))
        left_s = " " * (half - filled) + c(BLOCK * filled, RED + B)
        right_s = " " * half
    return left_s + right_s


def dim_row(row_idx: int) -> str:
    """Return dim ANSI code for even rows (alternate row shading)."""
    return D if row_idx % 2 == 0 else ""


def spinner(tick: int) -> str:
    """Return current spinner frame for given tick number."""
    return c(SPINNER_FRAMES[tick % len(SPINNER_FRAMES)], CYN + B)


def pulse_arrow(direction: str) -> str:
    """Highlighted directional arrow: up=green-bold, down=red-bold."""
    if direction == "up":
        return f"{GRN}{B}\u25b2{R}" if _TTY else "^"
    if direction == "down":
        return f"{RED}{B}\u25bc{R}" if _TTY else "v"
    return f"{D}\u2192{R}" if _TTY else "-"


# ── 2-Column Merge ──────────────────────────────────────────────────────────

def merge_cols(left: list[str], right: list[str],
               lw: int, rw: int, gap: str = " │ ") -> list[str]:
    """Merge two column outputs side-by-side with ANSI-aware padding.

    Pads shorter column with blank rows. Each line is padded to exactly lw/rw
    visible chars before joining with gap.
    """
    h = max(len(left), len(right))
    out = []
    for i in range(h):
        l = pad(left[i], lw) if i < len(left) else pad("", lw)
        r = pad(right[i], rw) if i < len(right) else pad("", rw)
        out.append(f"{l}{gap}{r}")
    return out


def fit_rows(lines: list[str], target: int, w: int) -> list[str]:
    """Truncate or pad a section's output to exactly `target` rows."""
    if len(lines) >= target:
        return lines[:target]
    return lines + [pad("", w)] * (target - len(lines))


def rotate_bottom_up(items: list, tick: int, max_vis: int) -> list:
    """1-row shift bottom-up rotation.

    tick=0: [item 0, 1, ..., max_vis-1]  top=oldest, bottom=newest
    tick=1: [item 1, 2, ..., max_vis]    item 0 gone, max_vis appears at bottom
    tick=2: [item 2, 3, ..., max_vis+1]

    New items appear at the bottom and scroll upward (ticker-tape style).
    No-op when n <= max_vis.
    """
    n = len(items)
    if n == 0 or n <= max_vis:
        return items[:max_vis]
    offset = tick % n
    return [items[(offset + i) % n] for i in range(max_vis)]


def rotate_top_down(items: list, tick: int, max_vis: int) -> list:
    """1-row shift top-down rotation (rotate_bottom_up 반대 방향).

    tick=0: [item 0, 1, ..., max_vis-1]  top=item 0
    tick=1: [item n-1, 0, 1, ..., max_vis-2]  item n-1 이 맨 위로, 기존 항목 아래로 밀림
    tick=2: [item n-2, n-1, 0, 1, ..., max_vis-3]

    LIVE LOG 와 동일 방향 — 새 항목이 위에서 등장, 기존 항목 아래로 흘러감.
    No-op when n <= max_vis.
    """
    n = len(items)
    if n == 0 or n <= max_vis:
        return items[:max_vis]
    offset = (-tick) % n  # bottom_up 과 반대 방향
    return [items[(offset + i) % n] for i in range(max_vis)]


# ── Visual effect helpers ────────────────────────────────────────────────────

def bg_alert(row: str, level: str) -> str:
    """Apply background color to an entire row string.

    level:
      'red'    — BG_R  (loss / danger / KILL)
      'yellow' — BG_Y  (warning / near stop)
      'green'  — BG_G  (profit / in the money)
      'orange' — 256-color orange bg (TIME exit imminent)
      ''       — passthrough (no-op)
    """
    if not _TTY or not level:
        return row
    _BG_ORANGE = _e("48;5;130")
    _bg_map = {
        "red":    BG_R,
        "yellow": BG_Y,
        "green":  BG_G,
        "orange": _BG_ORANGE,
    }
    bg = _bg_map.get(level, "")
    if not bg:
        return row
    return f"{bg}{row}\033[0m"


def blink(s: str) -> str:
    """Wrap string in slow-blink ANSI (code 5). TTY-safe."""
    if not _TTY:
        return s
    return f"\033[5m{s}\033[0m"


def dim_conditional(s: str, condition: bool) -> str:
    """Apply dim if condition is True, otherwise passthrough."""
    return c(s, D) if condition else s


