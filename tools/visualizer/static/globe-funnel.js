/* Polaris globe-funnel — G1→G8 GATE LANE (Jin 2026-06-23 structure rebuild).
 *
 * Jin: "globe가 우리 구조에 시그널에 맞게 활동하게 … 프로브·시그널·활동에 맞게 구성."
 * The middle shell used to read as a chaotic K-nearest satellite mesh (중구난방).
 * This layer draws the LITERAL pipeline the bot runs — the 8-gate funnel
 * (ADR-004: G1 Universe → G2 Signal → G3 Validator → G4 Watcher → G5 Sizer →
 * G6 Monitor → G7 Exit → G8 Reflector) — as ONE ordered, directed lane wrapping
 * the middle shell between the outer probe clouds and the core trade heart, so a
 * candidate visibly flows probe → signal → through the gates → position.
 *
 * Data: graph.json `gate_funnel[]` {gate_id,label,pass_n,kill_n,total} (already
 * emitted by polaris_graph._gate_funnel, previously never drawn) + `kills[]`
 * {ticker,gate_id,ts} for transient per-gate kill motes (measurement honesty —
 * NOT a throttle; nothing here gates/sizes/blocks a trade). Activity is EVENT-
 * DRIVEN: a segment only flares when its pass/kill counts change between polls.
 *
 * It WRAPS window.PolarisGlobe_drawFlows (draws the lane each frame, behind the
 * nodes, after the neural links) and exposes window.PolarisGlobe_gateAnchors so
 * globe-flows' pipeline edges can route a trade through the right gate position.
 * Display-only. Touches nothing in the trading path.
 */
(function () {
  'use strict';
  if (!document.getElementById('sphere')) return;

  const R_MIDDLE = (window.PolarisGlobe_shellR && window.PolarisGlobe_shellR.middle) || 0.55;
  // The gate lane is a tilted equatorial ring on the middle shell. Each gate owns
  // an arc segment in G1→G8 order; the lane is directed (G1 at one end → G8 toward
  // the core-exit side). band ≈ 0 (equator) so it sits clear of the family bands.
  const LANE_BAND = 0.0;
  const LANE_TILT = 0.34;          // small inclination so the directed lane reads in 3D
  // span: the gates occupy ~300° of the ring, leaving a visible gap = the
  // G1↔G8 seam (so the direction G1→…→G8 is legible, not a closed loop).
  const ARC_START = -Math.PI * 0.92;
  const ARC_SPAN = Math.PI * 1.84;

  let funnel = [];                 // last gate_funnel[] (sorted by gate_id)
  const segState = [];             // per-gate {pass_n,kill_n,flash,killFlash}
  const gateAnchors = {};          // gate_id → {x,y,z} home on the lane (for edges)
  let killMotes = [];              // transient {gate_id,t,dur}
  let _lastKillSig = '';

  window.PolarisGlobe_gateAnchors = gateAnchors;

  // 3D point on the tilted lane ring at parametric u∈[0,1] over the gate arc.
  function lanePoint(u) {
    const ang = ARC_START + u * ARC_SPAN;
    const ringR = Math.sqrt(Math.max(0, 1 - LANE_BAND * LANE_BAND)) * R_MIDDLE;
    const x = Math.cos(ang) * ringR;
    const zr = Math.sin(ang) * ringR;
    // tilt the ring around the X axis so the directed lane has depth.
    const ci = Math.cos(LANE_TILT), si = Math.sin(LANE_TILT);
    const y = LANE_BAND * R_MIDDLE * ci - zr * si;
    const z = LANE_BAND * R_MIDDLE * si + zr * ci;
    return { x, y, z };
  }

  // Ingest a fresh graph payload: detect pass/kill deltas (→ event flares) and
  // refresh the per-gate anchor homes used by the pipeline edges.
  function ingest(d) {
    const gf = (d && d.gate_funnel) || [];
    funnel = gf.slice().sort((a, b) => (a.gate_id || 0) - (b.gate_id || 0));
    const m = funnel.length;
    for (let i = 0; i < m; i++) {
      const g = funnel[i];
      const u = m > 1 ? (i + 0.5) / m : 0.5;
      const lp = lanePoint(u);
      gateAnchors[g.gate_id] = lp;
      g._u = u;
      const st = segState[i] || (segState[i] = { pass_n: g.pass_n, kill_n: g.kill_n, flash: 0, killFlash: 0 });
      // EVENT-DRIVEN: a segment flares only when its counts advance.
      if (g.pass_n > st.pass_n) st.flash = Math.min(1, st.flash + 0.9);
      if (g.kill_n > st.kill_n) st.killFlash = Math.min(1, st.killFlash + 0.9);
      st.pass_n = g.pass_n; st.kill_n = g.kill_n;
    }
    segState.length = m;
    // kills[] → a transient red mote on the gate that killed (per-ticker honesty).
    const kills = (d && d.kills) || [];
    const sig = kills.map((k) => (k.ticker || '') + ':' + (k.gate_id || 0) + ':' + (k.ts || 0)).join(',');
    if (sig !== _lastKillSig) {
      // spawn a mote for the newest kills only (those not in the previous sig).
      for (const k of kills) {
        const tok = (k.ticker || '') + ':' + (k.gate_id || 0) + ':' + (k.ts || 0);
        if (_lastKillSig.indexOf(tok) === -1 && gateAnchors[k.gate_id]) {
          killMotes.push({ gate_id: k.gate_id, t: 0, dur: 1.1 });
        }
      }
      _lastKillSig = sig;
    }
  }
  window.PolarisGlobe_ingestFunnel = ingest;

  // Draw the directed G1→G8 lane: a faint guide arc + per-gate pass(green)/kill(red)
  // width markers + a flowing pulse that runs G1→G8 so the pipeline direction reads.
  function drawLane(ctx, project, now, dt) {
    if (!funnel.length) return;
    const rgba = window.PolarisGlobe_rgba;
    const conductor = window.PolarisGlobe_conductor || { x: 0, y: 0, z: 0 };
    const m = funnel.length;
    // 1) faint continuous lane guide (the pipeline spine), depth-sorted via project.
    ctx.lineWidth = 0.8;
    ctx.strokeStyle = rgba([0x8a, 0x94, 0xb0], 0.16);
    ctx.beginPath();
    const STEPS = 64;
    for (let s = 0; s <= STEPS; s++) {
      const u = s / STEPS;
      const lp = lanePoint(u);
      const p = project(conductor.x + lp.x, conductor.y + lp.y, conductor.z + lp.z);
      if (s === 0) ctx.moveTo(p.sx, p.sy); else ctx.lineTo(p.sx, p.sy);
    }
    ctx.stroke();

    // 2) per-gate node: a small square sized by total flow; green pip ∝ pass, red
    //    ∝ kill. flash on a fresh pass/kill (event-driven, decays).
    for (let i = 0; i < m; i++) {
      const g = funnel[i];
      const st = segState[i];
      const lp = lanePoint(g._u);
      const p = project(conductor.x + lp.x, conductor.y + lp.y, conductor.z + lp.z);
      if (st) { st.flash = Math.max(0, st.flash - dt * 1.2); st.killFlash = Math.max(0, st.killFlash - dt * 1.2); }
      const tot = Math.max(1, g.total || (g.pass_n + g.kill_n) || 1);
      const passFrac = (g.pass_n || 0) / tot;
      const baseR = 2.0 + Math.min(2.4, Math.log10(1 + tot) * 1.4);
      const r = (baseR + (st ? st.flash * 2.4 : 0)) * (p.persp || 1);
      // gate marker body — neutral, brightens on flow.
      const a = 0.32 + (st ? st.flash * 0.5 : 0);
      ctx.fillStyle = rgba([0x9a, 0xa4, 0xc0], a);
      ctx.fillRect(p.sx - r * 0.5, p.sy - r * 0.5, r, r);
      // pass (green) and kill (red) wedges flanking the marker — the funnel read.
      const gw = r * (0.5 + passFrac);
      ctx.fillStyle = rgba([0x6c, 0xe8, 0x9a], 0.30 + (st ? st.flash * 0.55 : 0));
      ctx.fillRect(p.sx - r * 0.5 - 2, p.sy - gw * 0.5, 1.6, gw);
      if ((g.kill_n || 0) > 0 || (st && st.killFlash > 0)) {
        const kw = r * (0.3 + (1 - passFrac));
        ctx.fillStyle = rgba([0xff, 0x7a, 0x7a], 0.28 + (st ? st.killFlash * 0.6 : 0));
        ctx.fillRect(p.sx + r * 0.5 + 0.4, p.sy - kw * 0.5, 1.6, kw);
      }
      // gate id label (G1..G8) — tiny, only when the lane is reasonably front-facing.
      if ((p.depth || 0) > -0.2) {
        ctx.fillStyle = rgba([0xc4, 0xca, 0xd6], 0.5);
        ctx.font = '700 6px JetBrains Mono, monospace';
        ctx.textAlign = 'center';
        ctx.fillText('G' + g.gate_id, p.sx, p.sy - r * 0.7 - 2);
      }
    }

    // 3) directed flow pulse running G1→G8 (event-driven speed: faster on a fresh
    //    decision flash) so the pipeline DIRECTION is unmistakable.
    const flash = window.PolarisGlobe_decisionFlash || 0;
    drawLane._t = ((drawLane._t || 0) + dt * (0.18 + flash * 0.5)) % 1;
    const u = drawLane._t;
    const lp = lanePoint(u);
    const pp = project(conductor.x + lp.x, conductor.y + lp.y, conductor.z + lp.z);
    const pr = 2.2 * (pp.persp || 1);
    const gr = ctx.createRadialGradient(pp.sx, pp.sy, 0, pp.sx, pp.sy, pr);
    gr.addColorStop(0, rgba([0xbf, 0xd4, 0xff], 0.6 + flash * 0.3));
    gr.addColorStop(1, rgba([0xbf, 0xd4, 0xff], 0));
    ctx.fillStyle = gr;
    ctx.beginPath(); ctx.arc(pp.sx, pp.sy, pr, 0, 6.2832); ctx.fill();

    // 4) transient kill motes — a red dot at the killing gate that fades outward.
    for (let i = killMotes.length - 1; i >= 0; i--) {
      const km = killMotes[i];
      km.t += dt / km.dur;
      if (km.t >= 1) { killMotes.splice(i, 1); continue; }
      const anch = gateAnchors[km.gate_id];
      if (!anch) continue;
      const p = project(conductor.x + anch.x, conductor.y + anch.y, conductor.z + anch.z);
      const a = (1 - km.t) * 0.8;
      const rr = (2 + km.t * 5) * (p.persp || 1);
      const g2 = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, rr);
      g2.addColorStop(0, rgba([0xff, 0x7a, 0x7a], a));
      g2.addColorStop(1, rgba([0xff, 0x7a, 0x7a], 0));
      ctx.fillStyle = g2;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, rr, 0, 6.2832); ctx.fill();
    }
  }

  // Wrap drawFlows so the lane draws every frame (after links, before/under nodes —
  // globe-core calls drawFlows before drawNode). Also ingest the graph payload by
  // wrapping setGraph so deltas are captured on each 1s poll.
  function install() {
    const baseDraw = window.PolarisGlobe_drawFlows;
    const baseSet = window.PolarisGlobe_setGraph;
    if (typeof baseDraw !== 'function' || typeof baseSet !== 'function') {
      return setTimeout(install, 50);   // globe-flows/core not ready yet
    }
    window.PolarisGlobe_setGraph = function (d) {
      baseSet(d);
      try { ingest(d); } catch (e) { /* display-only */ }
    };
    window.PolarisGlobe_drawFlows = function (ctx, project, now, dt, helpers) {
      baseDraw(ctx, project, now, dt, helpers);
      try { drawLane(ctx, project, now, dt); } catch (e) { /* display-only */ }
    };
  }
  install();
})();
