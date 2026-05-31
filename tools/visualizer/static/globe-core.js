/* Polaris Neural Cloud — E4 galaxy globe (core layout + render loop)
 *
 * Jin E4 redesign (2026-05-31): the old 14-tier nested-sphere renderer became a
 * static blob with no live pathways. This is the rewrite — 3 exchange galaxies
 * orbiting a central AI conductor hub:
 *
 *   OKX     → Spot Crypto   (cyan)
 *   Capital → CFD long/short (violet)
 *   Alpaca  → US Equity     (gold)
 *
 * Each galaxy is a cluster of its tradable/position/strategy nodes; the conductor
 * is the visual centre that "manages" all three. globe-flows.js layers the live
 * particle pathways, pulses, flashes and capital-lifecycle arcs on top.
 *
 * Camera: drag to orbit, wheel to zoom, click a galaxy to fly the camera into it
 * (wired to board's exchange selector via window.PolarisGlobe.focusExchange).
 * Display-only — no trading behaviour is touched anywhere in this file.
 */
(function () {
  const canvas = document.getElementById('sphere');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  let dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0, CX = 0, CY = 0;
  function fit() {
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = Math.floor(W * dpr); canvas.height = Math.floor(H * dpr);
    CX = W / 2; CY = H / 2;
  }
  window.addEventListener('resize', fit);
  fit();

  // ── Galaxy definitions ─────────────────────────────────────────────────────
  // venueKey() maps backend `exchange` (3-char short: okx/cap/alp/bin) → galaxy.
  // Binance is crypto data only → folded into the OKX (crypto) galaxy.
  const GALAXIES = {
    okx:     { key: 'okx',     label: 'OKX · SPOT CRYPTO',  theme: [0x5f, 0xdf, 0xff], azimuth: -Math.PI * 0.66 },
    capital: { key: 'capital', label: 'CAPITAL · CFD L/S',  theme: [0xa8, 0x7c, 0xff], azimuth:  Math.PI * 0.5 },
    alpaca:  { key: 'alpaca',  label: 'ALPACA · US EQUITY', theme: [0xff, 0xc8, 0x4f], azimuth:  Math.PI * 0.06 },
  };
  const GALAXY_ORDER = ['okx', 'capital', 'alpaca'];
  const CONDUCTOR_THEME = [0x9f, 0xc7, 0xff];

  function venueKey(ex) {
    const e = (ex || '').toLowerCase().slice(0, 3);
    if (e === 'okx' || e === 'bin') return 'okx';      // crypto venues → OKX galaxy
    if (e === 'cap') return 'capital';
    if (e === 'alp') return 'alpaca';
    return null;                                        // infra / non-venue node
  }
  // Normalize the board selector arg ('okx'|'capital'|'alpaca'|'all') → galaxy key.
  function focusKey(which) {
    if (which === 'okx' || which === 'capital' || which === 'alpaca') return which;
    return null;
  }
  window.PolarisGlobe_venueKey = venueKey;   // shared with globe-flows.js
  window.PolarisGlobe_galaxies = GALAXIES;

  function rgba(c, a) { return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
  function chainColor(pnl) {
    if (pnl > 0.0001) return [0x87, 0xff, 0xaf];   // green
    if (pnl < -0.0001) return [0xff, 0x87, 0x87];  // red
    return [0xff, 0xff, 0xff];                      // flat / white
  }

  // ── Scene state ────────────────────────────────────────────────────────────
  // Nodes carry a stable home position (galaxy-local) + a current animated
  // position. The conductor sits at the origin. Cluster nodes spiral around
  // their galaxy centre on a ring whose radius depends on the node's "tier"
  // role (pos = inner, strat = mid, watch/mkt = outer halo).
  const nodes = [];               // {id, gx, role, hx,hy,hz, x,y,z, color, base, pulse, ...}
  const nodeById = new Map();     // id → node
  const nodeByIndex = [];         // positional index (parallel to backend nodes[])
  const galaxyState = {};         // key → {cx,cy,cz, theme, count, pnl, pulse, hot}
  let conductor = { x: 0, y: 0, z: 0, pulse: 0, beat: 0 };

  GALAXY_ORDER.forEach((k, i) => {
    const g = GALAXIES[k];
    // place galaxy centres on a wide ring around the conductor (3D)
    const az = g.azimuth;
    const R = 1.0;
    galaxyState[k] = {
      key: k, theme: g.theme, label: g.label,
      cx: Math.cos(az) * R, cy: (i - 1) * 0.18, cz: Math.sin(az) * R,
      count: 0, pnl: 0, pulse: 0, hot: 0,
    };
  });

  function roleRadius(role) {
    if (role === 'pos') return 0.20;
    if (role === 'watch') return 0.42;
    return 0.60;                       // mkt / universe halo
  }
  // venue-specific clusters live in the 3 galaxies. strat/reg/exit/orbit/axis/
  // obs/action/exit_tally are cross-cutting → conductor satellites (globe-satellites.js).
  function roleForCluster(cluster) {
    if (cluster === 'pos') return 'pos';
    if (cluster === 'watch') return 'watch';
    if (cluster === 'mkt') return 'mkt';
    return null;                       // cross-cutting → handled as a satellite
  }

  // Deterministic pseudo-random from a string (stable node placement per id).
  // Used to pin a node's home position to its id ONLY — never to its position in
  // the backend nodes[] array — so a re-ordered 2s refresh can't drift a node to
  // a new home (the old "reload backs the node away" glitch).
  function hash01(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return ((h >>> 0) % 100000) / 100000;
  }

  // Rebuild scene nodes from a fresh backend graph payload.
  function setGraph(d) {
    const backendNodes = d.nodes || [];
    nodeByIndex.length = 0;
    const liveIds = new Set();
    // reset per-galaxy aggregates
    GALAXY_ORDER.forEach((k) => { galaxyState[k].count = 0; galaxyState[k].pnl = 0; galaxyState[k].mktCount = 0; galaxyState[k].activeCount = 0; });

    for (let idx = 0; idx < backendNodes.length; idx++) {
      const bn = backendNodes[idx];
      const role = roleForCluster(bn.cluster);
      const gx = venueKey(bn.exchange);
      // cross-cutting cluster (or no role) → let the satellites module own it.
      if (role === null || gx === null) {
        if (window.PolarisGlobe_satNodeFor) {
          const sn = window.PolarisGlobe_satNodeFor(bn);
          if (sn) { liveIds.add(sn.id); nodeByIndex[idx] = sn; continue; }
        }
        nodeByIndex[idx] = null;
        continue;
      }
      const gs = galaxyState[gx];
      const id = bn.id;
      liveIds.add(id);
      let n = nodeById.get(id);
      if (!n) {
        n = { id, x: gs.cx, y: gs.cy, z: gs.cz, pulse: 0, flash: 0, born: performance.now() };
        nodeById.set(id, n);
        nodes.push(n);
      }
      // Stable home position from TWO id-hashes ONLY (order-independent). h1 →
      // azimuth around the galaxy ring, h2 → tilt + radial jitter, evenly spread.
      const rad = roleRadius(role);
      const h1 = hash01(id + '~a');
      const h2 = hash01(id + '~b');
      const ang = h1 * 6.283185;                         // uniform azimuth
      const tilt = (h2 - 0.5) * 1.2;                     // band thickness
      const rr = rad * (0.82 + h2 * 0.30);               // slight radial spread
      n.gx = gx; n.role = role; n.cluster = bn.cluster;
      n.label = bn.label || bn.ticker || id;
      n.ticker = bn.ticker;
      n.pnl = bn.pnl_usd || 0;
      n.direction = bn.direction;
      n.intensity = bn.intensity != null ? bn.intensity : 0.4;
      n.state = bn.state || 'lit';
      n.hx = gs.cx + Math.cos(ang) * rr;
      n.hy = gs.cy + Math.sin(tilt) * rr * 0.8;
      n.hz = gs.cz + Math.sin(ang) * rr;
      n.color = (role === 'pos') ? chainColor(n.pnl) : gs.theme;
      n.base = role === 'pos' ? 3.4 : 1.9;
      // ── universe shell vs lit-up node ──────────────────────────────────────
      // Backend now emits the FULL tradable universe as mkt nodes with an
      // `active` flag (is_active=1 = bot trading focus = lightup candidate). The
      // huge dormant remainder (hundreds–thousand+) is the dim point-cloud: it
      // renders in one cheap batched pass (no gradient/glow, no per-node z-sort)
      // so 60fps holds. A node is "dim" only when it's a mkt node that is NOT
      // active and NOT firing — active/firing/pos/watch keep the rich drawNode
      // path (glow/lightup). Display-only; backend `active` flag drives this.
      n.active = (bn.active === true) || (role !== 'mkt');
      n.dim = (role === 'mkt') && !n.active && n.state !== 'firing';
      gs.count++;
      gs.pnl += n.pnl;
      // Jin: 전체 거래가능 티커(mkt universe) + active(불 들어온 봇 focus) 카운트.
      if (role === 'mkt') { gs.mktCount++; if (bn.active === true) gs.activeCount++; }
      nodeByIndex[idx] = n;
    }
    // satellites module finalises its own home rings + drops stale sat nodes.
    if (window.PolarisGlobe_satFinalize) window.PolarisGlobe_satFinalize(liveIds);
    // drop galaxy nodes no longer present (sat nodes are pruned by satFinalize)
    for (let i = nodes.length - 1; i >= 0; i--) {
      if (nodes[i].sat) continue;
      if (!liveIds.has(nodes[i].id)) { nodeById.delete(nodes[i].id); nodes.splice(i, 1); }
    }
  }
  window.PolarisGlobe_setGraph = setGraph;
  window.PolarisGlobe_nodes = nodes;
  window.PolarisGlobe_nodeById = nodeById;
  window.PolarisGlobe_nodeByIndex = nodeByIndex;
  window.PolarisGlobe_galaxyState = galaxyState;
  window.PolarisGlobe_conductor = conductor;
  window.PolarisGlobe_hash01 = hash01;          // shared with globe-satellites.js
  window.PolarisGlobe_rgba = rgba;

  // ── Camera ─────────────────────────────────────────────────────────────────
  let yaw = 0.4, pitch = 0.32, autoSpin = true;
  let zoom = 1.0, targetZoom = 1.0;
  let _focus = null;                                         // focused galaxy key
  // Camera pan: current (x/y/z) eased toward target (tx/ty/tz) = focused galaxy
  // centre, so clicking a galaxy flies the whole scene to recentre on it.
  const pan = { x: 0, y: 0, z: 0, tx: 0, ty: 0, tz: 0 };

  function project(x, y, z) {
    // pan recentres on a focused galaxy
    x -= pan.x; y -= pan.y; z -= pan.z;
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    let rx = x * cy - z * sy;
    let rz = x * sy + z * cy;
    let ry = y * cp - rz * sp;
    rz = y * sp + rz * cp;
    const scale = Math.min(W, H) * 0.42 * zoom;
    const persp = 1 / (1 + rz * 0.25);
    return { sx: CX + rx * scale * persp, sy: CY + ry * scale * persp, depth: rz, persp };
  }

  // ── Render loop ──────────────────────────────────────────────────────────────
  let last = performance.now();
  let starfield = null;
  function buildStars() {
    starfield = [];
    for (let i = 0; i < 140; i++) {
      starfield.push({ x: Math.random() * W, y: Math.random() * H,
                       r: Math.random() * 1.2 + 0.2, tw: Math.random() * 6.28 });
    }
  }

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    // board.js startSphereWatchdog() reloads the page if this heartbeat stalls
    // for 10s — keep it ticking every frame so a live globe is never killed.
    window.__sphereHB = (window.__sphereHB || 0) + 1;
    if (autoSpin && !dragging) yaw += dt * 0.08;
    // ease camera toward zoom / pan targets
    zoom += (targetZoom - zoom) * Math.min(1, dt * 5);
    pan.x += (pan.tx - pan.x) * Math.min(1, dt * 4);
    pan.y += (pan.ty - pan.y) * Math.min(1, dt * 4);
    pan.z += (pan.tz - pan.z) * Math.min(1, dt * 4);

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);
    if (!starfield || starfield.length === 0 || starfield[0].x > W + 50) buildStars();
    // starfield backdrop
    for (const s of starfield) {
      const a = 0.18 + 0.18 * Math.sin(now / 700 + s.tw);
      ctx.fillStyle = `rgba(150,170,210,${a})`;
      ctx.fillRect(s.x, s.y, s.r, s.r);
    }

    conductor.beat = 0.5 + 0.5 * Math.sin(now / 600);
    conductor.pulse = Math.max(0, conductor.pulse - dt * 1.8);

    // satellites revolve around the conductor: recompute their home each frame.
    if (window.PolarisGlobe_satTick) window.PolarisGlobe_satTick(now, dt);

    // Collect drawables in TWO passes so the universe shell scales to 1000+
    // nodes at 60fps:
    //   • dim[]  — dormant mkt universe (the point-cloud). Cheap: no gradient,
    //              no z-sort (uniform tiny grey dots → order invisible), LOD-
    //              culled. Positions are still eased + projected so flows that
    //              reference them stay correct and the cloud rotates with scene.
    //   • draw[] — lit/active/firing + pos/watch/satellites. z-sorted, rich
    //              drawNode (glow/lightup) — the nodes that must "pop".
    const draw = [];
    const dim = [];
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      // satellites snap faster to their (moving) orbital home; galaxy nodes ease.
      const k = Math.min(1, dt * (n.sat ? 6 : 3));
      n.x += (n.hx - n.x) * k;
      n.y += (n.hy - n.y) * k;
      n.z += (n.hz - n.z) * k;
      n.pulse = Math.max(0, n.pulse - dt * 2.0);
      n.flash = Math.max(0, n.flash - dt * 1.4);
      const p = project(n.x, n.y, n.z);
      n._screen = p;     // keep updated so flows/chains can reference any node
      // A dim node with a transient pulse/flash (e.g. flashTicker hit) is
      // promoted to the rich pass so its flash actually shows.
      if (n.dim && n.pulse <= 0.01 && n.flash <= 0.01) dim.push({ n, p });
      else draw.push({ kind: 'node', n, p });
    }
    draw.sort((a, b) => a.p.depth - b.p.depth);

    // galaxy halo + label (behind nodes of that galaxy — draw first, dim)
    drawGalaxyHalos(now);

    // dim universe shell — drawn BEHIND the lit nodes + flows so the bright
    // signals read on top of the cloud. One batched cheap pass.
    drawDimCloud(dim);

    // Jin E6: 위성 궤도선 제거 (정신없음). drawSatRings 정의는 globe-satellites.js
    // 에 남겨두되 호출하지 않음 — 위성 노드(회전 satTick)는 그대로 유지.

    // synapse + particle pathways (globe-flows.js draws between projected nodes)
    if (window.PolarisGlobe_drawFlows) {
      window.PolarisGlobe_drawFlows(ctx, project, now, dt, { rgba });
    }

    for (const d of draw) drawNode(d.n, d.p, now);
    // Jin: 컨덕터 제거(거슬림 — 개념은 알고 있으니 시각화 불필요). 위성은 보이지 않는
    // 중심(origin)을 돈다. drawConductor 정의는 남겨둠(복원 필요 시 이 줄만 되살리면 됨).
    // drawConductor(now);

    ctx.restore();
    requestAnimationFrame(frame);
  }

  function dimFor(gx) {
    if (!_focus) return 1.0;
    return gx === _focus ? 1.0 : 0.22;
  }

  function drawGalaxyHalos(now) {
    for (const k of GALAXY_ORDER) {
      const gs = galaxyState[k];
      gs.pulse = Math.max(0, gs.pulse - 0.016);
      const p = project(gs.cx, gs.cy, gs.cz);
      const d = dimFor(k);
      const rad = Math.min(W, H) * 0.18 * zoom * p.persp;
      const grad = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, rad);
      const glow = (0.10 + gs.pulse * 0.5 + gs.hot * 0.3) * d;
      grad.addColorStop(0, rgba(gs.theme, glow));
      grad.addColorStop(1, rgba(gs.theme, 0));
      ctx.fillStyle = grad;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, rad, 0, 6.2832); ctx.fill();
      gs.hot = Math.max(0, gs.hot - 0.01);
      // galaxy label
      ctx.save();
      ctx.globalAlpha = (0.4 + 0.3 * d) * (_focus && _focus !== k ? 0.4 : 1);
      ctx.fillStyle = rgba(gs.theme, 1);
      ctx.font = '700 9px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.fillText(gs.label, p.sx, p.sy - rad - 6);
      ctx.font = '8px JetBrains Mono, monospace';
      ctx.fillStyle = rgba([0xa8, 0xa8, 0xa8], 0.8 * d);
      const pnlStr = (gs.pnl >= 0 ? '+' : '') + gs.pnl.toFixed(1);
      ctx.fillText(`${gs.mktCount} tickers (${gs.activeCount} active) · ${pnlStr}`, p.sx, p.sy + rad + 12);
      ctx.restore();
      gs._screen = p;     // cached for hit-test
      gs._screenR = rad;
    }
  }

  // ── Dim universe cloud (cheap batched pass) ─────────────────────────────────
  // Renders the dormant tradable-universe shell as tiny low-alpha grey-tinted
  // squares. Cost minimisation:
  //   • single fillStyle per galaxy theme (≤3 setStyle calls), grouped, so the
  //     whole cloud is just fillRect calls (no arc/beginPath, no gradient).
  //   • LOD: when the cloud is large, drop the back hemisphere + off-screen +
  //     a deterministic fraction, scaling the kept count toward a budget so
  //     frame time stays flat regardless of universe size.
  // The dot still tints toward its galaxy so the 3 venues read as filled
  // galaxies; alpha is low so active/firing nodes pop on top.
  const DIM_BUDGET = 650;            // target max dim dots actually drawn / frame
  function drawDimCloud(dim) {
    if (dim.length === 0) return;
    // LOD stride: if more dim dots than the budget, draw every Nth (deterministic
    // by array index → stable, no flicker). Backend already caps Alpaca; this is
    // the front-stop so any universe size stays 60fps.
    const stride = dim.length > DIM_BUDGET ? Math.ceil(dim.length / DIM_BUDGET) : 1;
    // group by galaxy so we set fillStyle at most 3× (one per theme).
    const buckets = { okx: [], capital: [], alpaca: [] };
    for (let i = 0; i < dim.length; i += stride) {
      const d = dim[i];
      const p = d.p;
      // cull: behind-camera depth fade + off-screen (cheap rejects before fill).
      if (p.persp <= 0) continue;
      if (p.sx < -8 || p.sx > W + 8 || p.sy < -8 || p.sy > H + 8) continue;
      const b = buckets[d.n.gx];
      if (b) b.push(d);
    }
    for (const k of GALAXY_ORDER) {
      const b = buckets[k];
      if (!b || b.length === 0) continue;
      const theme = galaxyState[k].theme;
      const dd = dimFor(k);
      // muted: blend theme toward grey so it reads as background point-cloud.
      const r = (theme[0] + 150) >> 1, g = (theme[1] + 160) >> 1, bl = (theme[2] + 175) >> 1;
      for (let i = 0; i < b.length; i++) {
        const d = b[i];
        const p = d.p;
        // depth shading: nearer = slightly brighter/bigger (front hemisphere pop).
        const depthA = 0.5 + 0.5 * Math.max(-1, Math.min(1, -d.n.z));
        const a = (0.22 + 0.22 * depthA) * dd;   // Jin: dust 같지 않게 살짝 밝게
        ctx.fillStyle = `rgba(${r},${g},${bl},${a})`;
        const s = Math.max(0.85, 1.3 * zoom * p.persp);
        ctx.fillRect(p.sx - s * 0.5, p.sy - s * 0.5, s, s);
      }
    }
  }

  function drawNode(n, p, now) {
    const d = n.sat ? (_focus ? 0.6 : 1.0) : dimFor(n.gx);
    let r = n.base * zoom * p.persp;
    const firing = n.state === 'firing';
    const stateBoost = firing ? 0.5 : (n.state === 'lit' ? 0.2 : 0);
    let a = (0.35 + stateBoost + n.intensity * 0.3) * d;
    // live pulse / flash from flows
    if (n.pulse > 0) { r *= 1 + n.pulse * 0.9; a = Math.min(1, a + n.pulse * 0.5); }
    if (n.flash > 0) { a = Math.min(1, a + n.flash * 0.6); }
    const c = (n.role === 'pos') ? chainColor(n.pnl) : n.color;
    // ── signal lightup: a firing node = an ACTIVE signal → breathing halo so the
    //    holding ticker pops out of the cloud (the original neural-signal concept).
    if (firing) {
      const breathe = 0.5 + 0.5 * Math.sin(now / 360 + (n.phase || 0) * 6.283);
      const gr = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, r * 5);
      gr.addColorStop(0, rgba(c, (0.30 + breathe * 0.28) * d));
      gr.addColorStop(0.55, rgba(c, (0.10 + breathe * 0.10) * d));
      gr.addColorStop(1, rgba(c, 0));
      ctx.fillStyle = gr;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, r * 5, 0, 6.2832); ctx.fill();
    }
    if (n.flash > 0.3 || n.pulse > 0.3) {
      const g = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, r * 4);
      g.addColorStop(0, rgba(c, Math.min(0.6, (n.flash + n.pulse) * 0.5 * d)));
      g.addColorStop(1, rgba(c, 0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, r * 4, 0, 6.2832); ctx.fill();
    }
    ctx.fillStyle = rgba(c, a);
    if (n.shape === 'square') {
      const s = Math.max(0.8, r * 1.6);
      ctx.fillRect(p.sx - s / 2, p.sy - s / 2, s, s);
    } else {
      ctx.beginPath(); ctx.arc(p.sx, p.sy, Math.max(0.6, r), 0, 6.2832); ctx.fill();
    }
    // firing core: a crisp bright pip on top so the signal reads as "lit".
    if (firing) {
      ctx.fillStyle = rgba([0xff, 0xff, 0xff], Math.min(0.95, 0.5 + a) * d);
      ctx.beginPath(); ctx.arc(p.sx, p.sy, Math.max(0.5, r * 0.42), 0, 6.2832); ctx.fill();
    }
    n._screen = p;
  }

  function drawConductor(now) {
    const p = project(conductor.x, conductor.y, conductor.z);
    const beat = conductor.beat, pulse = conductor.pulse;
    const r = (6 + beat*2 + pulse*6) * zoom * p.persp;   // Jin: 확 축소 — 작은 무게추 코어
    const rot = now/4000, tilt = 0.42;
    const HOT=[255,240,210],MID=[255,150,40],COOL=[120,40,90],RING=[200,225,255];
    ctx.globalCompositeOperation='lighter';
    for(let i=0;i<3;i++){const rr=r*(1.4-i*0.14);const g=ctx.createRadialGradient(p.sx,p.sy,r*0.95,p.sx,p.sy,rr);
      g.addColorStop(0,'rgba(0,0,0,0)');g.addColorStop(0.45,`rgba(${HOT},${0.10-i*0.02})`);g.addColorStop(0.7,`rgba(${MID},${0.14-i*0.03})`);g.addColorStop(1,`rgba(${COOL},0)`);
      ctx.fillStyle=g;ctx.save();ctx.translate(p.sx,p.sy);ctx.scale(1,tilt);ctx.rotate(rot);ctx.beginPath();ctx.arc(0,0,rr,Math.PI,Math.PI*2);ctx.fill();ctx.restore();}
    ctx.globalCompositeOperation='source-over';
    const core=ctx.createRadialGradient(p.sx,p.sy,0,p.sx,p.sy,r*1.02);
    core.addColorStop(0,'rgba(2,3,8,1)');core.addColorStop(0.82,'rgba(2,3,8,1)');core.addColorStop(1,'rgba(2,3,8,0)');
    ctx.fillStyle=core;ctx.beginPath();ctx.arc(p.sx,p.sy,r*1.02,0,6.2832);ctx.fill();
    ctx.globalCompositeOperation='lighter';
    ctx.save();ctx.translate(p.sx,p.sy);ctx.scale(1,0.94);
    ctx.strokeStyle=`rgba(${RING},${0.85+beat*0.15})`;ctx.lineWidth=Math.max(1,r*0.05);ctx.beginPath();ctx.arc(0,0,r*1.06,0,6.2832);ctx.stroke();
    ctx.strokeStyle=`rgba(${RING},0.35)`;ctx.lineWidth=Math.max(0.6,r*0.02);ctx.beginPath();ctx.arc(0,0,r*1.13,0,6.2832);ctx.stroke();ctx.restore();
    for(let i=0;i<3;i++){const rr=r*(1.4-i*0.14);[['left',0.22],['right',0.07]].forEach(([side,baseA])=>{
      const g=ctx.createRadialGradient(p.sx,p.sy,r*0.95,p.sx,p.sy,rr);g.addColorStop(0,'rgba(0,0,0,0)');
      g.addColorStop(0.45,`rgba(${HOT},${baseA-i*0.03})`);g.addColorStop(0.7,`rgba(${MID},${baseA*0.9-i*0.03})`);g.addColorStop(1,`rgba(${COOL},0)`);
      ctx.fillStyle=g;ctx.save();ctx.translate(p.sx,p.sy);ctx.scale(1,tilt);ctx.rotate(rot);
      const a0=side==='left'?Math.PI*0.5:Math.PI*1.5,a1=side==='left'?Math.PI*1.5:Math.PI*2.5;ctx.beginPath();ctx.arc(0,0,rr,a0,a1);ctx.fill();ctx.restore();});}
    const au=ctx.createRadialGradient(p.sx,p.sy,r,p.sx,p.sy,r*1.9);au.addColorStop(0,`rgba(${MID},${0.04+pulse*0.14})`);au.addColorStop(0.5,'rgba(159,199,255,0.04)');au.addColorStop(1,'rgba(0,0,0,0)');
    ctx.fillStyle=au;ctx.beginPath();ctx.arc(p.sx,p.sy,r*1.9,0,6.2832);ctx.fill();
    ctx.globalCompositeOperation='source-over';
    ctx.fillStyle=rgba(CONDUCTOR_THEME,0.9);ctx.font='700 8px JetBrains Mono, monospace';ctx.textAlign='center';
    ctx.fillText('CONDUCTOR',p.sx,p.sy+r+12);
    conductor._screen=p;
  }

  // ── Interaction: drag orbit, wheel zoom, click galaxy fly-in ─────────────────
  let dragging = false, lastX = 0, lastY = 0, moved = 0;
  canvas.addEventListener('mousedown', (e) => { dragging = true; lastX = e.clientX; lastY = e.clientY; moved = 0; canvas.style.cursor = 'grabbing'; });
  window.addEventListener('mouseup', () => { dragging = false; canvas.style.cursor = 'grab'; });
  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY; moved += Math.abs(dx) + Math.abs(dy);
    yaw += dx * 0.006; pitch = Math.max(-1.2, Math.min(1.2, pitch + dy * 0.006));
  });
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    targetZoom = Math.max(0.5, Math.min(3.5, targetZoom * (e.deltaY < 0 ? 1.12 : 0.89)));
  }, { passive: false });

  // click → if on a galaxy, fly in (and sync board selector); else reset
  canvas.addEventListener('click', (e) => {
    if (moved > 6) return;     // was a drag, not a click
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let hit = null, hitD = 26;
    for (const k of GALAXY_ORDER) {
      const gs = galaxyState[k];
      if (!gs._screen) continue;
      const dd = Math.hypot(mx - gs._screen.sx, my - gs._screen.sy);
      if (dd < (gs._screenR || 40) && dd < hitD * 4) { hit = k; }
    }
    if (hit) {
      applyFocus(hit, true);
      // Sync the board scope selector (re-issues focusExchange → idempotent).
      if (window.PolarisBoardExchange && window.PolarisBoardExchange.setActiveExchange) {
        window.PolarisBoardExchange.setActiveExchange(hit);
      }
    } else {
      applyFocus(null, true);
      if (window.PolarisBoardExchange && window.PolarisBoardExchange.setActiveExchange) {
        window.PolarisBoardExchange.setActiveExchange('all');
      }
    }
  });

  function applyFocus(key, fromUser) {
    _focus = key;
    if (key && galaxyState[key]) {
      const gs = galaxyState[key];
      pan.tx = gs.cx; pan.ty = gs.cy; pan.tz = gs.cz;
      targetZoom = 2.1;
    } else {
      pan.tx = 0; pan.ty = 0; pan.tz = 0;
      targetZoom = 1.0;
    }
  }

  // Public API — board_exchange.js calls focusExchange('okx'|'capital'|'alpaca'|'all').
  window.PolarisGlobe = window.PolarisGlobe || {};
  window.PolarisGlobe.focusExchange = (which) => { applyFocus(focusKey(which), false); };
  window.PolarisGlobe.getFocusExchange = () => _focus;
  window.PolarisGlobe.resetView = () => { applyFocus(null, false); yaw = 0.4; pitch = 0.32; };

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' || e.key === 'r' || e.key === 'R') window.PolarisGlobe.resetView();
    if (e.key === ' ') { autoSpin = !autoSpin; e.preventDefault(); }
  });

  // Legacy no-op shims so any stale caller of the old engine API doesn't throw.
  // (The board only uses focusExchange; these guard console/SSE leftovers.)
  window.PolarisCloud = window.PolarisCloud || {};
  const _noop = () => {};
  ['highlightChain', 'clearChain', 'openDetail', 'closeDetail', 'metricRipple',
   'metricRippleByLabel', 'smallRippleByClusterTicker', 'spawnSatelliteSignal',
   'spawnProviderToTickerBeam', 'spawnOutboundArc', 'spawnSupernova',
   'chainSparkCascade', 'togglePerfMode'].forEach((m) => {
    if (!window.PolarisCloud[m]) window.PolarisCloud[m] = _noop;
  });

  buildStars();
  requestAnimationFrame(frame);
})();
