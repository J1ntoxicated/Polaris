/* Polaris Neural Cloud — E4 conductor satellites (cross-cutting state rings)
 *
 * Jin E4 follow-up (2026-05-31): the venue-specific clusters (pos/watch/mkt) are
 * the 3 galaxies. The cross-cutting system state — strategies, regime, AI/learners,
 * exit patterns, dimension axes and the square health/action/tally rings — are NOT
 * tied to one venue, so they orbit the CENTRAL conductor as small inclined-ring
 * satellites instead of being hidden (the old roleForCluster returned null).
 *
 *   strat  → strategy ring        (coral)
 *   reg    → regime ring          (khaki)   ← the macro/asset-class regime context
 *   orbit  → AI / learner ring    (blue)    learners · gpt providers · ai_judges
 *   exit   → exit-pattern ring    (pink)    canonical TP/SL/TRAIL/...
 *   axis   → dimension ring       (peach)   session × liq × crisis
 *   obs    → health ring (square) (khaki)
 *   action → decision ring (square)(magenta) gate decisions / order intents
 *   exit_tally → tally ring (square)(rose)
 *
 * Each family rides its own ring radius + inclination; nodes revolve slowly so the
 * hub reads as an AI conductor "managing" the galaxies. Thin white beams (drawn in
 * globe-flows.js) link conductor↔satellite and satellite↔galaxy.
 *
 * Display-only — touches nothing in the trading path.
 */
(function () {
  if (!document.getElementById('sphere')) return;

  const nodes = window.PolarisGlobe_nodes;
  const nodeById = window.PolarisGlobe_nodeById;
  const conductor = window.PolarisGlobe_conductor;
  const hash01 = window.PolarisGlobe_hash01;
  const rgba = window.PolarisGlobe_rgba;

  // Family definitions. Jin 2026-06-23 ORBITAL (per-gate distinct orbits): the 8
  // gate/layer families NO LONGER share one latitude-banded shell. Each family now
  // rides its OWN orbital RING — a distinct inclination (tilt), ascending-node
  // rotation, radius and revolution speed/phase — so the 8 gates read as 8 separate
  // orbits (an atomic / gyroscope model), and the union of the tilted rings traces
  // out a SPHERE silhouette that gently floats. Jin '게이트별로 노드 궤도 다르게 …
  // 그 궤도에서 구 형성하면서 플로팅'. color = NEON family tint (밝고 채도 높은 8색
  // 한눈 구별), base = node size, shape = dot/square.
  //   inc   = orbital-plane tilt (rad)   — distinct per gate
  //   asc   = ascending-node spin (rad)  — rotates the tilted plane around Y
  //   rscale= ring radius ×R_MIDDLE      — near 1 so all rings co-trace one sphere
  //   ospeed= revolution speed (rad/s)   — distinct per gate (+ sign = direction)
  //   phase = starting longitude offset  — distinct per gate
  const R_MIDDLE = (window.PolarisGlobe_shellR && window.PolarisGlobe_shellR.middle) || 0.55;
  const PI = Math.PI;
  const FAMILIES = {
    exit:   { color: [0xf0, 0x22, 0x22], base: 2.0, label: 'EXIT',       shape: null,     inc: 0.30, asc: 0.00,        rscale: 1.00, ospeed:  0.16, phase: 0.10 }, // red
    strat:  { color: [0xff, 0x7a, 0x00], base: 2.6, label: 'STRATEGY',   shape: null,     inc: 1.05, asc: PI * 0.25,   rscale: 0.92, ospeed: -0.13, phase: 0.55 }, // orange
    reg:    { color: [0xff, 0xd0, 0x00], base: 2.6, label: 'REGIME',     shape: null,     inc: 1.40, asc: PI * 0.50,   rscale: 0.98, ospeed:  0.10, phase: 0.85 }, // yellow
    obs:    { color: [0x3d, 0xe8, 0x4a], base: 1.8, label: 'HEALTH',     shape: 'square', inc: 0.62, asc: PI * 0.75,   rscale: 0.86, ospeed: -0.19, phase: 0.30 }, // green
    axis:   { color: [0x12, 0xe5, 0xe5], base: 1.8, label: 'DIMENSION',  shape: null,     inc: 1.18, asc: PI * 1.00,   rscale: 1.04, ospeed:  0.14, phase: 0.65 }, // cyan
    orbit:  { color: [0x3d, 0x6e, 0xff], base: 2.2, label: 'AI · LEARN', shape: null,     inc: 0.88, asc: PI * 1.25,   rscale: 0.94, ospeed: -0.11, phase: 0.05 }, // blue
    action: { color: [0xb4, 0x3c, 0xff], base: 2.0, label: 'DECISION',   shape: 'square', inc: 1.30, asc: PI * 1.55,   rscale: 0.90, ospeed:  0.18, phase: 0.45 }, // violet
    exit_tally: { color: [0xff, 0x5c, 0xc8], base: 1.6, label: 'TALLY',  shape: 'square', inc: 0.46, asc: PI * 1.80,   rscale: 1.02, ospeed: -0.15, phase: 0.75 }, // pink
  };
  const FAMILY_ORDER = ['reg', 'strat', 'exit', 'orbit', 'axis', 'action', 'obs', 'exit_tally'];
  // member ids per family this frame → used to spread nodes evenly on the ring.
  const familyMembers = {};

  // Create/update a satellite node from a backend node. Returns the node (so the
  // core's nodeByIndex + chains/lifecycle paths can resolve it), or null if the
  // cluster isn't a known satellite family.
  function satNodeFor(bn) {
    const fam = FAMILIES[bn.cluster];
    if (!fam) return null;
    const id = bn.id;
    let n = nodeById.get(id);
    if (!n) {
      // spawn from the conductor so it eases out onto its ring (no jump-in)
      n = { id, x: conductor.x, y: conductor.y, z: conductor.z, pulse: 0, flash: 0, sat: true, born: performance.now() };
      nodeById.set(id, n);
      nodes.push(n);
    }
    n.sat = true;
    n.gx = null;                       // not a galaxy node (dimFor handles sat separately)
    n.fam = bn.cluster;
    n.cluster = bn.cluster;
    n.label = bn.label || id;
    n.ticker = bn.ticker || null;      // satellites are non-instrument → usually null
    n.pnl = bn.pnl_usd || 0;
    n.intensity = bn.intensity != null ? bn.intensity : 0.4;
    n.state = bn.state || 'lit';
    n.color = fam.color;
    n.base = fam.base;
    n.shape = fam.shape;
    n.phase = (bn.phase != null) ? bn.phase : hash01(id + '~p');
    // backend supplies orbit motion for obs/action/orbit/axis/exit_tally; derive a
    // stable fallback from the id-hash for reg/strat/exit so motion is deterministic.
    n._ospeed = (bn.orbit_speed != null) ? bn.orbit_speed : (0.05 + hash01(id + '~s') * 0.10);
    (familyMembers[bn.cluster] = familyMembers[bn.cluster] || []).push(id);
    return n;
  }
  window.PolarisGlobe_satNodeFor = satNodeFor;

  // After the core finished ingesting nodes: pin each sat node's stable slot on its
  // family ring (slot index derived from id-hash → order-independent) and prune
  // sat nodes that vanished this refresh.
  function satFinalize(liveIds) {
    // assign a deterministic angular slot per node (sorted by id-hash so it's stable
    // regardless of backend array order — the reload-drift guarantee for satellites).
    for (const fam of FAMILY_ORDER) {
      const ids = familyMembers[fam];
      if (!ids || !ids.length) continue;
      ids.sort((a, b) => hash01(a + '~o') - hash01(b + '~o'));
      const m = ids.length;
      for (let i = 0; i < m; i++) {
        const n = nodeById.get(ids[i]);
        if (n) n._slot = (i + 0.5) / m;       // [0,1) evenly spaced base angle
      }
    }
    // prune stale satellites
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      if (n.sat && !liveIds.has(n.id)) { nodeById.delete(n.id); nodes.splice(i, 1); }
    }
    // reset per-frame member accumulator for the next refresh
    for (const k in familyMembers) delete familyMembers[k];
  }
  window.PolarisGlobe_satFinalize = satFinalize;

  // ── Per-gate orbital placement (Jin 2026-06-23) ──────────────────────────────
  // Each of the 8 gate/layer families rides its OWN tilted orbital RING (distinct
  // inclination `inc` + ascending-node rotation `asc` + radius `rscale` + speed
  // `ospeed`), NOT one shared latitude-banded shell. A point on a family ring is a
  // circle in the ring plane (radius R), tilted by `inc` then rotated by `asc`
  // about Y, then offset to the conductor. The union of the 8 rings co-traces one
  // floating sphere (rscale≈1) while reading as 8 separate orbits (gyroscope).
  //   ringPoint(F, ang) → {x,y,z} in world space.
  function ringPoint(F, ang) {
    const R = R_MIDDLE * (F.rscale || 1.0);
    // base circle in XZ plane
    let x = Math.cos(ang) * R;
    let y = 0;
    let z = Math.sin(ang) * R;
    // incline the plane about the X axis by `inc` (tilts the ring)
    const ci = Math.cos(F.inc), si = Math.sin(F.inc);
    let y2 = y * ci - z * si;
    let z2 = y * si + z * ci;
    y = y2; z = z2;
    // rotate the (now tilted) plane about Y by `asc` (spreads ascending nodes)
    const ca = Math.cos(F.asc), sa = Math.sin(F.asc);
    const x2 = x * ca + z * sa;
    const z3 = -x * sa + z * ca;
    return { x: conductor.x + x2, y: conductor.y + y, z: conductor.z + z3 };
  }

  // Revolve each satellite along its family's distinct orbital ring each frame.
  // Stable slot + family phase keep members evenly spread and order-independent;
  // ospeed (per-gate, signed) gives each ring its own revolution rate/direction.
  function satTick(now, dt) {
    const t = now / 1000;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (!n.sat) continue;
      const fam = FAMILIES[n.fam];
      if (!fam) continue;
      const ang = (n._slot || 0) * 6.283185
        + t * (fam.ospeed || 0.08)
        + (fam.phase || 0) * 6.283185;
      const p = ringPoint(fam, ang);
      n.hx = p.x; n.hy = p.y; n.hz = p.z;
    }
  }
  window.PolarisGlobe_satTick = satTick;

  // Faint orbital-ring guide per family (full 3D tilt) + a drifting family label.
  function drawSatRings(ctx, project, now, helpers) {
    const rg = (helpers && helpers.rgba) || rgba;
    const t = now / 1000;
    for (const fam of FAMILY_ORDER) {
      const F = FAMILIES[fam];
      // skip families with no live members this refresh (keeps the hub uncluttered)
      let any = false;
      for (let i = 0; i < nodes.length; i++) { if (nodes[i].sat && nodes[i].fam === fam) { any = true; break; } }
      if (!any) continue;
      ctx.strokeStyle = rg(F.color, 0.16);
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      let started = false;
      const STEPS = 64;
      for (let s = 0; s <= STEPS; s++) {
        const ang = (s / STEPS) * 6.283185;
        const wp = ringPoint(F, ang);
        const p = project(wp.x, wp.y, wp.z);
        if (!started) { ctx.moveTo(p.sx, p.sy); started = true; }
        else ctx.lineTo(p.sx, p.sy);
      }
      ctx.stroke();
      // family label drifting slowly along its own ring (reads as a rotating dial)
      const la = t * 0.05 + (FAMILY_ORDER.indexOf(fam) * 0.9);
      const wl = ringPoint(F, la);
      const lp = project(wl.x, wl.y, wl.z);
      ctx.fillStyle = rg(F.color, 0.5);
      ctx.font = '700 7px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(F.label, lp.sx, lp.sy - 4);
    }
  }
  window.PolarisGlobe_drawSatRings = drawSatRings;
})();
