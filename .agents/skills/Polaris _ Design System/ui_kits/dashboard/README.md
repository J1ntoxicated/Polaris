# Polaris UI Kit — Terminal Dashboard

JSX recreation of the Polaris (Auto Invasion Mk1) terminal dashboard. The product
is a TTY app rendered with raw ANSI escape codes; this kit reproduces the visual
language for HTML mockups, design exploration, and screenshots.

## Components

| File | Recreates |
| --- | --- |
| `Banner.jsx` | 3-row top banner — status, exchange balances, 24h strip |
| `LivePositions.jsx` | Live positions table with threat bars + row-alert (loss > 2%) |
| `NorthStarMatrix.jsx` | 7×12 KPI table (PF / WR / DD / Sharpe per time slice) |
| `StrategyCellPanel.jsx` | Strategy × Cell matrix top/worst |
| `PipelineFunnel.jsx` | scan → candidate → pass → exec funnel |
| `ProvidersAI.jsx` | 13 signal providers + AI escalation matrix |
| `LiveLog.jsx` | Live log feed with severity coloring |
| `Footer.jsx` | System state strip — PID, evo, WS, uptime |
| `primitives.jsx` | `<Hline>`, `<Glyph>`, `<Cell>`, `<Bar>`, `<Spark>`, `<Badge>`, color helpers |

`index.html` wires them into the dual-monitor layout (Operations LEFT + Intelligence RIGHT).
