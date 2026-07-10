/* Polaris FLOW — "Jarvis" decoration layer (Jin 2026-07-10, feat/jarvis-
 * language). Purely-additive visual refinements on top of the existing
 * Synaptic Spine engine (wall_spine_field.js layout/edges + wall_spine.js
 * canvas/rAF/gate cores) — split into its own file so those two don't grow
 * past the project's 500-LOC cap while adding: (4) the strategy asteroid
 * mini-reticle + score_F charge-fill gauge arc, (6) the watch-tier "[SYM]"
 * bracket chip, (7) the faint static engineering graticule.
 *
 * This file owns NO layout math and issues/sizes/gates/throttles nothing —
 * it only draws new hairline glyphs at positions the field layer already
 * computed (field.screenOf/nodesOf/gateScreen/venueColorOf). Loaded after
 * wall_spine_field.js and before wall_spine.js, which calls drawDecor() once
 * per frame and renderGraticule()/setStrategyGauges() at the appropriate
 * hooks (see wall_spine.js's bakeStatic() and its public API).
 *
 * Philosophy doc: vault/50_research/wall_design_philosophy_synaptic_current.md
 * Jarvis visual-language spec: hairline strokes only (0.6-0.9px, additive
 * where the element is meant to glow), no full-screen or radial-burst
 * effects, camera fully static, real data only (no fabricated numbers).
 * Display-only — nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  const field = window.PolarisSpineField;
  if (!field) return;

  /* ===== (7) engineering graticule — faint static horizontal rule lines,
   * anchored to the gate spine's mean Y, alpha <=0.04. Baked into the static
   * layer once per bake (wall_spine.js's bakeStatic()) — never redrawn per
   * frame, camera-invariant, not a radial effect (plain horizontal rules).
   * Console v2 (Jin 2026-07-11): zone dividers now read off
   * PolarisSpineField.WALL_ZONES instead of carrying their own hardcoded
   * ratios — the old 0.478/0.605 values had already drifted out of sync
   * with the real rail(0.505H then)/gate(0.655H) y this file doesn't own;
   * WALL_ZONES forecloses that class of drift for good. */
  function renderGraticule(ctx, w, h, gateScreenArr) {
    const gy = (gateScreenArr && gateScreenArr.length)
      ? gateScreenArr.reduce((a, g) => a + g.y, 0) / gateScreenArr.length
      : h * 0.6;
    const step = 46;
    ctx.strokeStyle = 'rgba(150,190,225,0.035)';
    ctx.lineWidth = 0.5;
    for (let y = gy % step; y < h; y += step) {
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    // Jarvis zone dividers (Jin 2026-07-11 "정리 되는 좋은거 다 갖다"):
    // hairline separators + end ticks + tiny uppercase section tags so the
    // vertical hierarchy (signal field → watchlist → strategy rail → gate
    // bus → regime) reads as an engineered console, not floating layers.
    const zone = (y, title) => {
      ctx.strokeStyle = 'rgba(150,190,225,0.10)';
      ctx.lineWidth = 0.6;
      ctx.beginPath(); ctx.moveTo(w * 0.015, y); ctx.lineTo(w * 0.985, y); ctx.stroke();
      [w * 0.015, w * 0.985].forEach((cx) => {
        ctx.beginPath(); ctx.moveTo(cx, y - 3); ctx.lineTo(cx, y + 3); ctx.stroke();
      });
      ctx.font = '600 7.5px JetBrains Mono, monospace';
      ctx.fillStyle = 'rgba(160,200,235,0.35)';
      ctx.textAlign = 'left';
      ctx.fillText(title, w * 0.015 + 6, y - 4);
    };
    const z = field.WALL_ZONES || {};
    zone(h * (z.signalTop != null ? z.signalTop : 0.165), 'SIGNAL FIELD');
    zone(h * (z.watchDivider != null ? z.watchDivider : 0.438), 'WATCHLIST');
    zone(h * ((z.railY != null ? z.railY : 0.492) - 0.014), 'STRATEGY RAIL');
    zone(h * ((z.gateBusY != null ? z.gateBusY : 0.655) - 0.05), 'GATE BUS · EXECUTION · LEARNING');
    zone(h * ((z.regimeY != null ? z.regimeY : 0.775) - 0.03), 'REGIME CONTEXT');
  }

  /* ===== drawArcGauge — shared hairline arc gauge (Jin 2026-07-11 console
   * v2, wall_console_blueprint.md §3) ===== consumed by
   * wall_console_readouts.js's BAY equity/PnL/firing/conversion gauges. A
   * NEW standalone utility (not a refactor of the existing strategy-reticle
   * score_F ring below, which keeps its own bespoke breathing — touching a
   * working per-node animation for a pure code-sharing refactor risks
   * exactly the "느낌 회귀" the build spec elsewhere explicitly forbids for
   * zero visual gain). Base ring + steel α0.13 (always source-over), value
   * arc in the given color (source-over), ONE additive tip mote (the single
   * contract-approved additive exception) + optional label/value text. */
  function drawArcGauge(ctx, cx, cy, r, pct, color, label, value) {
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(138,148,176,0.13)'; ctx.lineWidth = 1.1; ctx.stroke();
    const clamped = Math.max(0, Math.min(1, pct || 0));
    if (clamped > 0) {
      ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + clamped * Math.PI * 2);
      ctx.strokeStyle = field.rgba(color, 0.85); ctx.lineWidth = 1.7; ctx.stroke();
      const ta = -Math.PI / 2 + clamped * Math.PI * 2;
      ctx.globalCompositeOperation = 'lighter';
      field.drawDot(ctx, cx + Math.cos(ta) * r, cy + Math.sin(ta) * r, 1.6, color, 0.85, 5);
      ctx.globalCompositeOperation = 'source-over';
    }
    if (label) {
      ctx.font = '600 6.5px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(138,148,176,0.55)';
      ctx.fillText(label, cx, cy + r + 10);
    }
    if (value != null) {
      ctx.font = '700 9px ui-monospace, Menlo, monospace';
      ctx.textAlign = 'center';
      ctx.fillStyle = 'rgba(223,232,255,0.92)';
      ctx.fillText(value, cx, cy + 3);
    }
  }

  /* ===== drawZoneScan — SIGNAL FIELD-only scan hairline (Jin 2026-07-11
   * console v2, "효시 부족" fix): a slow ~9s top->bottom 1px sweep confined
   * INSIDE the signal field band (WALL_ZONES.signalTop..signalClamp) — the
   * zone dividers themselves are the boundary, so this is element-local, not
   * a full-screen effect. Zero data shape (no ticks/labels/counts) so it
   * reads unmistakably as decoration, never as a fabricated reading. */
  function drawZoneScan(ctx, now) {
    const sz = field.sizeOf && field.sizeOf();
    if (!sz || !sz.W || !sz.H) return;
    const { W, H } = sz;
    const z = field.WALL_ZONES || {};
    const top = H * (z.signalTop != null ? z.signalTop : 0.165);
    const bot = H * (z.signalClamp != null ? z.signalClamp : 0.425);
    const period = 9000;
    const t = (now % period) / period;
    const y = top + t * (bot - top);
    ctx.globalCompositeOperation = 'source-over';
    ctx.beginPath();
    ctx.moveTo(W * 0.015, y); ctx.lineTo(W * 0.985, y);
    ctx.strokeStyle = 'rgba(150,190,225,0.04)';
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  /* ===== (4) strategy asteroid reticle — mini hairline ring (venue color) +
   * score_F window-fill arc gauge (SAME data as the bottom #class-gauge-
   * strip, flow_stats' `classes`), breathing while the strategy is firing.
   * ===== */
  let stratGauge = new Map(); // 'strat_<id>' -> real fill fraction 0..1
  function setStrategyGauges(classes) {
    stratGauge = new Map();
    (classes || []).forEach((c) => {
      if (!c || !c.strategy_id || !c.window_w) return;
      stratGauge.set('strat_' + c.strategy_id, Math.max(0, Math.min(1, c.filled / c.window_w)));
    });
  }
  function bobOf(s, now) {
    return {
      x: s.x + Math.sin(now * 0.0006 * s.bobSpeed + s.phaseOff) * s.bobAmp,
      y: s.y + Math.cos(now * 0.00042 * s.bobSpeed + s.phaseOff * 1.3) * s.bobAmp,
    };
  }
  function drawStrategyReticles(ctx, now) {
    ctx.globalCompositeOperation = 'lighter';
    field.nodesOf('strat').forEach((n) => {
      const s = field.screenOf(n.id);
      if (!s) return;
      const p = bobOf(s, now);
      const vc = field.venueColorOf(n.exchange) || s.color || '#8fb0c8';
      const ringR = (s.r || 4) + 5.5;
      // slimmer base ring (Jin 2026-07-11 "열매마냥" — instrument, not fruit)
      ctx.beginPath(); ctx.arc(p.x, p.y, ringR, 0, Math.PI * 2);
      ctx.strokeStyle = field.rgba(vc, 0.13); ctx.lineWidth = 0.6; ctx.stroke();
      const pct = stratGauge.get(n.id) || 0;
      if (pct > 0) {
        const breathe = n.state === 'firing' ? (0.55 + 0.45 * Math.sin(now / 500 + (s.phaseOff || 0))) : 0.85;
        ctx.beginPath(); ctx.arc(p.x, p.y, ringR, -Math.PI / 2, -Math.PI / 2 + pct * Math.PI * 2);
        ctx.strokeStyle = field.rgba(vc, 0.7 * breathe); ctx.lineWidth = 0.85; ctx.stroke();
      }
    });
    ctx.globalCompositeOperation = 'source-over';
  }

  /* ===== (6) watch-tier bracket chip — "[SYM]" corner-bracket glyph (venue
   * hairline), same L-tick grammar as the gate reticle corner ticks, that
   * tracks the watch node's own live bob position. ===== */
  function drawBracketChip(ctx, x, y, text, color, alpha) {
    ctx.font = '600 7.5px JetBrains Mono, monospace';
    const w = ctx.measureText(text).width;
    const left = x - w / 2 - 3, right = x + w / 2 + 3, top = y - 5, bottom = y + 5, tick = 3;
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = field.rgba(color, alpha); ctx.lineWidth = 0.65;
    [[left, top, 1, 1], [right, top, -1, 1], [left, bottom, 1, -1], [right, bottom, -1, -1]].forEach(([cx, cy, sx, sy]) => {
      ctx.beginPath();
      ctx.moveTo(cx, cy + sy * tick); ctx.lineTo(cx, cy); ctx.lineTo(cx + sx * tick, cy);
      ctx.stroke();
    });
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = field.rgba(color, Math.min(1, alpha + 0.2));
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y + 0.5);
    ctx.textBaseline = 'alphabetic';
  }
  // Watch chip row (Jin 2026-07-11 console v2, "TRX/USDG/ALGO 1.9px 중첩"
  // fix): x-lane placement via PolarisConsoleLanes.placeRow — a minimum-
  // pitch sweep + relax so adjacent "[SYM]" chips never overlap, demoting
  // anything that still can't fit into a second row instead of stamping on
  // top of a neighbour. A hard cap folds any further overflow into one real-
  // count "+N" tail chip rather than crowding the row indefinitely.
  const WATCH_CHIP_CAP = 42;
  function drawWatchChips(ctx, now) {
    const items = [];
    const byId = {};
    field.nodesOf('watch').forEach((n) => {
      const s = field.screenOf(n.id);
      if (!s) return;
      const p = bobOf(s, now);
      const sym = String(n.ticker || '').split(':').pop().split('-')[0].split('_')[0];
      if (!sym) return;
      byId[n.id] = { n, p, sym };
      items.push({ id: n.id, x: p.x });
    });
    if (!items.length) return;
    const visible = items.slice(0, WATCH_CHIP_CAP);
    const overflowN = items.length - visible.length;
    const sz = field.sizeOf && field.sizeOf();
    const wMax = (sz && sz.W) || 2000;
    const lanes = window.PolarisConsoleLanes;
    const placed = lanes ? lanes.placeRow(visible, { minPitch: 24, xMin: 4, xMax: wMax - 4, rowGap: 13 }) : null;
    let lastX = 0, lastY = byId[visible[0].id].p.y;
    visible.forEach((it) => {
      const { n, p, sym } = byId[it.id];
      const lane = placed && placed.get(it.id);
      const x = lane ? lane.x : p.x;
      const y = p.y - 11 + (lane ? lane.dy : 0);
      const vc = field.venueColorOf(n.exchange) || '#8fb0c8';
      drawBracketChip(ctx, x, y, sym, vc, n.state === 'dormant' ? 0.3 : 0.6);
      lastX = x; lastY = y;
    });
    if (overflowN > 0) {
      drawBracketChip(ctx, Math.min(wMax - 20, lastX + 30), lastY, '+' + overflowN, '#8a94b0', 0.55);
    }
  }

  function drawDecor(ctx, now) {
    drawStrategyReticles(ctx, now);
    drawWatchChips(ctx, now);
  }

  window.PolarisSpineDeco = { renderGraticule, setStrategyGauges, drawDecor, drawArcGauge, drawZoneScan };
})();
