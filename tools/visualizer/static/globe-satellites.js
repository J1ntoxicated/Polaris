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

  // Family ring definitions. radius = orbit radius around the conductor (scene
  // units), inc = ring inclination, color = subtle family tint, base = node size.
  const FAMILIES = {
    reg:    { color: [0xd7, 0xd7, 0x87], radius: 0.30, inc: 0.10, base: 2.6, label: 'REGIME',     shape: null },
    strat:  { color: [0xff, 0x9f, 0x87], radius: 0.40, inc: 0.55, base: 2.6, label: 'STRATEGY',   shape: null },
    exit:   { color: [0xff, 0x87, 0xd7], radius: 0.34, inc: -0.85, base: 2.0, label: 'EXIT',      shape: null },
    orbit:  { color: [0x9f, 0xc7, 0xff], radius: 0.50, inc: 0.95, base: 2.2, label: 'AI · LEARN', shape: null },
    axis:   { color: [0xff, 0xd7, 0xc7], radius: 0.58, inc: -0.35, base: 1.8, label: 'DIMENSION', shape: null },
    action: { color: [0xd7, 0x87, 0xd7], radius: 0.46, inc: 1.25, base: 2.0, label: 'DECISION',   shape: 'square' },
    obs:    { color: [0xc8, 0xd0, 0x90], radius: 0.26, inc: -1.30, base: 1.8, label: 'HEALTH',     shape: 'square' },
    exit_tally: { color: [0xff, 0x87, 0xaf], radius: 0.66, inc: 0.40, base: 1.6, label: 'TALLY',  shape: 'square' },
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

  // Revolve satellites around the conductor each frame → set their (moving) home.
  function satTick(now, dt) {
    const t = now / 1000;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (!n.sat) continue;
      const fam = FAMILIES[n.fam];
      if (!fam) continue;
      const ang = (n._slot || 0) * 6.283185 + t * (n._ospeed || 0.08) + (n.phase || 0) * 6.283185;
      const ci = Math.cos(fam.inc), si = Math.sin(fam.inc);
      const lx = Math.cos(ang) * fam.radius;
      const lz = Math.sin(ang) * fam.radius;
      // tilt the ring around the X axis by the family inclination
      n.hx = conductor.x + lx;
      n.hy = conductor.y + lz * si;
      n.hz = conductor.z + lz * ci;
    }
  }
  window.PolarisGlobe_satTick = satTick;

  // Faint inclined ring guides + a family tick label (behind nodes/flows).
  function drawSatRings(ctx, project, now, helpers) {
    const rg = (helpers && helpers.rgba) || rgba;
    const t = now / 1000;
    for (const fam of FAMILY_ORDER) {
      const F = FAMILIES[fam];
      // skip families with no live members this refresh (keeps the hub uncluttered)
      let any = false;
      for (let i = 0; i < nodes.length; i++) { if (nodes[i].sat && nodes[i].fam === fam) { any = true; break; } }
      if (!any) continue;
      const ci = Math.cos(F.inc), si = Math.sin(F.inc);
      ctx.strokeStyle = rg(F.color, 0.16);
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      let started = false, lx0 = 0, ly0 = 0;
      const STEPS = 48;
      for (let s = 0; s <= STEPS; s++) {
        const ang = (s / STEPS) * 6.283185;
        const x = conductor.x + Math.cos(ang) * F.radius;
        const zr = Math.sin(ang) * F.radius;
        const y = conductor.y + zr * si;
        const z = conductor.z + zr * ci;
        const p = project(x, y, z);
        if (!started) { ctx.moveTo(p.sx, p.sy); started = true; lx0 = p.sx; ly0 = p.sy; }
        else ctx.lineTo(p.sx, p.sy);
      }
      ctx.stroke();
      // family label at the ring's leading edge (slow drift so it reads as a dial)
      const la = t * 0.05 + (FAMILY_ORDER.indexOf(fam) * 0.9);
      const lp = project(
        conductor.x + Math.cos(la) * F.radius,
        conductor.y + Math.sin(la) * F.radius * si,
        conductor.z + Math.sin(la) * F.radius * ci
      );
      ctx.fillStyle = rg(F.color, 0.5);
      ctx.font = '700 7px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(F.label, lp.sx, lp.sy - 4);
    }
  }
  window.PolarisGlobe_drawSatRings = drawSatRings;
})();
