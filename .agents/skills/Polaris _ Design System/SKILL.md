---
name: polaris-design
description: Use this skill to generate well-branded interfaces and assets for Polaris (북극성, "Auto Invasion Mk1" trading bot dashboard), either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

## Quick orientation

- **Polaris is a terminal aesthetic.** Monospace only (JetBrains Mono substitute), near-black background `#0a0d12`, ANSI 256-color pastels, unicode box-drawing + star glyphs as the icon system.
- **Brand color = `--polaris-blue` (#5f87af)**, used for the ★ glyph, section dividers, column separators.
- **No emoji. No SVG icons. No gradients. No rounded corners. No shadows. No images.** If you reach for any of these, stop.
- **Voice is bilingual (한/EN), telegraphic, military-themed.** "FULL ATTACK", "★ LIT", "✧ DARK", "MAX ATTACK" are real strings — use them.
- Numbers are first-class: every panel is a tabular density wall with right-aligned numbers, tabular-nums, explicit signs (`+$24.3`, `-2.36%`), and unicode sparks/bars.

## Files

- `README.md` — full brand guidelines (Content Fundamentals, Visual Foundations, Iconography)
- `colors_and_type.css` — CSS variables, all palette tokens + type scale
- `assets/` — ★ logo SVGs
- `preview/` — small spec cards (colors, type, badges, tabular density, etc.)
- `ui_kits/dashboard/` — JSX recreation of the dual-monitor terminal dashboard

## Building with Polaris

1. Always link `colors_and_type.css` and use the CSS vars (`--polaris-blue`, `--p-grn`, `--p-red`, `--p-cyn`, `--p-wht`, `--p-gry`, `--p-dim`, `--bg`, etc.).
2. Set `font-family: var(--font-mono)` on the root.
3. Use `<Hline>`, `<Cell>`, `<Bar>`, `<ThreatBar>`, `<Spark>`, `<Badge>`, `<PnL>` from `ui_kits/dashboard/primitives.jsx`.
4. Status iconography is unicode only: ★ ☆ ✦ ✧ ● ○ ◉ ◆ ▲ ▼.
5. Loss > 2% → row gets `background: var(--bg-row-r)`. Warn → `var(--bg-row-y)`. Otherwise no bg.
6. Animation = 1s frame redraws (`step-end`). No CSS transitions on color, no fades, no easing.

## When in doubt

- Lean into density. More numbers, more rows, smaller type — closer to the terminal.
- Lean into the metaphor. The bot attacks; fear = opportunity.
- If you need an icon that isn't in the unicode glyph table, **flag it to the user** rather than introducing one.
