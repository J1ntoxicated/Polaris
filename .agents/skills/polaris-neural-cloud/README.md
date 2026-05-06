# Polaris Neural Cloud

Live 3D dashboard for the Polaris/북극성 trading system.

**One-line summary**: every pipeline stage is a wireframe sphere shell, exchanges are mini-spheres at the core, AI modules orbit overhead — when something fires, you see the lightning.

## Quick start

### Just look at it
Open `bundle/polaris-neural-cloud.html` in a browser. Done.

### Develop / extend
```bash
cd src/
python3 -m http.server 8080
# open http://localhost:8080
```
Edit `src/index.html` + the three JS files, refresh.

## What's where

- **Topology + rendering**: `src/sphere-render.js` (the only file that knows about geometry)
- **Data binding**: `src/data-adapter.js` (loads `data/*.json`, maps entities to nodes, tails `events.jsonl`)
- **UI**: `src/interaction.js` (hover/click → tooltip, side panel, chain highlighting)
- **HTML chrome**: `src/index.html` (top bar, right legend, bottom strip)

See **`SKILL.md`** for the full guide on:
- binding your real AI module names (`PolarisCloud.bindAIModules`)
- wiring live data via JSON files or WebSocket
- the `PolarisCloud` programmatic API
- topology / orbit / shell parameters

## Origin

This is v4 — concentric shells with orbiting AI satellites. v3 was 7 free-floating clusters. The v4 metaphor (양파 껍질 + 위성) better matches the actual data flow: outer shells provide context, inner shells decide, the core executes, and AI watches from above.
