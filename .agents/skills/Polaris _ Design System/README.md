# Polaris (북극성) Design System

> **Polaris** (북극성, "North Star") is the operator-facing terminal dashboard for **Auto Invasion Mk1** — a fully-autonomous, multi-exchange algorithmic trading bot. The North Star Index (NSI) is the system's master health metric; the dashboard takes its name from it.

---

## What is the product?

**Auto Invasion Mk1** is a Python 3.11 trading bot that runs 24/7 across **OKX** (crypto perps), **Capital.com** (forex / indices / commodities CFDs), **Alpaca** (US stocks / ETF paper) and **Binance** (data only). It is a **contrarian-attack** system — every regime is an *attack* regime; there is no defensive mode. The bot self-tunes via an AI Governor (Gemini primary, Claude critical) and evolves its strategy population through Elo tournaments + Bayesian / structural mutation.

The **product surface that humans actually see** is the dashboard: two terminal monitors rendered with raw ANSI escape codes, ~260 columns wide × ~66 rows tall.

| Surface | What it is |
| --- | --- |
| **Operations dashboard** (`operations.py`) | LEFT monitor — live positions, signals, T13 KPI status, footer. Operator-realtime view. |
| **Intelligence dashboard** (`intel.py`) | RIGHT monitor — 7-panel analytics: Market/Regime, Strategy×Cell, Pipeline&Funnel, Providers/AI, Exit Quality, Obs/Safety, Action Items, Live Log. |

There is **no web app, no mobile app, no marketing site**. The "brand" lives entirely inside the terminal — colored ANSI text, box-drawing characters, sparklines, and the ★ Polaris star glyph.

## Sources used to build this design system

- **Codebase:** `auto_invasion_mk1-main/` (mounted via File System Access API, read-only)
  - Primary visual SSOT: `invasion/dashboard/ansi.py` (palette, glyphs, helpers)
  - Banner / brand voice: `invasion/dashboard/sections/banner.py`
  - Layout: `invasion/dashboard/operations.py`, `invasion/dashboard/intel.py`
  - All section renderers: `invasion/dashboard/sections/intel_*.py`, `operations_*.py`, `positions*.py`
  - Architecture: `docs/ARCHITECTURE.md`
  - Exchange tag SSOT: `invasion/dashboard/sections/_exchange_style.py`
- No Figma, no design files, no marketing artifacts were supplied. Everything in this system is reverse-engineered from the terminal renderer.

---

## Index — what's in this folder

| Path | Purpose |
| --- | --- |
| `README.md` | This file |
| `SKILL.md` | Agent skill manifest (cross-compatible with Claude Code) |
| `colors_and_type.css` | CSS variables for colors + type tokens |
| `fonts/` | Font files (mono — the brand IS monospaced) |
| `assets/` | Logos, icon glyphs, sample dashboards, brand artifacts |
| `preview/` | Small HTML cards rendered in the Design System tab |
| `ui_kits/dashboard/` | React JSX recreation of the Polaris terminal UI |

---

## CONTENT FUNDAMENTALS

Polaris is **operator copy, not marketing copy**. Voice rules:

- **Bilingual code-switching.** Korean (한국어) and English are used together, often within one comment. Function docstrings frequently switch mid-sentence: e.g. *"한글/한자/일본어 등 east_asian_width='W' or 'F' 문자는 터미널에서 2 cells 차지."* Operator names ("Jin") and dates appear as casual citations: *"Jin 04-24"*, *"Jin '노트북 공책' 2026-04-24"*.
- **Terse, abbreviated, telegraph-style.** Always prefer 3–6 letter labels over full words. Examples from the codebase: `WR`, `PF`, `DD`, `Asym`, `NSI`, `F&G`, `BEP`, `KPI`, `DUP_REJ`, `mrgn%`, `levg`, `pos`, `bal`. **Never** spell out "Win Rate"; it's `WR` everywhere.
- **Casing.** ALL CAPS for section headers and status states (`LIVE POSITIONS`, `RISK_OFF`, `MAX ATTACK`, `LIT`, `DARK`). lowercase for column labels (`top`, `wst`, `incr1h`, `cap`, `gb`). PascalCase only in JSX/Python class names.
- **No "you" or "we".** Direct second person never appears. The bot is the agent; the operator reads. Imperative phrases only when triggering: `Awaiting trade data...`, `awaiting signal match`.
- **Combat / military metaphors are core.** The bot is "attacking the market". Real strings from production: `MAX ATTACK`, `FULL ATTACK`, `SHORT ATTACK`, `SCALP ATTACK`, `Battle Mode`, `INVASION SYSTEM`. The repo itself is named `auto_invasion`. Lean into this — it's not a typo.
- **Contrarian framing.** Fear = attack opportunity. Risk-off regimes spawn the most aggressive battle mode. Reflect this in copy: low fear = low conviction.
- **Star metaphor saturates everything.** ★ LIT (alive), ☆ DARK (offline), ✦ accent, ✧ extinguished. The product is named after Polaris; star glyphs are the primary status iconography.
- **No emoji.** Zero. Status uses ★ ☆ ✦ ✧ ● ○ ◆ ▲ ▼ ◉ — all BMP unicode geometry, never `🔴` or `📈`.
- **Numbers are first-class citizens.** Every panel is a tabular density wall. `$100,302  Pos: 7  PnL: -$120  WR: 54%  F&G: 19  Up: 1h30m`. Always include units (`%`, `$`, `s`, `m`, `h`). PnL always carries explicit sign: `+$123`, `-$120`.
- **Unknown values render as `—` (em dash) or `--`, never `null`/`N/A`/blank.** Empty rows show `awaiting trade data` or `no cell data`, dim grey.
- **Time formats are absolute and short.** `2026-04-26 14:32:01`, `1h30m`, `45s`, `123ms`. Never relative ("a few minutes ago").

### Voice samples (lifted verbatim from the codebase)

> `★ POLARIS ✦ ◉ LIT [RISK_OFF] FULL ATTACK   ★★★ NSI ████████ 78  Tick: 12,847  2026-04-26 14:32:01`

> `OKX $52,103  CAP $31,994  ALP $16,205  Pos: 7  PnL: +$24.3  WR: 54%  F&G: 19  Up: 1h30m`

> `═══ 24h: 242 trades  131W  Net: -$120.3  WR: 54% ═══════════════════════════════════════`

> `Scanner: 1,247,392 ticks  Sig cand: 18  — awaiting signal match`

---

## VISUAL FOUNDATIONS

Polaris is a **monospace terminal aesthetic**. Every visual decision is downstream of "this has to render in 256-color ANSI on a 80–271 column terminal". The design system codifies the look so HTML mockups can mimic it 1:1.

### Type
- **Single typeface family: monospace.** No proportional fonts anywhere. The codebase ships no fonts (it's a TTY); we use **JetBrains Mono** as the on-screen substitute (subbed for the user's local terminal font).
- **Two weights only:** 400 regular and 700 bold. Bold is used for active values, headers, and `LIT` state. Italic exists in the ANSI palette (`I = \033[3m`) but is rarely used.
- **One size in the terminal**, but for HTML mockups we expose a small scale: 11/12/14/18/24/32 px. Body / data rows = 12px. Section dividers = 12px tracked. Banner title (`POLARIS`) = 14–18px bold.
- **No anti-aliased icons.** Glyphs are unicode characters that share the type's metrics: ★ ☆ ✦ ✧ ● ○ ◆ ▲ ▼ ◉ ─ │ ┌ ┐ └ ┘ ═ ║ █ ░ ▒ ▓ ▁▂▃▄▅▆▇█.

### Color
A **deliberately desaturated, eye-friendly pastel palette** layered over a near-black background. From `ansi.py`:

| Token | 256-color | Usage |
| --- | --- | --- |
| `--bg` | `#0a0d12` (closest to terminal default) | Page background |
| `--p-grn` | 256-color 114 → `#87d787` | Profit, long, OK, LIVE |
| `--p-red` | 256-color 174 → `#d78787` | Loss, short, danger, KILL |
| `--p-cyn` | 256-color 117 → `#87d7ff` | Signal, AI, info, OKX exchange |
| `--p-ylw` | 256-color 186 → `#d7d787` | Warning, transition, ALP exchange |
| `--p-mag` | 256-color 183 → `#d7afff` | Regime, strategy, YOLO |
| `--p-blu` | 256-color 110 → `#87afd7` | CAP exchange, info |
| `--p-org` | 256-color 216 → `#ffaf87` | Accents, borders (legacy) |
| `--p-wht` | 256-color 253 → `#dadada` | Key numbers, primary text |
| `--p-gry` | 256-color 248 → `#a8a8a8` | Secondary labels |
| `--p-dim` | 256-color 242 → `#6c6c6c` | Background elements |
| `--ghost` | 256-color 241 → `#626262` | Faint dividers |
| `--polaris-blue` | 256-color 67 → `#5f87af` | Brand accent — section dividers, the star |
| `--p-navy` | 256-color 24 → `#005f87` | Deep accent |

**Brand color = `--polaris-blue` (#5f87af) — steel/cyan blue. Use it for the ★ glyph, section dividers (`hline()`), and the col separator (`│`). Saturated full-bright `RED/GRN/YLW/CYN/MAG/BLU` exist but are reserved for high-urgency states (KILL, alert backgrounds).**

Backgrounds: `BG_R` (red) and `BG_Y` (yellow) only ever wrap a whole row to flag a critical condition (e.g. `pnl_pct < -2.0` paints the row red). **No gradients.** No bluish-purple gradients, no glassmorphism, no soft shadows.

### Spacing
Spacing is measured in **character cells**, not pixels. Convert in HTML at 1 cell = `0.6em` horizontal, `1.4em` line-height.
- Panels are **butt-jointed** with no gutter, separated by `hline()` strings.
- Two-column layout: `LW + GAP_W(3) + RW = W`. The 3-cell gap is literally `" │ "` rendered in `--polaris-blue`.
- Cells inside rows use 1 or 2 spaces between groups: `f"{c('Pos:', P_GRY)} {c(str(n_pos), P_CYN+B)}"` — label-space-value, then **two** spaces before the next group.
- No padding on cards. No rounded corners. No `border-radius`.

### Borders, dividers, cards
- **No cards.** Sections are demarcated by horizontal rules (`hline`) and column separators (`│`), not by boxes.
- Box-drawing characters when a box IS used: `─ │ ┌ ┐ └ ┘ ═ ║`. Doubles (`═ ║`) for the 24h summary strip.
- **No box shadows.** No `inset` shadows. The only "elevation" is reverse-video (`BG_R`, `BG_Y`) painting an entire row.

### Backgrounds & imagery
- Background is always solid near-black `#0a0d12`. **No images, no patterns, no gradients, no textures.**
- Sparklines (`▁▂▃▄▅▆▇█`) and gradient bars (red→yellow→green block fill) are the only "imagery" — they're ASCII art.
- No photography. No illustrations. No SVG illustrations either — anything pictorial breaks the metaphor.

### Animation
- **Frame-based, not transition-based.** The dashboard redraws the whole frame every 1 second (`time.sleep(1)`). Fades / eases / springs do not exist.
- The one motion primitive is **rotation**: `rotate_bottom_up(items, tick, max_rows)` cycles long lists through a fixed window so every item gets airtime. Use this for any "ticker" feel in mockups.
- Spinner: `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` cycles per tick.
- Blink: `\033[5m` (the slow-blink ANSI) for critical alerts only. Never use CSS `transition` on color.
- For HTML mockups: a 1s `step-end` re-render with no easing matches the feel. If you must, a single 60ms cross-fade on row-update is acceptable.

### Hover / press states
- The terminal has no hover. **Do not invent hover states.** For interactive HTML mockups, mimic *focus* by toggling the row's foreground to `--p-wht-bold` and adding a leading `▶` glyph. No background change, no scale, no shadow.

### Layout rules
- **Fixed grid.** Every section advertises a row budget (`_ROWS = 14`, `_T13_STATUS_ROWS = 3`). Renderers MUST fill or truncate to that budget. Variable-height panels exist but are explicitly marked.
- **Right-aligned numbers, left-aligned labels.** Always.
- **Width-first, height-flexible.** Code reasons in *visible cells* (`vlen()` is CJK-aware → 2 cells for 한글).
- Top-down reading order: header (status) → body (positions / matrix) → footer (system state).

### Borders & radii
- `border-radius: 0` everywhere. No rounded anything.
- Borders are **single 1-cell unicode rules**, color `--polaris-blue` for primary, `--ghost` for tertiary.

### Transparency & blur
- **None.** No `backdrop-filter`. No `opacity:0.5` overlays. The closest to opacity is the `--p-dim` text color, which is just a darker grey.

### Cards
- There are no cards. If a mockup truly requires bounded content, draw an explicit box with `─ │ ┌ ┐ └ ┘` characters and color `--polaris-blue`. No fill. No shadow. No radius.

### Imagery vibe
- Cool. Steel-blue and pastel green/red over near-black. Never warm. Never sepia. No grain.

---

## ICONOGRAPHY

Polaris ships **zero raster icons, zero SVG icons, zero icon fonts**. Every "icon" is a unicode glyph that shares the monospaced text metrics. This is the entire icon system, lifted from `ansi.py`:

| Glyph | Unicode | Meaning |
| --- | --- | --- |
| ★ | U+2605 | LIT — bot alive, active strategy, NSI healthy |
| ☆ | U+2606 | Idle / dim indicator |
| ✦ | U+2726 | Section divider, accent |
| ✧ | U+2727 | DARK — extinguished, offline strategy |
| ● | U+25CF | Bullet, OK status |
| ○ | U+25CB | Off status |
| ◉ | U+25C9 | LIVE badge icon |
| ◆ | U+25C6 | INFO badge icon |
| ▲ | U+25B2 | Up tick / warning |
| ▼ | U+25BC | Down tick / danger |
| ↑ ↓ → | U+2191/2193/2192 | Trend direction |
| █ ░ ▒ ▓ | U+2588 etc | Block fills (bars, threat) |
| ▁▂▃▄▅▆▇█ | U+2581-2588 | Sparkline ramp |
| ─ │ ┌ ┐ └ ┘ | U+2500-2518 | Single box drawing |
| ═ ║ ╔ ╗ ╚ ╝ | U+2550-255D | Double box drawing (24h strip only) |

**Rules:**
- **Never** add an emoji. Never add a Lucide icon. Never inline an SVG that isn't a shape derived from these glyphs.
- For HTML mockups: render glyphs as *text* in the monospace face. Do NOT use an icon font CDN. Do NOT use Heroicons, Lucide, Feather, etc.
- The single brand mark is **★** (U+2605) in `--polaris-blue` — that's the logo.
- Status pairs always carry a glyph: `★ LIT` / `✧ DARK`, `● OK` / `○ OFF`, `▲ WARN` / `▼ DANGER`.

If a future mockup needs an "icon" not in this list, **flag it to the user** rather than introducing one.

---

## Asset substitutions (flagged)

- **Font:** the codebase ships no font (it inherits the user's terminal font). I substituted **JetBrains Mono** (Google Fonts) as the closest match for screenshots and mockups. If you want a specific system font (SF Mono, Fira Code, Berkeley Mono, etc.) — flag it and I'll swap.
- **No logo asset existed in the repo.** I generated `assets/polaris-mark.svg` and `assets/polaris-wordmark.svg` from the in-code `★ POLARIS` banner. These are pure type + glyph, no novel illustration.
