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
   * frame, camera-invariant, not a radial effect (plain horizontal rules). */
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
      ctx.beginPath(); ctx.arc(p.x, p.y, ringR, 0, Math.PI * 2);
      ctx.strokeStyle = field.rgba(vc, 0.22); ctx.lineWidth = 0.7; ctx.stroke();
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
  function drawWatchChips(ctx, now) {
    field.nodesOf('watch').forEach((n) => {
      const s = field.screenOf(n.id);
      if (!s) return;
      const p = bobOf(s, now);
      const sym = String(n.ticker || '').split(':').pop().split('-')[0].split('_')[0];
      if (!sym) return;
      const vc = field.venueColorOf(n.exchange) || s.color || '#8fb0c8';
      drawBracketChip(ctx, p.x, p.y - 11, sym, vc, n.state === 'dormant' ? 0.3 : 0.6);
    });
  }

  function drawDecor(ctx, now) {
    drawStrategyReticles(ctx, now);
    drawWatchChips(ctx, now);
  }

  window.PolarisSpineDeco = { renderGraticule, setStrategyGauges, drawDecor };
})();
