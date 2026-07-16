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

  // Jin 2026-07-16 P4b: this engine is now MOBILE-ONLY (desktop /flow dropped
  // the sphere for the spine canvas as its own page). Note: flow.html still
  // embeds /m in a small iframe (.mobile-pane), so this file also renders
  // there — but at iframe size (~400x250, phone-sized), so the mobile tuning
  // below applies cleanly to that pane too. The dpr cap tunes for a small
  // ~29vh phone pane rather than a full desktop viewport: 2x backing-buffer
  // pixels on a battery-constrained device for a canvas this small is wasted
  // GPU/battery, so cap at 1.5x.
  let dpr = Math.min(1.5, window.devicePixelRatio || 1);
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
  // Jin 2026-06-23 COLORFUL: drop the grayscale wash — each venue's OUTER cloud
  // wears its DASHBOARD venue color (polaris.css --exch-okx/cap/alp) so a venue
  // reads the same hue in cloud + board: OKX cyan #87d7ff · Capital PURPLE #b47cff ·
  // Alpaca yellow #d7d787. theme IS the venue color now (labels/halo/base dot).
  // Jin 2026-06-23: Capital is PURPLE #b47cff (NOT blue — the old #87afd7 was
  // indistinguishable from OKX cyan; polaris.css --exch-cap was bumped to match).
  const GALAXIES = {
    okx:     { key: 'okx',     label: 'OKX · SPOT CRYPTO',  theme: [0x87, 0xd7, 0xff], azimuth: -Math.PI * 0.66 },
    capital: { key: 'capital', label: 'CAPITAL · CFD L/S',  theme: [0xb4, 0x7c, 0xff], azimuth:  Math.PI * 0.5 },
    alpaca:  { key: 'alpaca',  label: 'ALPACA · US EQUITY', theme: [0xd7, 0xd7, 0x87], azimuth:  Math.PI * 0.06 },
  };
  const GALAXY_ORDER = ['okx', 'capital', 'alpaca'];
  const CONDUCTOR_THEME = [0xc4, 0xca, 0xd2];

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

  // ── Venue light (Jin 2026-06-23) ────────────────────────────────────────────
  // A WATCH / active node lights up in its EXCHANGE color so you can read which
  // venue is firing. Open positions ignore this and render as a pure P&L
  // green/red dot — see drawNode.
  // Jin 2026-06-23: venue colors are DISTINCT + match the dashboard so each venue
  // reads the same hue in cloud and board. Capital MUST be PURPLE #b47cff (NOT the
  // old blue #87afd7 — that was indistinguishable from OKX cyan):
  //   OKX → cyan #87d7ff · Capital → PURPLE #b47cff · Alpaca → yellow #d7d787
  const VENUE_LIGHT = {
    okx:     [0x87, 0xd7, 0xff],   // cyan   --exch-okx
    capital: [0xb4, 0x7c, 0xff],   // PURPLE --exch-cap
    alpaca:  [0xd7, 0xd7, 0x87],   // yellow --exch-alp
  };
  const NEUTRAL_GRAY = [0xc4, 0xca, 0xd2];
  function venueLight(gx) { return VENUE_LIGHT[gx] || NEUTRAL_GRAY; }
  // ── Asset-group base color (Jin 2026-07-16 P4b mobile-drift rewire) ────────
  // Same 6-hue palette as wall_spine_field.js's ASSET_GROUP_COLOR (duplicated
  // here rather than plumbed cross-module for 6 stable literal hex strings —
  // shared visual language, not runtime state; same precedent as this file's
  // own GATE_COLORS duplication in wall_console_readouts.js). mkt-cluster
  // universe nodes carry asset_group from the backend (polaris_graph.py); this
  // is their BASE color — venue color is reserved for actual activation (see
  // drawNode's `useVenue`), mirroring the desktop field's "base=asset class,
  // activation=venue" split.
  const ASSET_GROUP_COLOR_HEX = {
    crypto: '#979b50', forex: '#7b4f92', indices: '#696eb5',
    commodity: '#bf7dbf', stock: '#94bb81', etf: '#a96693',
  };
  function hexToRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const ASSET_GROUP_COLOR = {};
  Object.keys(ASSET_GROUP_COLOR_HEX).forEach((k) => { ASSET_GROUP_COLOR[k] = hexToRgb(ASSET_GROUP_COLOR_HEX[k]); });
  // Per-ticker hue/brightness jitter so individual symbols are distinguishable
  // inside one venue cloud instead of blobbing into a single flat mass — Jin
  // "각 노드 색 다 다르게". Deterministic from id (stable across refreshes), kept
  // small so the venue hue stays unmistakable. h ∈ [0,1] = the node's hash01.
  function venueShade(base, h) {
    const bri = 0.82 + h * 0.36;                 // ±18% brightness band
    const hueShift = (h - 0.5) * 46;             // ±23 channel skew → subtle hue spread
    const clamp = (v) => v < 24 ? 24 : (v > 255 ? 255 : v);
    return [
      clamp(Math.round(base[0] * bri + hueShift * 0.5)),
      clamp(Math.round(base[1] * bri)),
      clamp(Math.round(base[2] * bri - hueShift * 0.5)),
    ];
  }
  // Jin 2026-06-23 SIMPLIFY: dropped the per-position regime hue-tint (the broken
  // one-regime cloud cast), the exit-FSM marker ring, and the strategy glyph. A
  // watch/active node is just its plain venue light; an open position is just a
  // pure P&L green/red dot. (REGIME_CAST/activeColor/exitMarker/stratGlyph removed.)

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
  // Jin 2026-06-23: the Polaris North Star was removed ("반짝이는 별은 없애줘").
  // last_decision_ts is still tracked (decisionTs) to brighten the neural circuit
  // on a fresh AI decision, but nothing draws a star any more.
  let decisionTs = 0, decisionFlash = 0;

  // ── TAIL HAZE (Jin 2026-06-24 tier-LOD) ─────────────────────────────────────
  // The backend tier-LOD now emits only the hottest ~cap rows as individual mkt
  // nodes; the long tail (tier B overflow + the dormant T library) arrives as a
  // per-venue AGGREGATE descriptor {exchange,count,active,density}. We render it
  // as a cheap, COUNT-INDEPENDENT haze: a fixed small budget of procedural motes
  // scattered over each venue's outer-shell sector, so the cloud reads FULL (the
  // library is "there", as soft fog) while per-frame cost stays O(venue) — the
  // mote budget never grows with the watched universe size. Display-only.
  let tailAgg = [];                 // [{gx, count, active, density}]
  // Jin 2026-07-16 P4b mobile-only frame-cost trim: 64/venue (192 total) was
  // sized for a full desktop viewport; the ~29vh phone pane doesn't resolve
  // that many motes anyway — halved so the fixed per-frame haze cost drops
  // without visibly thinning the fog at phone size.
  const HAZE_PER_VENUE = 32;        // fixed mote budget per venue (count-independent)

  // ── SCATTERED SPARSE SPHERE — inside stays visible (Jin 2026-06-23) ──────────
  // Jin '나선형 불가사리 같다 … 전에 빌드처럼 구 덮으면서 크게 좀 스캐터 되어있게 전체적
  // 으로 구형 이루면서'. The spiral-arm galaxies read as a STARFISH (불가사리) and the
  // solid tiled shell HID the inside. This is the proven EARLIER even-sphere build:
  // the OUTER venue nodes scatter LOOSELY over one LARGE sphere surface (fibonacci
  // even-spread DIRECTION + per-node angular AND radial jitter → organic, airy, NOT
  // a regular pattern, NOT spiral arms). Big radius + generous spacing → sparse, you
  // SEE THROUGH the gaps to the inside. The silhouette reads as ONE SPHERE.
  //   CORE   r≈0.15 → executed-trade / open-position nodes (pure P&L green/red).
  //   MIDDLE r≈0.55 → gate/layer SATELLITES (globe-satellites.js families).
  //   OUTER  r≈1.5  → 3 venue clouds scattered over the LARGE outer sphere, each
  //                   in its own loose sector (cyan/purple/yellow), see-through.
  const R_CORE = 0.15;
  const R_MIDDLE = 0.55;
  const R_OUTER = 1.5;          // LARGE outer sphere → spread out, airy, see-through
  window.PolarisGlobe_shellR = { core: R_CORE, middle: R_MIDDLE, outer: R_OUTER };

  // ── ONION GATE SHELLS (Jin 2026-06-24) ──────────────────────────────────────
  // The 8-gate pipeline rendered as concentric onion rings: G1 (Universe scanner)
  // is the OUTERMOST shell, G8 (Reflector) the innermost gate shell, and the very
  // CORE = open positions (a candidate that cleared all 8 gates). A node sits on
  // the shell of the gate stage it has currently reached; as it's PROMOTED (passes
  // a gate) it pulses INWARD to the next shell — the pipeline read as an onion.
  //
  // The graph has no per-node gate_stage field, so we derive the stage a node has
  // reached from what IS emitted (cluster/state/active/signal_count): dormant mkt
  // universe = G1 candidates (outer rim), an active/firing watch node = deep in
  // the gates, a position = the core. The 8 shells fan between R_OUTER (G1) and
  // just outside R_CORE (G8) so the silhouette stays one see-through sphere.
  const GATE_N = 8;
  const R_GATE_OUT = R_OUTER;          // G1 sits on the existing outer sphere
  const R_GATE_IN = R_CORE * 1.9;      // G8 sits just outside the core heart
  // shellR(stage): stage 1..8 → radius (1 = outermost). stage 0 reserved for core.
  function gateShellR(stage) {
    if (stage <= 0) return R_CORE;
    const t = (stage - 1) / (GATE_N - 1);          // 0 at G1 … 1 at G8
    return R_GATE_OUT + (R_GATE_IN - R_GATE_OUT) * t;
  }
  window.PolarisGlobe_gateShellR = gateShellR;
  // Derive the gate stage (1=G1 outer … 8=G8 inner; 0=core/position) a node has
  // reached from its live role/state. Display-only heuristic over emitted fields:
  //   pos                         → 0  (cleared the pipeline → core position)
  //   watch + firing              → 7  (validated signal under the watcher/exit eye)
  //   watch                       → 5  (in validation/sizing band)
  //   mkt + firing/active         → 3  (a real signal caught → entered the gates)
  //   mkt dormant universe        → 1  (G1 scanner candidate, the outer rim)
  function gateStageFor(role, state, active, signalCount) {
    if (role === 'pos') return 0;
    if (role === 'watch') return (state === 'firing') ? 7 : 5;
    if (signalCount > 0 || state === 'firing') return 3;
    if (active) return 3;
    return 1;
  }
  window.PolarisGlobe_gateStageFor = gateStageFor;

  // Even-sphere (fibonacci-sphere) direction for the i-th of n points: returns a
  // unit vector spread evenly over the whole sphere. Used for CORE + OUTER shells.
  const GOLDEN = Math.PI * (3 - Math.sqrt(5));
  function fibDir(i, n) {
    const y = 1 - (i + 0.5) / n * 2;          // y ∈ (1,-1), evenly stepped
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = GOLDEN * i;
    return { x: Math.cos(th) * r, y, z: Math.sin(th) * r };
  }

  // Each venue owns a loose SECTOR of the outer sphere — a pole direction the
  // venue's nodes group around (so cyan/purple/yellow read as 3 regions) while the
  // 3 sectors together still tile the WHOLE sphere (one-sphere silhouette). The
  // sector poles sit ~120° apart on a slightly tilted ring; cx/cy/cz = the pole on
  // the outer shell (= label anchor + the OUTER end of the neural links in flows).
  const VENUE_TILT = 0.30;      // small latitude lift off the equator for poles
  GALAXY_ORDER.forEach((k, i) => {
    const g = GALAXIES[k];
    const az = i * (2 * Math.PI / GALAXY_ORDER.length);
    const py = Math.sin(VENUE_TILT * ((i % 2) ? -1 : 1));   // alternate hemisphere lift
    const pr = Math.sqrt(Math.max(0, 1 - py * py));
    const pole = { x: Math.cos(az) * pr, y: py, z: Math.sin(az) * pr };  // unit
    galaxyState[k] = {
      key: k, theme: g.theme, label: g.label,
      pole,
      // centre = the venue's pole on the LARGE outer sphere (label + link anchor).
      cx: pole.x * R_OUTER, cy: pole.y * R_OUTER, cz: pole.z * R_OUTER,
      count: 0, pnl: 0, pulse: 0, hot: 0,
    };
  });

  // Scatter one venue node over the LARGE outer sphere, loosely grouped around the
  // venue's pole. h01 → even fibonacci direction over a spherical CAP around the
  // pole (the venue's share of the surface); a01 → extra angular jitter so it's
  // organically scattered, not a tight regular lattice; b01 → small radial jitter
  // so the shell has airy thickness (not a hard wall). Returns absolute position +
  // unit dir. Purely id-derived (order-independent) → stable across refreshes.
  const CAP = 1.32;             // half-angle of a venue's cap (rad) — wide = loose
  const SCAT_LATTICE = 512;     // fibonacci lattice size the cap samples from
  function scatterPos(gxKey, h01, a01, b01, shellR) {
    const gs = galaxyState[gxKey];
    const pole = gs.pole;
    // even fibonacci point on a cap around +Z, then rotate the cap onto the pole.
    // cos(theta) ranges over the cap; phi gets a golden-angle base + jitter.
    const ct = 1 - h01 * (1 - Math.cos(CAP));            // even over cap area
    const st = Math.sqrt(Math.max(0, 1 - ct * ct));
    const phi = GOLDEN * Math.floor(h01 * SCAT_LATTICE) + (a01 - 0.5) * 1.4; // jittered
    const lx = Math.cos(phi) * st, ly = Math.sin(phi) * st, lz = ct;        // cap-local (+Z)
    // rotate local +Z frame onto `pole` (Rodrigues toward pole from +Z).
    const d = rotZto(pole, lx, ly, lz);
    // shellR = the node's ONION GATE shell radius (G1 outer → G8 inner). Default
    // to R_OUTER (G1 rim) when unspecified. Small radial jitter → airy thickness.
    const base = (shellR || R_OUTER) * (0.94 + b01 * 0.12);
    return { x: d.x * base, y: d.y * base, z: d.z * base, dir: d };
  }
  // Rotate a vector given in a +Z-up local frame so its +Z aligns with unit `pole`.
  function rotZto(pole, lx, ly, lz) {
    // axis = (+Z) × pole, angle = acos(pole.z). Rodrigues rotation of (lx,ly,lz).
    const ax0 = -pole.y, ay0 = pole.x, az0 = 0;          // (0,0,1)×pole = (-py,px,0)
    const s = Math.hypot(ax0, ay0, az0);
    if (s < 1e-6) {                                      // pole ≈ +Z (or −Z)
      const sign = pole.z >= 0 ? 1 : -1;
      return { x: lx, y: ly * sign, z: lz * sign };
    }
    const ux = ax0 / s, uy = ay0 / s, uz = az0 / s;      // unit axis
    const c = pole.z, sn = s;                            // cos = pole.z, sin = |axis|
    const dot = ux * lx + uy * ly + uz * lz;
    // v*cos + (axis×v)*sin + axis*(axis·v)*(1−cos)
    const cx = uy * lz - uz * ly, cy = uz * lx - ux * lz, cz = ux * ly - uy * lx;
    return {
      x: lx * c + cx * sn + ux * dot * (1 - c),
      y: ly * c + cy * sn + uy * dot * (1 - c),
      z: lz * c + cz * sn + uz * dot * (1 - c),
    };
  }
  window.PolarisGlobe_fibDir = fibDir;
  window.PolarisGlobe_scatterPos = scatterPos;

  // ── OUTER probe shell orbital motion (Jin '프로브는 위성처럼 다른 궤도 가지고 돌게')
  // The outer probe/ticker nodes orbit on their OWN distinct paths (like the 8 gate
  // orbits) rather than sitting as static dim dust. Each outer node rotates its base
  // scatter direction around a per-node tilted axis (id-derived inclination/speed/
  // direction → all distinct), keeping its radius → it sweeps a small orbit on the
  // LARGE outer shell. The shell as a whole reads as a slowly rotating layer of
  // orbiting satellites; still dim when dormant, glowing on real signal. Display-only.
  // Rotate unit vector v around unit axis u by angle ang (Rodrigues).
  function rotAxis(ux, uy, uz, vx, vy, vz, ang) {
    const c = Math.cos(ang), s = Math.sin(ang), dot = ux * vx + uy * vy + uz * vz;
    const cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
    return {
      x: vx * c + cx * s + ux * dot * (1 - c),
      y: vy * c + cy * s + uy * dot * (1 - c),
      z: vz * c + cz * s + uz * dot * (1 - c),
    };
  }
  // CORE shell slow uniform spin about Y → the innermost rotating layer.
  const CORE_SPIN = 0.05;          // rad/s — gentle
  function outerTick(now, dt) {
    const t = now / 1000;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.sat) continue;
      // CORE (positions): gentle uniform spin about Y around the origin.
      const cb = n._cBase;
      if (cb) {
        const ca = Math.cos(t * CORE_SPIN), sa = Math.sin(t * CORE_SPIN);
        const dx = cb.dx * ca + cb.dz * sa, dz = -cb.dx * sa + cb.dz * ca;
        n.hx = dx * cb.r; n.hy = cb.dy * cb.r; n.hz = dz * cb.r;
        n.dir = { x: dx, y: cb.dy, z: dz };
        continue;
      }
      const ob = n._oBase;
      if (!ob) continue;
      // orbital axis = base dir tilted by _oInc about a fixed lateral axis, so each
      // node's orbit plane is distinct (different inclinations across the shell).
      const ci = Math.cos(n._oInc), si = Math.sin(n._oInc);
      // tilt the base direction itself to define the spin axis (axis ≠ position →
      // the point actually sweeps an orbit rather than spinning in place).
      let ax = ob.dx, ay = ob.dy * ci - ob.dz * si, az = ob.dy * si + ob.dz * ci;
      const am = Math.hypot(ax, ay, az) || 1; ax /= am; ay /= am; az /= am;
      const ang = t * n._oSpeed;
      const rr = Math.hypot(ob.x, ob.y, ob.z);          // keep on the outer shell radius
      const d = rotAxis(ax, ay, az, ob.dx, ob.dy, ob.dz, ang);
      n.hx = d.x * rr; n.hy = d.y * rr; n.hz = d.z * rr;
      n.dir = d;                                          // refresh cached dir for links/fade
    }
  }
  window.PolarisGlobe_outerTick = outerTick;
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
    // Fresh AI decision (last_decision_ts advances) → brighten the neural circuit.
    const lt = +d.last_decision_ts || 0;
    if (lt && lt !== decisionTs) {
      if (decisionTs) decisionFlash = 1;        // skip the first load
      decisionTs = lt;
    }
    nodeByIndex.length = 0;
    // tier-LOD tail aggregate → per-venue haze descriptors (count-independent).
    tailAgg = [];
    const ta = d.tail_aggregate || [];
    for (let i = 0; i < ta.length; i++) {
      const t = ta[i];
      const gx = venueKey(t.exchange);
      if (!gx) continue;
      tailAgg.push({ gx, count: +t.count || 0, active: +t.active || 0, density: +t.density || 0.2 });
    }
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
      // Stable home position from id-hashes ONLY (order-independent):
      //   pos   → CORE shell (r≈0.15), even over the WHOLE small inner sphere.
      //   watch → scattered LOOSELY over the LARGE outer sphere, in its venue sector.
      const h1 = hash01(id + '~a');
      const h2 = hash01(id + '~b');
      const h3 = hash01(id + '~c');
      let dir;
      if (role === 'pos') {
        // CORE: even over the full small sphere. Map the id-hash onto a fibonacci
        // index drawn from a fixed lattice so points stay spread (not clumped),
        // while remaining purely id-derived (order-independent).
        const LAT = 233;                                 // fixed fibonacci lattice size
        dir = fibDir(Math.floor(h1 * LAT), LAT);
        // tiny per-node radial jitter so the heart has a little thickness/depth.
        const rr = R_CORE * (0.86 + h2 * 0.28);
        // Cache base dir/radius so coreTick can gently rotate the CORE shell as one
        // (the innermost of the 3 concentric rotating layers) — slow uniform spin so
        // the trade hearts read as a gently turning core, not a frozen clump.
        n._cBase = { dx: dir.x, dy: dir.y, dz: dir.z, r: rr };
        n.hx = dir.x * rr; n.hy = dir.y * rr; n.hz = dir.z * rr;
      } else {
        // OUTER: scattered LOOSELY over the LARGE outer sphere surface (Jin "전에 빌드
        // 처럼 구 덮으면서 크게 스캐터 … 전체적으로 구형"). scatterPos = fibonacci even-
        // spread direction over the venue's spherical cap + angular AND radial jitter →
        // organic, airy, see-through. NOT spiral arms (no starfish). Purely id-derived
        // (order-independent): h1=cap position, h2=angular jitter, h3=radial jitter.
        // ONION GATE SHELL (Jin 2026-06-24): the node's CURRENT pipeline stage
        // (G1 outer … G8 inner) picks which concentric shell it scatters onto. A
        // freshly-PROMOTED node (stage moved inward since the last refresh) fires an
        // inward pulse so you SEE it advance through the gates. Stage is derived from
        // the just-assigned role/state/active/signalCount below (recomputed here from
        // bn so it reflects this refresh's data).
        const stage = gateStageFor(role, (bn.signal_count_30m > 0 ? 'firing' : (bn.state || 'lit')),
          (bn.active === true) || (role !== 'mkt'), bn.signal_count_30m || 0);
        const sp = scatterPos(gx, h1, h2, h3, gateShellR(stage));
        // Promotion detection: stage decreased (moved inward) vs the node's last
        // stage → inward pulse. New nodes (no prior stage) just appear on-shell.
        if (n._gateStage != null && stage < n._gateStage) { n.pulse = Math.min(1, (n.pulse || 0) + 0.9); }
        n._gateStage = stage;
        // Jin '프로브는 위성처럼 다른 궤도 가지고 돌게' — the OUTER probe shell now ORBITS
        // as satellites (own distinct orbital path) instead of sitting static. Cache the
        // base scatter pos/dir + a per-node orbital signature (axis tilt + speed/dir,
        // id-derived so it's stable + distinct per node); outerTick() rotates the home
        // around that axis each frame. hx/hy/hz are seeded here so node 1 starts on-shell.
        n._oBase = { x: sp.x, y: sp.y, z: sp.z, dx: sp.dir.x, dy: sp.dir.y, dz: sp.dir.z };
        n._oInc = (h2 - 0.5) * 2.4;                      // orbital-plane tilt (rad) — distinct
        n._oSpeed = (0.03 + h3 * 0.05) * (h1 < 0.5 ? 1 : -1);  // distinct speed + direction
        n.hx = sp.x; n.hy = sp.y; n.hz = sp.z;
        dir = sp.dir;                                    // unit dir (neural links, depth-fade)
      }
      n.gx = gx; n.role = role; n.cluster = bn.cluster;
      n.label = bn.label || bn.ticker || id;
      n.ticker = bn.ticker;
      n.strategyId = bn.strategy_id || null;             // for ticker↔strat links
      n.pnl = bn.pnl_usd || 0;
      n.direction = bn.direction;
      n.exitState = bn.exit_state || null;               // open/touched/protected/trailing
      n.intensity = bn.intensity != null ? bn.intensity : 0.4;
      n.sizeMul = bn.size_mul != null ? bn.size_mul : 0.6;
      // Capital weekend-shell visibility floor (Jin 2026-06-28, display-only): the
      // backend stamps shell_floor + lifts intensity/size_mul on Capital dormant
      // nodes so the dim-cloud renders the venue shell instead of grey dust. The
      // node STAYS dormant (no glow) — drawDimCloud just reads these for shell nodes.
      n.shellFloor = bn.shell_floor === true;
      n.state = bn.state || 'lit';
      // ── REAL-signal glow (Jin 2026-06-23) ──────────────────────────────────
      // The backend now joins the live signals table → per-instrument 30m catch
      // count (signal_count_30m). A node whose pipeline actually caught a signal
      // glows BRIGHTLY the instant count>0; dormant tickers stay dim. This is the
      // true glow source — is_active/`active` is only a secondary un-dim cue. We
      // OR it client-side too so a real catch always reads as firing regardless of
      // the backend state string. Display-only.
      n.signalCount = bn.signal_count_30m || 0;
      if (n.signalCount > 0) n.state = 'firing';
      n.dir = dir;                                       // cached unit dir (links)
      // Color: open positions are a pure P&L green/red dot. Other OUTER exchange
      // nodes wear their DASHBOARD venue color, per-ticker shaded (venueShade) so
      // each symbol is individually distinguishable rather than one flat blob.
      // mkt-cluster universe nodes instead base on their ASSET_GROUP (crypto/
      // forex/indices/commodity/stock/etf) when the backend supplies one —
      // venue tint is reserved for actual activation (drawNode's `useVenue`).
      const shaded = venueShade(venueLight(gx), h1);
      n.assetGroup = bn.asset_group || null;
      const assetBase = (role === 'mkt' && n.assetGroup && ASSET_GROUP_COLOR[n.assetGroup])
        ? ASSET_GROUP_COLOR[n.assetGroup] : shaded;
      n.color = (role === 'pos') ? chainColor(n.pnl) : assetBase;
      n.venueColor = shaded;
      n.base = role === 'pos' ? 2.4 : 1.9;   // Jin grayscale: 체결(녹/적) 노드 좀만 더 축소 (3.4→2.4 ~30%)
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
    const scale = Math.min(W, H) * 0.30 * zoom;  // Jin: smaller cloud, full sphere visible (outer 1.5R fits in pane)
    const persp = 1 / (1 + rz * 0.25);
    return { sx: CX + rx * scale * persp, sy: CY + ry * scale * persp, depth: rz, persp };
  }

  // ── Backdrop ─────────────────────────────────────────────────────────────────
  // Jin 2026-06-23 "그냥 까맣게": the starfield / nebula / cosmic dust / gradient wash
  // were all removed. The backdrop is now a plain solid near-black fill — see
  // drawCosmos. driftX/driftY remain only as the gentle parallax driver for the
  // scene rotation feel (no longer drives any background layer).
  let last = performance.now();
  let driftX = 0, driftY = 0;

  // Jin 2026-06-23: backdrop is now a plain black fill — nothing to pre-build.
  function rebuildBackdrop() { /* solid black void — no starfield/nebula/dust caches */ }

  // Jin 2026-06-23 "그냥 까맣게": the starfield / nebula haze / cosmic dust / gradient
  // wash were all removed — Jin wants a PLAIN BLACK VOID behind the sphere, nothing
  // decorative. The backdrop is now a single solid near-black fill. (buildStars /
  // buildDust / buildNebula / buildBg + their caches are no longer invoked.)
  function drawCosmos(now, dt) {
    ctx.fillStyle = '#05070b';
    ctx.fillRect(0, 0, W, H);
  }

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    // board.js startSphereWatchdog() reloads the page if this heartbeat stalls
    // for 10s — keep it ticking every frame so a live globe is never killed.
    window.__sphereHB = (window.__sphereHB || 0) + 1;
    if (autoSpin && !dragging) yaw += dt * 0.08;
    // slow cinematic drift of the cosmos (parallax driver) — floats through space.
    driftX = Math.sin(now / 23000) * 1.0 + yaw * 0.18;
    driftY = Math.cos(now / 31000) * 0.6 + pitch * 0.12;
    // ease camera toward zoom / pan targets
    zoom += (targetZoom - zoom) * Math.min(1, dt * 5);
    pan.x += (pan.tx - pan.x) * Math.min(1, dt * 4);
    pan.y += (pan.ty - pan.y) * Math.min(1, dt * 4);
    pan.z += (pan.tz - pan.z) * Math.min(1, dt * 4);

    ctx.save();
    ctx.scale(dpr, dpr);
    // plain black backdrop — fills canvas (no starfield/nebula/dust).
    drawCosmos(now, dt);

    conductor.beat = 0.5 + 0.5 * Math.sin(now / 600);
    conductor.pulse = Math.max(0, conductor.pulse - dt * 1.8);
    decisionFlash = Math.max(0, decisionFlash - dt * 0.8);
    window.PolarisGlobe_decisionFlash = decisionFlash;   // shared → neural-line brighten

    // satellites revolve around the conductor: recompute their home each frame.
    if (window.PolarisGlobe_satTick) window.PolarisGlobe_satTick(now, dt);
    // OUTER probe shell orbits as satellites (distinct per-node orbital paths).
    outerTick(now, dt);

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

    // ONION GATE SHELLS (Jin 2026-06-24): faint concentric guide rings, G1 outer →
    // G8 inner → core, drawn BEHIND the nodes so the pipeline reads as an onion.
    drawGateShells(now);

    // galaxy halo + label — Jin 2026-06-23: removed the per-cluster white nebula
    // "fog ball" / bright galactic core. Keep only the venue labels (drawn cheaply
    // inside drawGalaxyHalos via the labelsOnly flag) so each cluster is still named.
    drawGalaxyHalos(now, /* labelsOnly */ true);

    // tier-LOD TAIL HAZE — the long-tail library as a soft per-venue fog, drawn
    // BEHIND the lit nodes + flows. Count-independent O(venue) cost (fixed mote
    // budget per venue) so the cloud reads full at any watched-universe size.
    drawTailHaze(now);

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
    // Jin 2026-06-23: the Polaris North Star was removed entirely ("반짝이는 별은
    // 없애줘") — drawPolaris + its call + the polaris state object are all gone.
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

  // ── Onion gate shells (Jin 2026-06-24) ──────────────────────────────────────
  // Faint concentric circles around the scene origin, one per gate G1(outer)→
  // G8(inner) plus the core, so the 8-gate pipeline reads as an onion the nodes
  // ride. Centred on the projected origin; radius = the shell's world radius times
  // the same screen scale project() uses (so it tracks zoom + the perspective at
  // the centre). Display-only guide — no node data touched.
  function drawGateShells(now) {
    const o = project(0, 0, 0);
    const scale = Math.min(W, H) * 0.30 * zoom * o.persp;
    ctx.save();
    ctx.lineWidth = 0.6;
    for (let s = 1; s <= GATE_N; s++) {
      const rr = gateShellR(s) * scale;
      if (rr < 2) continue;
      // outer gates fainter, inner gates a touch brighter (closer to a position).
      const a = 0.05 + (s / GATE_N) * 0.06;
      ctx.strokeStyle = rgba([0x8a, 0x94, 0xb0], a);
      ctx.beginPath(); ctx.arc(o.sx, o.sy, rr, 0, 6.2832); ctx.stroke();
      // gate label on the right edge of the ring (tiny, only when ring is legible).
      if (rr > 16) {
        ctx.fillStyle = rgba([0xa0, 0xaa, 0xc4], 0.34 + (s / GATE_N) * 0.18);
        ctx.font = '700 7px JetBrains Mono, monospace';
        ctx.textAlign = 'left';
        ctx.fillText('G' + s, o.sx + rr + 2, o.sy + 2);
      }
    }
    // core ring = open positions (cleared all 8 gates).
    const cr = R_CORE * scale;
    if (cr > 2) {
      ctx.strokeStyle = rgba([0xc4, 0xca, 0xd6], 0.14);
      ctx.beginPath(); ctx.arc(o.sx, o.sy, cr, 0, 6.2832); ctx.stroke();
    }
    ctx.restore();
  }

  function drawGalaxyHalos(now, labelsOnly) {
    for (const k of GALAXY_ORDER) {
      const gs = galaxyState[k];
      gs.pulse = Math.max(0, gs.pulse - 0.016);
      gs.hot = Math.max(0, gs.hot - 0.01);
      const p = project(gs.cx, gs.cy, gs.cz);
      const d = dimFor(k);
      const rad = Math.min(W, H) * 0.20 * zoom * p.persp;
      // Jin 2026-06-23: removed the per-cluster white nebula "fog ball" + bright
      // galactic core. Kept behind the labelsOnly flag (currently always true) so
      // each cluster is just its stars + label, no foggy glow ball.
      if (!labelsOnly) {
        const breathe = 0.5 + 0.5 * Math.sin(now / 2600 + gs.cx * 3);
        const glow = (0.07 + 0.03 * breathe + gs.pulse * 0.5 + gs.hot * 0.3) * d;
        ctx.globalCompositeOperation = 'lighter';
        const grad = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, rad);
        grad.addColorStop(0, rgba(gs.theme, glow * 1.6));
        grad.addColorStop(0.4, rgba(gs.theme, glow * 0.6));
        grad.addColorStop(1, rgba(gs.theme, 0));
        ctx.fillStyle = grad;
        ctx.beginPath(); ctx.arc(p.sx, p.sy, rad, 0, 6.2832); ctx.fill();
        const cr = rad * 0.30;
        const cg = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, cr);
        cg.addColorStop(0, rgba(gs.theme, (0.18 + gs.pulse * 0.4) * d));
        cg.addColorStop(1, rgba(gs.theme, 0));
        ctx.fillStyle = cg;
        ctx.beginPath(); ctx.arc(p.sx, p.sy, cr, 0, 6.2832); ctx.fill();
        ctx.globalCompositeOperation = 'source-over';
      }
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
      // True active count = emitted-node active + the tail-haze active rows the
      // per-venue node cap folded away (Alpaca's ~1500-row universe far exceeds
      // the render cap, so most of its active focus lives only in tail_aggregate;
      // without adding it back the label under-reports active by ~6x).
      let tailActive = 0;
      for (let ti = 0; ti < tailAgg.length; ti++) {
        if (tailAgg[ti].gx === k) tailActive += tailAgg[ti].active;
      }
      const trueActive = gs.activeCount + tailActive;
      const shownLabel = trueActive > gs.mktCount
        ? `${trueActive} active (${gs.mktCount} shown)`
        : `${gs.mktCount} tickers (${gs.activeCount} active)`;
      ctx.fillText(`${shownLabel} · ${pnlStr}`, p.sx, p.sy + rad + 12);
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
  // Jin 2026-07-16 P4b mobile-only frame-cost trim: 650 was sized for a full
  // desktop viewport; a ~29vh phone pane can't resolve that density, so the
  // budget is cut for lower per-frame fillRect cost (node LOD).
  const DIM_BUDGET = 320;            // target max dim dots actually drawn / frame
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
        const n = d.n;
        // depth shading: nearer = slightly brighter/bigger (front hemisphere pop).
        const depthA = 0.5 + 0.5 * Math.max(-1, Math.min(1, -n.z));
        let a = (0.22 + 0.22 * depthA) * dd;   // Jin: dust 같지 않게 살짝 밝게
        let s = Math.max(0.85, 1.3 * zoom * p.persp);
        // Capital weekend-shell floor (Jin 2026-06-28, display-only): a shell_floor
        // dormant node (Capital market-closed) reads its backend-floored
        // intensity/size so the venue shell stays visible — brighter dot + a touch
        // larger — instead of collapsing to grey dust. Still dormant (no glow); only
        // OKX/Alpaca dim dots keep the original depth-only weight (untouched).
        if (n.shellFloor) {
          a = Math.min(0.6, a + (n.intensity || 0) * 0.35);
          s = Math.max(s, (n.sizeMul || 0) * 1.6 * zoom * p.persp);
        }
        ctx.fillStyle = `rgba(${r},${g},${bl},${a})`;
        ctx.fillRect(p.sx - s * 0.5, p.sy - s * 0.5, s, s);
      }
    }
  }

  // ── Tail haze (Jin 2026-06-24 tier-LOD) ─────────────────────────────────────
  // Renders each venue's long-tail library as a soft fog of procedural motes
  // over the venue's outer-shell sector. COUNT-INDEPENDENT: the per-venue mote
  // budget is fixed (HAZE_PER_VENUE), only its DENSITY/brightness scales with the
  // aggregate count — so a 1900-row universe costs exactly the same per frame as
  // a 300-row one. Motes are static id-free scatter (deterministic angle from the
  // loop index) so the fog is stable, drifting only with the scene rotation. No
  // gradients, no z-sort — flat fillRect, like drawDimCloud. Display-only.
  function drawTailHaze(now) {
    if (!tailAgg.length) return;
    const t = now / 1000;
    for (let v = 0; v < tailAgg.length; v++) {
      const agg = tailAgg[v];
      const dd = dimFor(agg.gx);
      if (dd <= 0.001) continue;
      const theme = (galaxyState[agg.gx] && galaxyState[agg.gx].theme) || NEUTRAL_GRAY;
      // muted toward grey so the haze reads as background fog, not lit nodes.
      const r = (theme[0] + 150) >> 1, g = (theme[1] + 160) >> 1, bl = (theme[2] + 175) >> 1;
      // how full the fog reads: more tail rows → denser/brighter (log-damped so a
      // huge universe doesn't blow out). density = backend representative weight.
      const fill = Math.min(1, Math.log1p(agg.count) / 7) * (0.5 + 0.5 * agg.density);
      // fixed mote budget; only alpha scales with fill (count-independent cost).
      const motes = HAZE_PER_VENUE;
      ctx.fillStyle = `rgba(${r},${g},${bl},${(0.05 + 0.16 * fill) * dd})`;
      for (let i = 0; i < motes; i++) {
        const h01 = (i + 0.5) / motes;
        const a01 = ((i * 0.6180339887) % 1);          // golden-ratio angular spread
        const b01 = (((i * 7) % 13) / 13);             // pseudo radial jitter
        const sp = scatterPos(agg.gx, h01, a01, b01, R_OUTER * 1.04);
        // gentle drift with the scene (rotate the scatter dir slowly about Y).
        const ca = Math.cos(t * 0.02), sa = Math.sin(t * 0.02);
        const dx = sp.dir.x * ca + sp.dir.z * sa, dz = -sp.dir.x * sa + sp.dir.z * ca;
        const rr = R_OUTER * 1.04 * (0.9 + b01 * 0.16);
        const p = project(dx * rr, sp.dir.y * rr, dz * rr);
        if (p.persp <= 0) continue;
        if (p.sx < -8 || p.sx > W + 8 || p.sy < -8 || p.sy > H + 8) continue;
        const s = Math.max(1.0, 2.0 * zoom * p.persp);
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
    // Jin 2026-06-23 SIMPLIFY: an open POSITION is a pure P&L green/red dot — no
    // venue color, no venue halo. A WATCH / active non-position node lights up in
    // its plain EXCHANGE color so you can see which venue is firing; the dormant
    // universe stays neutral gray.
    const isPos = n.role === 'pos';
    const isActiveLit = !n.sat && (isPos || n.active || n.state === 'lit' || firing);
    const venueC = (n.venueColor || venueLight(n.gx));
    // Jin 2026-06-23 COLORFUL: even a non-active outer node keeps its per-ticker
    // venue shade (n.color) instead of falling back to neutral gray — the cloud
    // reads colorful. Satellites keep their family color (n.color).
    // Jin 2026-07-16 P4b: a mkt-cluster node's `n.color` is its ASSET_GROUP base
    // (see setGraph) — it only swaps to the venue tint on actual FIRING (a real
    // signal catch), not merely the `active` focus flag, so the asset-group base
    // is visible at rest instead of always being overridden to venue color.
    // Non-mkt roles (watch/satellites) keep the prior isActiveLit gate.
    const useVenue = n.role === 'mkt' ? firing : isActiveLit;
    const c = isPos
      ? chainColor(n.pnl)
      : (useVenue ? venueC : n.color);
    // halo/glow color: positions glow in their own P&L color; active watch nodes
    // glow in their venue color.
    const haloC = isPos ? c : (useVenue ? venueC : c);
    // ── signal lightup: a firing node = an ACTIVE signal → breathing halo so the
    //    holding ticker pops out of the cloud (the original neural-signal concept).
    if (firing) {
      const breathe = 0.5 + 0.5 * Math.sin(now / 360 + (n.phase || 0) * 6.283);
      const gr = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, r * 5);
      gr.addColorStop(0, rgba(haloC, (0.30 + breathe * 0.28) * d));
      gr.addColorStop(0.55, rgba(haloC, (0.10 + breathe * 0.10) * d));
      gr.addColorStop(1, rgba(haloC, 0));
      ctx.fillStyle = gr;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, r * 5, 0, 6.2832); ctx.fill();
    }
    if (n.flash > 0.3 || n.pulse > 0.3) {
      const g = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, r * 4);
      g.addColorStop(0, rgba(haloC, Math.min(0.6, (n.flash + n.pulse) * 0.5 * d)));
      g.addColorStop(1, rgba(haloC, 0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, r * 4, 0, 6.2832); ctx.fill();
    }
    // tasteful additive bloom: lit/active galaxy nodes glow like stars inside the
    // nebula in their VENUE color. Capped so it never blows out. Firing already
    // has its breathing halo. Pos nodes also bloom so the venue halo wraps the
    // green/red P&L dot (you read venue + P&L together).
    if (!firing && isActiveLit && !n.sat) {
      const bloom = Math.min(0.34, (0.12 + n.intensity * 0.18) * d);
      ctx.globalCompositeOperation = 'lighter';
      const bg = ctx.createRadialGradient(p.sx, p.sy, 0, p.sx, p.sy, r * 3.0);
      bg.addColorStop(0, rgba(haloC, bloom));
      bg.addColorStop(1, rgba(haloC, 0));
      ctx.fillStyle = bg;
      ctx.beginPath(); ctx.arc(p.sx, p.sy, r * 3.0, 0, 6.2832); ctx.fill();
      ctx.globalCompositeOperation = 'source-over';
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

    // EXIT-FSM marker (Jin 2026-06-23 structure): a position actively in the exit
    // FSM (touched/protected/trailing — emitted by the backend, previously ignored)
    // wears a thin static ring so the exit lifecycle is visible WITHOUT moving the
    // dot (no jitter). 'open' = no ring. Position is unchanged — ring only.
    if (isPos && n.exitState && n.exitState !== 'open') {
      const ringA = 0.55 * d;
      ctx.strokeStyle = rgba(c, ringA);
      ctx.lineWidth = Math.max(0.6, r * 0.22);
      ctx.beginPath(); ctx.arc(p.sx, p.sy, Math.max(1.2, r * 2.0), 0, 6.2832); ctx.stroke();
      // trailing = a second dashed-feel outer tick so it reads distinct from touched.
      if (n.exitState === 'trailing') {
        ctx.strokeStyle = rgba(c, ringA * 0.55);
        ctx.lineWidth = Math.max(0.5, r * 0.14);
        ctx.beginPath(); ctx.arc(p.sx, p.sy, Math.max(1.6, r * 2.7), 0, 6.2832); ctx.stroke();
      }
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

  // ── Touch (Jin 2026-07-16 P4b — this engine is mobile-only now): 1-finger
  // horizontal-dominant drag orbits; a vertical swipe is left untouched so the
  // page keeps scrolling (canvas has touch-action:pan-y in mobile.html — that
  // axis is reserved for native scroll). 2-finger pinch zooms. A plain tap
  // (no meaningful `moved`) still reaches the 'click' listener below via the
  // browser's synthetic click → galaxy fly-in / reset works unchanged.
  let touchLastX = 0, touchLastY = 0, touchOrbiting = false, pinchDist0 = 0, pinchZoom0 = 1;
  function touchDist(t0, t1) { return Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY); }
  canvas.addEventListener('touchstart', (e) => {
    if (e.touches.length === 1) {
      touchLastX = e.touches[0].clientX; touchLastY = e.touches[0].clientY;
      touchOrbiting = false; moved = 0;
    } else if (e.touches.length === 2) {
      pinchDist0 = touchDist(e.touches[0], e.touches[1]);
      pinchZoom0 = targetZoom;
    }
  }, { passive: true });
  canvas.addEventListener('touchmove', (e) => {
    if (e.touches.length === 2) {
      const d = touchDist(e.touches[0], e.touches[1]);
      if (pinchDist0 > 0) targetZoom = Math.max(0.5, Math.min(3.5, pinchZoom0 * (d / pinchDist0)));
      e.preventDefault();
      return;
    }
    if (e.touches.length !== 1) return;
    const tx = e.touches[0].clientX, ty = e.touches[0].clientY;
    const dx = tx - touchLastX, dy = ty - touchLastY;
    // Small deadzone before committing to an orbit-drag: only claim the
    // gesture once horizontal movement clearly dominates vertical.
    if (!touchOrbiting && Math.abs(dx) > Math.abs(dy) * 1.3 && Math.abs(dx) > 6) touchOrbiting = true;
    if (!touchOrbiting) return;
    touchLastX = tx; touchLastY = ty; moved += Math.abs(dx) + Math.abs(dy);
    yaw += dx * 0.006; pitch = Math.max(-1.2, Math.min(1.2, pitch + dy * 0.006));
    e.preventDefault();
  }, { passive: false });
  canvas.addEventListener('touchend', () => { touchOrbiting = false; pinchDist0 = 0; });

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
  // Jin 2026-07-16 P4b: mobile.html has called this since 2026-06-something
  // to pull the constellation into a compact cloud for the short ~29vh phone
  // pane, but the function was never actually defined here — the call was a
  // guarded no-op (`if (...&&...setBaseZoom)`), so the globe always rendered
  // at the default zoom=1.0 despite the "small compact cloud" comment/intent.
  // Sets BOTH zoom and targetZoom so the compact framing is immediate on load
  // (no animated zoom-out-then-in from the default).
  window.PolarisGlobe.setBaseZoom = (z) => { zoom = z; targetZoom = z; };

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

  // Jin 2026-06-23: pollEquity removed — it only fed the Polaris North Star core
  // tint, and Polaris is gone. Net equity P&L still reads on the board + per-venue
  // cluster labels; the cloud's P&L color lives on the centre trade dots.

  rebuildBackdrop();
  requestAnimationFrame(frame);
})();
