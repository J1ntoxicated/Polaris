/* Polaris FLOW — "Synaptic Spine" field layer (Jin 2026-07-10, feat/jarvis-
 * wall). Owns the background constellation: node layout (screen[] + the 8
 * gate-spine anchor points), the ambient edge mesh (functional wiring +
 * whisper mesh + real lifecycle chains), the micro-pulse pool, and the
 * static-layer pre-render — everything wall_spine.js's canvas/rAF loop reads
 * every frame but does NOT rebuild every frame. Split out of wall_spine.js to
 * keep both files under the project's 500-LOC cap (same cloud.js/
 * cloud_nodes.js precedent this design replaces).
 *
 * Jarvis visual-language pass (Jin 2026-07-10, feat/jarvis-language): the
 * NEW purely-additive decoration systems (strategy score_F reticle, watch
 * bracket chip, engineering graticule) live in the sibling wall_spine_deco.js
 * instead of growing this already-oversized file further — this file only
 * gained the small hooks those need (nodesOf()) plus in-place refinements of
 * EXISTING functions it already owned (target-lock bracket in glowAt(),
 * leader-line labels in renderStaticLayer(), zigzag/bob/band-width tuning in
 * buildLayout()).
 *
 * Philosophy doc: vault/50_research/wall_design_philosophy_synaptic_current.md
 * Display-only — nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  /* ===== seeded RNG — deterministic per-id jitter (stable across polls) === */
  function hashStr(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function rngFor(id) { return mulberry32(hashStr(id)); }

  const colorRgbCache = new Map();
  function hexToRgb(hex) {
    let rgb = colorRgbCache.get(hex);
    if (rgb) return rgb;
    const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    rgb = m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [200, 200, 200];
    colorRgbCache.set(hex, rgb);
    return rgb;
  }
  function rgba(hex, a) { const [r, g, b] = hexToRgb(hex); return `rgba(${r},${g},${b},${a})`; }
  function mixHex(h1, h2, t) {
    // Must return '#rrggbb' (not 'rgb(...)') — hexToRgb()/rgba() above only
    // parse the '#hex' form, and the '.startsWith(\'#\')' checks at edgeFor()
    // shadowColor and drawField()'s micro-pulse color both gate on it; an
    // 'rgb(...)' string silently fell through both to grey/GATE_HALO cyan
    // instead of the blended cluster hue.
    const a = hexToRgb(h1), b = hexToRgb(h2);
    const hex = (v) => Math.round(a[v] + (b[v] - a[v]) * t).toString(16).padStart(2, '0');
    return `#${hex(0)}${hex(1)}${hex(2)}`;
  }

  const GATE_HALO = '#5fd7ff';
  const FEEDBACK_COLOR = '#ffb454'; // gold — G8->G2 plasticity strand
  // graft 4 (radial LINEAGE_HUES) — small hue spread so several live-open
  // strands bundling into the same endpoint (G6/G7) don't wash out into one
  // saturated white beam once additive-blended.
  const LINEAGE_HUES = ['#87d7ff', '#9fc7ff', '#7ec8e3', '#a7d8ff', '#8fe0d0'];
  const GATE_IDS = ['g1', 'g2', 'g3', 'g4', 'g5', 'g6', 'g7', 'g8'];

  let CLUSTER_COLOR = {};
  // Venue glow colors — same hexes as the page legend (.wall-venues) so a
  // firing ticker reads as "its exchange is alive" (Jin 2026-07-10).
  const VENUE_COLOR = { okx: '#5fdfff', cap: '#a87cff', alp: '#ffc84f' };
  const firingIds = new Set(); // mkt ids firing NOW (roster-driven, 1s poll)
  // Jarvis target-lock entrance timing (Jin 2026-07-10, feat/jarvis-language):
  // id -> performance.now() the instant a mkt dot's firing state flips on
  // (roster-driven, 1s poll granularity) — drives the 300ms outside->inside
  // bracket-contract animation in drawField()'s glowAt(); settled (>=300ms)
  // ids just draw the static locked bracket.
  const markerBorn = new Map();
  // Ticker pipeline migration (Jin 2026-07-10 "유니버스 소속이 옆으로 옆으로
  // 넘어가야"): a mkt dot with a live gate event GLIDES to a parking ring
  // around that gate nucleus and progresses rightward as later gates fire;
  // idle 150s -> glides home to its dust slot. Real-event-driven only.
  // Static bake already reruns every 1s poll, so parked dots are simply
  // skipped there and drawn live here — no ghost duplicates.
  const migrations = new Map(); // id -> {fx,fy,tx,ty,t,dur,phase,lastMs,gateIdx}
  const MIGRATE_IDLE_MS = 150000;
  // Strategy-constellation assignment (Jin 2026-07-10 "전략들이 보고있는
  // 티커들 링크해서 클러스터"): mkt ticker id -> its strategy node id.
  const tickerStrat = new Map();
  // LIVE wires (Jin 2026-07-10 "배선이 실시간으로 변경돼야"): real
  // strategy<->ticker interactions from SSE events — created the moment a
  // strategy actually fires on a ticker, fading over 15min, rewired live.
  // (The hash-assigned constellation spokes remain as the dim base cloth.)
  const liveWires = new Map(); // 'from>to' -> {from,to,color,born}
  const LIVE_WIRE_TTL = 900000;
  function touchWire(fromId, toId, color) {
    if (!screen[fromId] || !screen[toId]) return;
    liveWires.set(fromId + '>' + toId, { from: fromId, to: toId, color: color || '#8fb0c8', born: performance.now() });
  }
  let W = 1344, H = 962;
  const screen = {};      // id -> {x,y,r,depth,color,baseAlpha,bobAmp,bobSpeed,phaseOff,fireUntil,node}
  let gateScreen = [];    // 8x {x,y,fireUntil,pulsePhase} — index-aligned with GATE_IDS
  let livingIds = [];     // non-'mkt' node ids — get the per-frame parallax bob (graft 1c)
  let allNodes = [];
  let nodeById = {};
  const ambientEdges = [];
  const edgeCache = new Map();
  const activeGlowIds = new Set();

  function setSize(w, h) { W = w; H = h; }

  function jitteredBand(nodes, xMin, xMax, yMin, yMax, salt, jitterFrac) {
    const n = nodes.length;
    if (!n) return;
    const aspect = Math.max(0.15, (xMax - xMin) / Math.max(1, yMax - yMin));
    const cols = Math.max(1, Math.round(Math.sqrt(n * aspect)));
    const rows = Math.max(1, Math.ceil(n / cols));
    const cellW = (xMax - xMin) / cols, cellH = (yMax - yMin) / rows;
    nodes.forEach((node, idx) => {
      const r = rngFor(node.id + salt);
      const col = idx % cols, row = Math.floor(idx / cols);
      const jf = jitterFrac == null ? 0.82 : jitterFrac;
      screen[node.id] = {
        x: xMin + cellW * (col + 0.5) + (r() - 0.5) * cellW * jf,
        y: yMin + cellH * (row + 0.5) + (r() - 0.5) * cellH * jf,
      };
    });
  }

  function buildLayout(data) {
    allNodes = data.nodes || [];
    nodeById = {};
    allNodes.forEach((n) => { nodeById[n.id] = n; });
    CLUSTER_COLOR = {};
    (data.clusters || []).forEach((c) => { CLUSTER_COLOR[c.id] = c.color; });

    // Gate spine: gentle S-curve across the middle + a small deterministic
    // per-gate force-stagger (graft 1a, organic) so the 8 relay-hubs read as
    // woven INTO the constellation rather than a too-clean horizontal chain.
    gateScreen = GATE_IDS.map((gid, i) => {
      const t = i / (GATE_IDS.length - 1);
      const rg = rngFor(gid + ':stagger');
      const x = W * 0.085 + t * W * 0.83 + (rg() - 0.5) * W * 0.012;
      const y = H * 0.655 + Math.sin(t * Math.PI * 1.7 + 0.35) * H * 0.055
        + Math.cos(t * Math.PI * 0.9) * H * 0.02 + (rg() - 0.5) * H * 0.034;
      return { x, y, fireUntil: 0, pulsePhase: Math.random() * Math.PI * 2 };
    });

    const byCluster = {};
    allNodes.forEach((n) => { (byCluster[n.cluster] = byCluster[n.cluster] || []).push(n); });

    // Jin 2026-07-10 "줄서기 하는거야 뭐야": the roster arrives grouped by
    // venue/symbol, so consecutive grid cells held same-venue names and a
    // firing wave lit up as one straight queue. Deterministic hash-order
    // shuffle scatters venues/symbols across the band (stable across polls).
    const hashShuffle = (arr) => (arr || []).slice().sort((a, b) => hashStr(a.id + ':mix') - hashStr(b.id + ':mix'));
    // Strategy constellations (Jin 2026-07-10 "전략+보는 티커 = 클러스터,
    // 들어갈 땐 아래 메인 게이트로"): strategies anchor the TOP field; each
    // venue's tickers are dealt round-robin (deterministic hash order) to
    // that venue's strategies and orbit them in a golden-angle elliptical
    // cloud. Firing tickers then DESCEND to the gate spine below (migration)
    // — the top-to-bottom order finally reads as the real pipeline. The old
    // random mkt->strat links (the white-line convergence hub) die here.
    tickerStrat.clear();
    addWatchDrop.length = 0;
    // Milky-way BAND (Jin 2026-07-10 "구모양 말고 띠처럼"): one continuous
    // galactic band sweeps the top; strategies are bright knots ALONG it and
    // ticker clouds elongate along the band tangent so neighbouring
    // constellations blend into a single streak.
    const bandY = (x) => H * 0.23 + Math.sin((x / W) * Math.PI * 1.35 + 0.7) * H * 0.095;
    // STRATEGY RAIL (Jin 2026-07-11 "전체적으로 공간활용" + "열매마냥 매달린
    // 것"): full-width fixed-pitch instrument rail, venue-GROUPED (OKX block →
    // CAP → ALP so the venue colors read as sections), tiny zigzag only —
    // an engineered rail, not fruit dangling off the band.
    const stratsOrdered = (byCluster.strat || []).slice().sort((a, b) => {
      const va = String(a.exchange || ''), vb = String(b.exchange || '');
      return va === vb ? String(a.label || '').localeCompare(String(b.label || '')) : va.localeCompare(vb);
    });
    stratsOrdered.forEach((n, i) => {
      const r = rngFor(n.id + ':knot');
      const x = W * 0.03 + ((i + 0.5) / stratsOrdered.length) * W * 0.94 + (r() - 0.5) * W * 0.006;
      const zigzag = (i % 2 === 0 ? 1 : -1) * H * 0.011;
      screen[n.id] = { x, y: H * 0.505 + zigzag, bandAnchorY: bandY(x) };
    });
    const stratPool = { okx: [], cap: [], alp: [] };
    hashShuffle(byCluster.strat).forEach((n) => {
      const k = String(n.exchange || '').slice(0, 3).toLowerCase();
      if (stratPool[k]) stratPool[k].push(n);
    });
    const perStratIdx = new Map();
    const candRelaxIds = []; // candidates across venues — relaxed apart below
    ['okx', 'cap', 'alp'].forEach((vk) => {
      const pool = stratPool[vk];
      const ticks = hashShuffle((byCluster.mkt || []).filter((n) => String(n.exchange || '').slice(0, 3).toLowerCase() === vk));
      if (!pool.length) { // venue without strategies: park its dust low-left
        jitteredBand(ticks, W * 0.03, W * 0.2, H * 0.46, H * 0.55, ':orphan' + vk, 0.8);
        return;
      }
      // Galaxy profile (Jin 2026-07-10 "은하수처럼"): dense luminous core
      // thinning to a sparse halo (power-law radius), golden-angle swirl for
      // spiral-arm hint, per-cluster tilt. First pass counts members so the
      // radial falloff is normalized per constellation.
      const stratCount = new Map();
      ticks.forEach((n, i) => {
        const st = pool[i % pool.length];
        stratCount.set(st.id, (stratCount.get(st.id) || 0) + 1);
      });
      // Two layers (Jin 2026-07-10 "디밍된 전체를 백그라운드에 깔고,
      // 후보들만 밝게"): CANDIDATES (firing / recent signals / focus-tier /
      // hot intensity) ride their strategy knot in the band; the REST of the
      // tradable universe spreads WIDE and dim underneath as the backdrop.
      const isCandidate = (n) => n.state === 'firing'
        || (n.signal_count_30m || 0) > 0
        || (n.signal_count_4h || 0) > 0 // 저빈도 베뉴(ALP 1D) 4h 여운
        || (n.intensity || 0) >= 0.45
        || n.tier_label === 'S' || n.tier_label === 'A' || n.tier_label === 'B';
      const cands = ticks.filter(isCandidate);
      const backdrop = ticks.filter((n) => !isCandidate(n));
      jitteredBand(backdrop, W * 0.02, W * 0.98, H * 0.04, H * 0.46, ':bg' + vk, 0.9);
      backdrop.forEach((n) => {
        screen[n.id].bgLayer = true; // 강한 디밍 (알파 계산에서)
        const st = pool[Math.floor(rngFor(n.id + ':bgst')() * pool.length)];
        tickerStrat.set(n.id, st.id); // 발화 시 이주 목적지는 유지
      });
      cands.forEach((n, i) => {
        const st = pool[i % pool.length];
        const k = perStratIdx.get(st.id) || 0;
        perStratIdx.set(st.id, k + 1);
        const r = rngFor(n.id + ':orb');
        const gauss = () => (r() + r() + r() - 1.5) / 1.5;
        // Jin 2026-07-11 "오른쪽 폭 다 써도 되는거 아니야": candidates sweep
        // the FULL width along the band curve — the strategy relationship
        // lives in the wires + migration journey, not in x-position. The
        // sky above the monitor/exit/reflector district was dead space.
        const dySig = 68;
        const x = Math.max(8, Math.min(W * 0.97, W * (0.03 + r() * 0.94)));
        const y = Math.max(20, Math.min(H * 0.435, bandY(x) + gauss() * dySig));
        screen[n.id] = { x, y, isCand: true };
        screen[n.id].coreBoost = Math.min(1, n.intensity != null ? +n.intensity : 0.3);
        tickerStrat.set(n.id, st.id);
        candRelaxIds.push(n.id);
      });
    });
    // Min-separation relaxation (Jin 2026-07-11 "왤케 따닥따닥이야"): a few
    // deterministic repel passes so no two candidate dots overlap — each
    // ticker stays individually readable (뭉침 금지 계약).
    const SEP = 17;
    for (let pass = 0; pass < 4; pass++) {
      for (let i = 0; i < candRelaxIds.length; i++) {
        const a = screen[candRelaxIds[i]];
        for (let j = i + 1; j < candRelaxIds.length; j++) {
          const b = screen[candRelaxIds[j]];
          let dx = b.x - a.x, dy = b.y - a.y;
          const d2 = dx * dx + dy * dy;
          if (d2 >= SEP * SEP) continue;
          const d = Math.sqrt(d2) || 0.001;
          const push = (SEP - d) / 2;
          dx /= d; dy /= d;
          if (d2 === 0) { dx = 1; dy = 0; }
          a.x -= dx * push; a.y -= dy * push;
          b.x += dx * push; b.y += dy * push;
        }
      }
    }
    candRelaxIds.forEach((id) => {
      const s = screen[id];
      s.x = Math.max(8, Math.min(W * 0.97, s.x));
      s.y = Math.max(20, Math.min(H * 0.435, s.y));
    });
    // watch (G4 pre-entry probes) ride NEXT TO their ticker inside the
    // constellation ("전략이랑 프로브랑 같이").
    // Watch TIER (Jin 2026-07-10 vertical hierarchy: 티커 은하수 -> 와치
    // 티어 -> 전략 소행성대 -> 버스): G4 watchlist entries sit in their own
    // row between the band and the strategy lane, at their ticker's x.
    (byCluster.watch || []).forEach((n) => {
      const mkt = allNodes.find((m) => m.cluster === 'mkt' && m.ticker === n.ticker && m.exchange === n.exchange);
      const base = mkt && screen[mkt.id];
      const r = rngFor(n.id + ':wt');
      // full-width follow (Jin 2026-07-11): the 0.60W clamp piled every
      // right-side watch chip onto one exact x — track the ticker instead.
      const x = Math.min(W * 0.97, base ? base.x : W * (0.22 + r() * 0.38));
      screen[n.id] = { x, y: H * 0.445 + (r() - 0.5) * H * 0.02 };
      if (base) {
        // short drop-line ticker -> its watch entry (the "선발" visual)
        addWatchDrop.push([mkt.id, n.id]);
      }
    });

    const gG3 = gateScreen[2], gG6 = gateScreen[5], gG7 = gateScreen[6], gG8 = gateScreen[7];
    // REGIME row (Jin 2026-07-11 "공간활용"): even-pitch labelled row filling
    // the bottom-left void (above the DROP LANE overlay), feeding g3.
    (byCluster.reg || []).forEach((n, j, arr) => {
      const x = W * 0.05 + ((j + 0.5) / Math.max(1, arr.length)) * W * 0.25;
      screen[n.id] = { x, y: H * 0.775 + ((j % 2) ? H * 0.018 : 0) };
    });
    // OPEN POSITIONS — even double-arc fan under g6 (was a jittered blob):
    // alternating inner/outer ring, P/L colors stay the only green/red.
    (byCluster.pos || []).forEach((n, j, arr) => {
      const ring = j % 2;
      const t = (Math.floor(j / 2) + 0.5) / Math.max(1, Math.ceil(arr.length / 2));
      const ang = Math.PI * (0.12 + t * 0.76);
      const rad = 60 + ring * 26;
      screen[n.id] = { x: gG6.x + Math.cos(ang) * rad * 1.35, y: gG6.y + Math.sin(ang) * rad * 0.85 };
    });
    // G6 monitor advisors — ALL probe readings carry gate_id=6 (verified),
    // so they belong ON g6: a tidy left-to-top arc hugging the nucleus
    // (same satellite grammar as G1's tier census), clear of the pos band
    // below and the exit side to the right (Jin: "게이트 주변에 정리").
    (byCluster.probe || []).forEach((n, j) => {
      const ang = Math.PI * 1.05 + j * 0.42;
      screen[n.id] = { x: gG6.x + Math.cos(ang) * 84, y: gG6.y + Math.sin(ang) * 58 };
    });
    jitteredBand(byCluster.exit || [], gG7.x - 60, gG7.x + 140, gG7.y + 60, gG7.y + 115, ':exit', 0.7);
    jitteredBand(byCluster.exit_tally || [], gG7.x - 140, gG7.x + 60, gG7.y + 125, gG7.y + 175, ':exittally', 0.7);

    const meta = [].concat(byCluster.action || [], byCluster.obs || [], byCluster.orbit || [], byCluster.axis || []);
    meta.forEach((node) => {
      const r = rngFor(node.id + ':meta');
      const ang = r() * Math.PI * 2, rad = 60 + r() * 95;
      screen[node.id] = { x: gG8.x + Math.cos(ang) * rad * 1.15, y: gG8.y + Math.sin(ang) * rad * 0.62 - 10 };
    });

    livingIds = [];
    allNodes.forEach((node) => {
      const s = screen[node.id];
      if (!s) return;
      const r = rngFor(node.id + ':depth');
      const depth = Math.floor(r() * 3);
      s.depth = depth;
      s.color = CLUSTER_COLOR[node.cluster] || '#9fb0c8';
      // Jin 2026-07-10 "익스체인지 색은?": universe/watch tickers are venue-
      // tinted so the whole field reads by exchange (matches the .wall-venues
      // legend chips) — dim at rest via baseAlpha, full-strength when the
      // firing glow / migration halo lights the same hue on top.
      // Jin 2026-07-10 color contract: P/L green/red is EXCLUSIVE to open
      // positions; every other exchange-carrying entity (mkt dust, watch,
      // strategies, exits, …) wears its venue color passively — top of the
      // field, migrating to a gate, or firing alike. Venue-less meta nodes
      // (obs/action/axis…) keep their cluster color.
      if (node.cluster === 'pos') {
        s.color = (node.pnl_usd || 0) >= 0 ? '#7dffa8' : '#ff7d8a';
      } else {
        const vc = VENUE_COLOR[String(node.exchange || '').slice(0, 3).toLowerCase()];
        if (vc) s.color = vc;
        // venue-less SYSTEM meta nodes (action=gate-verdict tallies, obs=
        // health, orbit/axis) — neutral steel, dimmer: their old cluster
        // pinks/olives read as mystery entities next to the venue/P&L hues
        // (Jin 2026-07-10 "핑크색 저건 왜 색이 저래?").
        else if (node.cluster === 'action' || node.cluster === 'obs'
                 || node.cluster === 'orbit' || node.cluster === 'axis'
                 || node.cluster === 'exit' || node.cluster === 'exit_tally'
                 || node.cluster === 'reg') {
          s.color = '#8a94b0';
        }
      }
      if (node.cluster === 'mkt') {
        const cb = s.coreBoost || 0;
        s.r = 1.15 + depth * 0.55 + cb * 0.45;
        s.baseAlpha = 0.16 + depth * 0.09 + (node.intensity || 0.3) * 0.12 + cb * 0.11;
        if (s.bgLayer) { s.baseAlpha *= 0.42; s.r = Math.min(s.r, 1.35); } // 배경 유니버스 강한 디밍
        // Jin 2026-07-11 "자비스 돌아가듯이 이펙트를 넣던지": candidates
        // leave the baked dust and FLOAT — slow organic wander per dot, so
        // the band reads as live machinery, not a printed starfield.
        // Backdrop dust stays baked (perf).
        if (s.isCand) {
          const rb = rngFor(node.id + ':bob');
          s.bobAmp = 3.5 + rb() * 2.5;
          s.bobSpeed = 0.3 + rb() * 0.3;
          s.phaseOff = rb() * Math.PI * 2;
          livingIds.push(node.id);
        }
      } else if (node.cluster === 'watch') {
        // chip IS the identity — the dangling dot goes near-invisible
        // (Jin 2026-07-11 "열매마냥 매달린것도 저게 최선이야?")
        s.r = 1.3;
        s.baseAlpha = 0.26 + (node.intensity || 0.4) * 0.12;
      } else if (node.cluster === 'strat') {
        s.r = 4.2 + Math.min(2.2, Math.log((node.trades_24h || 1) + 1) * 0.7);
        s.baseAlpha = 0.55 + Math.min(0.35, (node.intensity || 0.3) * 0.4);
        if (node.state === 'dormant') { s.baseAlpha *= 0.45; s.r *= 0.8; } // 휴면 전략 = 자리만
      } else if (node.cluster === 'probe') {
        s.r = 2.6;
        s.baseAlpha = node.state === 'dormant' ? 0.22 : 0.55 + (node.intensity || 0.3) * 0.3;
      } else {
        s.r = 3.0 + depth * 0.4;
        s.baseAlpha = 0.5 + (node.intensity || 0.4) * 0.3;
      }
      s.fireUntil = 0;
      s.node = node;
      // graft 1c (organic parallax bob) — a tiny constant sin/cos drift on
      // every NAMED node (everything but the 'mkt' dust field, which stays
      // baked/static texture) so the spine never reads as fully frozen
      // between comets. Amplitude is sub-pixel-to-2px — "current", not motion.
      if (node.cluster !== 'mkt') {
        const rb = rngFor(node.id + ':bob');
        // Jin 2026-07-10 "액티비티 있는 애들은 움직이고 전략은 스태틱":
        // pos/watch = the living probes — visible slow wander (4-7px);
        // strat = anchors, fully static; the rest keep the sub-2px current.
        if (node.cluster === 'strat') {
          // Jin 2026-07-10 revision (feat/jarvis-language, "좀 움직여도"):
          // supersedes the earlier fully-static strat mandate below — a
          // small 1.8-3.2px wander so the asteroid belt reads as live
          // current too, not a frozen anchor row.
          s.bobAmp = 1.8 + rb() * 1.4;
        } else if (node.cluster === 'pos' || node.cluster === 'watch') {
          s.bobAmp = 4 + rb() * 3;
        } else if (node.cluster === 'probe') {
          s.bobAmp = node.state === 'dormant' ? 0 : 3 + rb() * 2.5;
        } else {
          s.bobAmp = 0.6 + depth * 0.55;
        }
        s.bobSpeed = 0.35 + rb() * 0.35;
        s.phaseOff = rb() * Math.PI * 2;
        livingIds.push(node.id); // amp 0 (strat) draws static at its anchor
      }
    });
  }

  // Cheap per-poll refresh: node dynamic fields (state/intensity/pnl) change
  // far more often than the roster's node-id SET does. Recomputing baseAlpha
  // in place (O(n), no position/edge rebuild) lets the 1s poll stay honest
  // about "who's lit/firing now" WITHOUT re-running the O(n^2) whisper-mesh
  // K-NN scan every tick (that only reruns on an actual structural change —
  // see wall_spine_hud.js's node-count fingerprint check).
  function refreshNodeState(nodes) {
    const prevFiring = firingIds.size ? new Set(firingIds) : null;
    firingIds.clear();
    (nodes || []).forEach((n) => {
      const s = screen[n.id];
      if (!s) return;
      s.node = n;
      if (n.cluster === 'mkt') s.baseAlpha = 0.16 + s.depth * 0.09 + (n.intensity || 0.3) * 0.12;
      else if (n.cluster === 'watch') s.baseAlpha = 0.26 + (n.intensity || 0.4) * 0.12; // buildLayout dim과 동기 (리뷰 MED)
      else if (n.cluster === 'strat') s.baseAlpha = 0.55 + Math.min(0.35, (n.intensity || 0.3) * 0.4);
      else s.baseAlpha = 0.5 + (n.intensity || 0.4) * 0.3;
      // live P/L tint for open positions (sign can flip between polls)
      if (n.cluster === 'pos') s.color = (n.pnl_usd || 0) >= 0 ? '#7dffa8' : '#ff7d8a';
      // Jin 2026-07-10 "살아있는 애들은 익스체인지 색으로 빛나야": a mkt dot
      // whose roster state says it's firing NOW gets a persistent
      // venue-colored breathing glow (drawn live in drawField — the dust
      // itself stays baked in the static layer). Roster-driven, no
      // fabrication; refreshed every 1s poll.
      if (n.cluster === 'mkt'
          && (n.state === 'firing' || (n.signal_count_30m || 0) > 0)) {
        s.venueColor = VENUE_COLOR[String(n.exchange || '').slice(0, 3).toLowerCase()] || s.color;
        s.fireLevel = Math.max(0.35, Math.min(1, n.intensity != null ? +n.intensity : 0.6));
        firingIds.add(n.id);
      }
    });
    // Jarvis target-lock entrance (Jin 2026-07-10, feat/jarvis-language):
    // only the NEWLY-firing ids get a fresh born time — a still-firing id
    // keeps its original timestamp, so its bracket stays "settled" rather
    // than re-popping every poll.
    const now = performance.now();
    firingIds.forEach((id) => { if (!prevFiring || !prevFiring.has(id)) markerBorn.set(id, now); });
    markerBorn.forEach((_, id) => { if (!firingIds.has(id)) markerBorn.delete(id); });
  }

  // Called by wall_spine.fireGateEvent for a REAL g1..g5 gate event on a mkt
  // ticker: glide it to a parking slot ringed around that gate nucleus. A
  // later gate re-targets the SAME dot further right (the 옆으로 progression).
  // Journey choreography (Jin 2026-07-10 "G2에 붙었다가 밸리데이터로
  // 넘어가고" — one tick's G1..G5 events arrive as one SSE batch, so a
  // naive re-target teleported dots to the LAST gate, sometimes out of
  // order): stops queue per ticker, always played ASCENDING with a short
  // dwell at each gate — the dot visibly walks G2 -> G3 -> G4 -> G5.
  const MIGRATE_DWELL_MS = 900;
  function migrateTicker(nodeId, gateIdx) {
    const s = screen[nodeId];
    if (!s || !gateScreen[gateIdx]) return;
    let m = migrations.get(nodeId);
    if (!m) {
      m = { fx: s.x, fy: s.y, tx: s.x, ty: s.y, t: 1, dur: 0.3, phase: 'out',
            lastMs: performance.now(), gateIdx: -1, stops: [], dwellUntil: 0 };
      // Jin 2026-07-10 "전략이 활성화 티커 받아서 아래로 내리는 형상": a
      // fresh journey first drops to the ticker's own strategy asteroid,
      // dwells, THEN walks the gate spine.
      const stId = tickerStrat.get(nodeId);
      const st = stId && screen[stId];
      if (st) {
        const r = rngFor(nodeId + ':via');
        m.t = 0; m.dur = 0.8 + r() * 0.3;
        m.tx = st.x + (r() - 0.5) * 16;
        m.ty = st.y - 8 - r() * 6;
        m.via = true;
      }
      migrations.set(nodeId, m);
    }
    if (m.phase === 'return') { m.phase = 'out'; m.gateIdx = -1; }
    if (gateIdx > m.gateIdx && m.stops.indexOf(gateIdx) < 0) {
      m.stops.push(gateIdx);
      m.stops.sort((a, b) => a - b);
    }
    m.lastMs = performance.now();
    // NOTE: no immediate advance — an SSE batch delivers one journey's
    // G1..G5 events in a single JS task, so departing on the FIRST event
    // would drop the rest via the monotonic guard. The frame loop departs
    // after the whole batch has queued (sorted ascending).
  }
  function maybeAdvance(nodeId, m) {
    if (m.t < 1 || !m.stops.length) return;
    if (performance.now() < (m.dwellUntil || 0)) return;
    const s = screen[nodeId];
    if (!s) return;
    const gi = m.stops.shift();
    const gs = gateScreen[gi];
    if (!gs) return;
    const r = rngFor(nodeId + ':park:' + gi);
    const ang = r() * Math.PI * 2, rad = 30 + r() * 22;
    const cur = migratePos(m, s);
    m.fx = cur.x; m.fy = cur.y;
    m.tx = gs.x + Math.cos(ang) * rad;
    m.ty = gs.y + Math.sin(ang) * rad;
    m.t = 0; m.dur = 0.8 + r() * 0.4; m.phase = 'out';
    m.gateIdx = gi; m.dwellUntil = 0;
  }
  // Send a migrated dot home (entry fill: its life continues as a pos node;
  // or idle decay). No-op when not migrating.
  function migrateHome(nodeId) {
    const m = migrations.get(nodeId);
    const s = screen[nodeId];
    if (!m || !s) return;
    const cur = migratePos(m, s);
    migrations.set(nodeId, {
      fx: cur.x, fy: cur.y, tx: s.x, ty: s.y,
      t: 0, dur: 1.2, phase: 'return', lastMs: performance.now(),
      gateIdx: m.gateIdx, stops: [], dwellUntil: 0,
    });
  }
  function migratePos(m, s) {
    const k = easeOut(Math.min(1, m.t));
    return { x: m.fx + (m.tx - m.fx) * k, y: m.fy + (m.ty - m.fy) * k };
  }
  function easeOut(t) { return 1 - Math.pow(1 - t, 3); }
  // Jarvis target-lock bracket (Jin 2026-07-10, feat/jarvis-language): a
  // non-rotating 4-point hairline corner bracket around an actively-engaged
  // node. lockAge==null -> settled (fully closed); lockAge<300 -> the
  // bracket is still contracting outside->inside (element-local, one-shot).
  function drawTargetLock(ctx, x, y, r, color, alpha, lockAge) {
    const settled = lockAge == null || lockAge >= 300;
    const k = settled ? 1 : easeOut(Math.max(0, lockAge) / 300);
    const half = r * (2.4 - k * 1.4);
    const tick = half * 0.34;
    const a = alpha * (settled ? 1 : (0.3 + 0.7 * k));
    ctx.strokeStyle = rgba(color, a);
    ctx.lineWidth = 0.75;
    [[1, -1], [1, 1], [-1, 1], [-1, -1]].forEach(([sx, sy]) => {
      ctx.beginPath();
      ctx.moveTo(x + sx * half, y + sy * half - sy * tick);
      ctx.lineTo(x + sx * half, y + sy * half);
      ctx.lineTo(x + sx * half - sx * tick, y + sy * half);
      ctx.stroke();
    });
  }
  // venue color for any exchange string ('okx'/'capital'/'alpaca' or 3-letter)
  function venueColorOf(exchange) {
    return VENUE_COLOR[String(exchange || '').slice(0, 3).toLowerCase()] || null;
  }

  function edgeFor(fromId, toId, x1, y1, x2, y2, opts) {
    const key = fromId + '->' + toId;
    let e = edgeCache.get(key);
    if (e) return e;
    const r = rngFor(key);
    const dx = x2 - x1, dy = y2 - y1;
    const dist = Math.hypot(dx, dy) || 1;
    const nx = -dy / dist, ny = dx / dist;
    const bowScale = (opts && opts.bowScale) || 1;
    const bow = (0.09 + r() * 0.20) * dist * (r() < 0.5 ? -1 : 1) * bowScale;
    // Jin 2026-07-10 "화면 밖으로 빠진 라인": clamp the bow control point to
    // the canvas so no strand arcs off-frame (big-bow feedback/lineage
    // bundles were sweeping outside and reading as cut lines).
    const pad = 14;
    const mx = Math.max(pad, Math.min(W - pad, (x1 + x2) / 2 + nx * bow));
    const my = Math.max(pad, Math.min(H - pad, (y1 + y2) / 2 + ny * bow));
    const color = (opts && opts.color) || '#5fd7ff';
    const alpha = (opts && opts.alpha) != null ? opts.alpha : 0.12;
    e = {
      x1, y1, x2, y2,
      c1x: x1 + (mx - x1) * 0.55, c1y: y1 + (my - y1) * 0.55,
      c2x: x2 + (mx - x2) * 0.55, c2y: y2 + (my - y2) * 0.55,
      color, alpha, width: (opts && opts.width) || 0.7, glow: !!(opts && opts.glow),
      kind: (opts && opts.kind) || 'ambient',
      strokeStyle: rgba(color, alpha),
      shadowColor: color.startsWith('#') ? color : GATE_HALO,
    };
    edgeCache.set(key, e);
    return e;
  }
  function bezierPoint(e, t) {
    const u = 1 - t;
    return {
      x: u * u * u * e.x1 + 3 * u * u * t * e.c1x + 3 * u * t * t * e.c2x + t * t * t * e.x2,
      y: u * u * u * e.y1 + 3 * u * u * t * e.c1y + 3 * u * t * t * e.c2y + t * t * t * e.y2,
    };
  }
  function addAmbient(fromId, toId, x1, y1, x2, y2, opts) { ambientEdges.push(edgeFor(fromId, toId, x1, y1, x2, y2, opts)); }

  // graft 1b (organic whisper mesh) — K-nearest-neighbour low-alpha strands
  // across the WHOLE screen (gate cores included as candidates), so the
  // spine reads as part of one entangled field, not a separate river next to
  // a sparser background. Built once per layout — never touched per-frame.
  function buildWhisperMesh() {
    const pts = allNodes.map((n) => ({ id: n.id, x: screen[n.id] && screen[n.id].x, y: screen[n.id] && screen[n.id].y, color: (screen[n.id] || {}).color }))
      .filter((p) => p.x != null);
    gateScreen.forEach((g, i) => pts.push({ id: GATE_IDS[i], x: g.x, y: g.y, color: GATE_HALO }));
    const K = 3;
    const used = new Set();
    for (const a of pts) {
      const dists = [];
      for (const b of pts) {
        if (a === b) continue;
        const dx = a.x - b.x, dy = a.y - b.y;
        dists.push([dx * dx + dy * dy, b]);
      }
      dists.sort((p, q) => p[0] - q[0]);
      for (let k = 0; k < K && k < dists.length; k++) {
        const b = dists[k][1];
        const key = a.id < b.id ? a.id + '|' + b.id : b.id + '|' + a.id;
        if (used.has(key)) continue;
        used.add(key);
        addAmbient(a.id, b.id, a.x, a.y, b.x, b.y, { color: mixHex(a.color || '#8fb0c8', b.color || '#8fb0c8', 0.5), alpha: 0.035 + rngFor(key)() * 0.05, width: 0.55, bowScale: 0.5 });
      }
    }
  }

  function buildEdges(data) {
    ambientEdges.length = 0;
    edgeCache.clear();

    for (let i = 0; i < gateScreen.length - 1; i++) {
      const a = gateScreen[i], b = gateScreen[i + 1];
      addAmbient(GATE_IDS[i], GATE_IDS[i + 1], a.x, a.y, b.x, b.y, { color: GATE_HALO, alpha: 0.34, width: 1.6, glow: true, kind: 'backbone', bowScale: 0.35 });
    }
    {
      const a = gateScreen[7], b = gateScreen[1];
      addAmbient('g8', 'g2', a.x, a.y, b.x, H * 0.985, { color: FEEDBACK_COLOR, alpha: 0.26, width: 1.3, glow: true, kind: 'feedback', bowScale: 2.6 });
    }

    const strat = allNodes.filter((n) => n.cluster === 'strat');
    // Constellation spokes: each ticker links to ITS strategy (short local
    // web, venue-colored) — replaces the old random venue-pool links whose
    // few targets became the white-line convergence hub Jin flagged.
    tickerStrat.forEach((stratId, mktId) => {
      const a = screen[mktId], b = screen[stratId];
      const n = nodeById[mktId];
      if (!a || !b || !n) return;
      const vc = venueColorOf(n.exchange) || '#8fb0c8';
      addAmbient(mktId, stratId, a.x, a.y, b.x, b.y, { color: vc, alpha: 0.04 + (n.intensity || 0.2) * 0.04, width: 0.5, bowScale: 0.4 });
    });

    // Descent paths are WHISPERS, not beams (Jin: the additive pile-up of
    // 26+32 bright lines into one pixel was the "white broom"). Ring-offset
    // arrivals so nothing converges on a single point; venue-colored.
    const g4 = gateScreen[3];
    allNodes.filter((n) => n.cluster === 'watch').forEach((n) => {
      const a = screen[n.id]; if (!a) return;
      const r = rngFor(n.id + ':g4arr');
      const ang = r() * Math.PI * 2, rad = 16 + r() * 18;
      addAmbient(n.id, 'g4', a.x, a.y, g4.x + Math.cos(ang) * rad, g4.y + Math.sin(ang) * rad,
        { color: venueColorOf(n.exchange) || '#8fb0c8', alpha: 0.05 + (n.intensity || 0.3) * 0.05, width: 0.6, bowScale: 0.35 });
    });

    const g2 = gateScreen[1];
    strat.forEach((n) => {
      const a = screen[n.id]; if (!a) return;
      const r = rngFor(n.id + ':g2arr');
      const ang = r() * Math.PI * 2, rad = 18 + r() * 22;
      addAmbient(n.id, 'g2', a.x, a.y, g2.x + Math.cos(ang) * rad, g2.y + Math.sin(ang) * rad,
        { color: venueColorOf(n.exchange) || CLUSTER_COLOR.strat, alpha: 0.06 + (n.intensity || 0.3) * 0.05, width: 0.6, glow: n.state === 'firing', bowScale: 0.3 });
    });

    const g3 = gateScreen[2];
    allNodes.filter((n) => n.cluster === 'reg').forEach((n) => {
      const a = screen[n.id]; if (!a) return;
      addAmbient(n.id, 'g3', a.x, a.y, g3.x, g3.y, { color: CLUSTER_COLOR.reg, alpha: 0.28, width: 1.0 });
    });

    const g6 = gateScreen[5];
    allNodes.filter((n) => n.cluster === 'pos').forEach((n) => {
      const a = screen[n.id]; if (!a) return;
      addAmbient(n.id, 'g6', a.x, a.y, g6.x, g6.y, { color: CLUSTER_COLOR.pos, alpha: 0.4, width: 1.3, glow: true });
    });

    const g7 = gateScreen[6];
    allNodes.filter((n) => n.cluster === 'exit').forEach((n) => {
      const a = screen[n.id]; if (!a) return;
      addAmbient(n.id, 'g7', a.x, a.y, g7.x, g7.y, { color: CLUSTER_COLOR.exit, alpha: 0.22, width: 0.9 });
    });
    allNodes.filter((n) => n.cluster === 'exit_tally').forEach((n) => {
      const a = screen[n.id]; if (!a) return;
      const w = 0.6 + Math.min(2.2, Math.log((n.count || 1) + 1) * 0.6);
      addAmbient(n.id, 'g7', a.x, a.y, g7.x, g7.y, { color: CLUSTER_COLOR.exit_tally, alpha: 0.28, width: w, glow: true });
    });

    const g8 = gateScreen[7];
    ['action', 'obs', 'orbit', 'axis'].forEach((cl) => {
      allNodes.filter((n) => n.cluster === cl).forEach((n) => {
        const a = screen[n.id]; if (!a) return;
        addAmbient(n.id, 'g8', a.x, a.y, g8.x, g8.y, { color: CLUSTER_COLOR[cl], alpha: 0.16, width: 0.6 });
      });
    });

    // Real lifecycle chains — open trades (bright, pulsing) + closed-trade
    // aggregate (width by real frequency). graft 4 (radial LINEAGE_HUES): the
    // open-lifecycle strands get a small deterministic hue spread instead of
    // one flat mix, so several bundling into the same G6/G7 endpoint stay
    // distinguishable once additive-blended rather than washing to white.
    const closedPairCount = new Map();
    (data.lifecycle_paths || []).forEach((p) => {
      const ids = p.node_ids;
      if (p.kind === 'open') {
        // Per-path lens offset at shared waypoints (Jin: 13 opens all route
        // through the CURRENT REGIME node — e.g. reg_regime_chop — and the
        // point-convergence read as "쪼그만 점이 다 잡고있다"). Each path
        // passes the waypoint through its own offset in a ~30px lens, so the
        // bundle reads as a braid, not a knot. Unique edge keys per path
        // (same strat->reg pair repeats across positions).
        // Regime is CONTEXT, not a pipeline station (Jin 2026-07-10 "모니터는
        // 전략이랑 연결돼야"): strand runs strategy -> position DIRECT; the
        // entry regime hangs off the position as a thin faint context link.
        const mainIds = ids.filter((id) => id.indexOf('reg_') !== 0);
        const regId = ids.find((id) => id.indexOf('reg_') === 0);
        const pr = rngFor((mainIds[mainIds.length - 1] || 'p') + ':lens');
        const la = pr() * Math.PI * 2, lr = 8 + pr() * 22;
        const ox = Math.cos(la) * lr, oy = Math.sin(la) * lr;
        for (let i = 0; i < mainIds.length - 1; i++) {
          const a = screen[mainIds[i]], b = screen[mainIds[i + 1]];
          if (!a || !b) continue;
          const midA = i > 0, midB = (i + 1) < (mainIds.length - 1);
          const key = mainIds[i] + '~' + (mainIds[mainIds.length - 1] || '');
          const hue = LINEAGE_HUES[Math.floor(rngFor(key + ':hue')() * LINEAGE_HUES.length)];
          addAmbient(key, mainIds[i + 1], a.x + (midA ? ox : 0), a.y + (midA ? oy : 0),
            b.x + (midB ? ox : 0), b.y + (midB ? oy : 0),
            { color: hue, alpha: 0.3, width: 1.1, glow: true, kind: 'live-open' });
        }
        if (regId && mainIds.length) {
          const posId = mainIds[mainIds.length - 1];
          const a = screen[posId], b = screen[regId];
          if (a && b) addAmbient(posId + '~ctx', regId, a.x, a.y, b.x, b.y, { color: '#8a94b0', alpha: 0.08, width: 0.5 });
        }
      } else {
        const mainIds = ids.filter((id) => id.indexOf('reg_') !== 0);
        for (let i = 0; i < mainIds.length - 1; i++) {
          const key = mainIds[i] + '->' + mainIds[i + 1];
          closedPairCount.set(key, (closedPairCount.get(key) || 0) + 1);
        }
      }
    });
    closedPairCount.forEach((count, key) => {
      const [fromId, toId] = key.split('->');
      const a = screen[fromId], b = screen[toId];
      if (!a || !b) return;
      const w = 0.5 + Math.min(2.6, Math.log(count + 1) * 0.8);
      // Color contract (Jin 2026-07-10): the old pink mix glowed on recently-
      // traded tickers and read as a mystery hue ("핑크색 저건 왜 색이 저래?").
      // Closed-lifecycle history strands wear the ticker's VENUE color, dim —
      // history is not money; P/L green/red stays exclusive to positions/exits.
      const src = nodeById[fromId];
      const strandColor = (src && venueColorOf(src.exchange))
        || CLUSTER_COLOR[(src && src.cluster) || ''] || '#8a90a0';
      addAmbient(fromId, toId, a.x, a.y, b.x, b.y, { color: strandColor, alpha: 0.13 + Math.min(0.2, count * 0.01), width: w });
    });

    // watch drop-lines: ticker -> its watch-tier entry (then watch -> g4)
    addWatchDrop.forEach(([mid, wid]) => {
      const a = screen[mid], b = screen[wid];
      const n = nodeById[wid];
      if (!a || !b || !n) return;
      addAmbient(mid + '>w', wid, a.x, a.y, b.x, b.y, { color: venueColorOf(n.exchange) || '#8fb0c8', alpha: 0.22, width: 0.7, bowScale: 0.3 });
    });
    addWatchDrop.length = 0;

    // Probe wiring (Jin: "프로브들은 연결이 하나도 안 되는 거야?") —
    // anchor line to G6 (they are the monitor's advisors) + live links to
    // the positions they actually read in the last 30m (server probe_links).
    const g6gate = gateScreen[5];
    (allNodes.filter((n) => n.cluster === 'probe')).forEach((n) => {
      const a = screen[n.id];
      if (!a || !g6gate) return;
      addAmbient(n.id, 'g6', a.x, a.y, g6gate.x, g6gate.y, { color: '#8a94b0', alpha: 0.18, width: 0.7, bowScale: 0.4 });
    });
    (lastProbeLinks || []).forEach((lk) => {
      const a = screen[lk.probe], b = screen[lk.pos];
      if (!a || !b) return;
      addAmbient(lk.probe, lk.pos, a.x, a.y, b.x, b.y, { color: '#8a94b0', alpha: 0.3, width: 0.8, glow: true });
    });

    buildWhisperMesh();
  }
  let lastProbeLinks = [];
  const addWatchDrop = []; // [mktId, watchId] — buildLayout이 채우고 buildEdges가 그림
  function setProbeLinks(links) { lastProbeLinks = links || []; }

  /* ===== micro-pulse pool — the unbroken current ===== */
  const MAX_PULSES = 46;
  const pulses = [];
  function spawnPulse() {
    if (pulses.length >= MAX_PULSES || !ambientEdges.length) return;
    pulses.push({ e: ambientEdges[Math.floor(Math.random() * ambientEdges.length)], t: 0, speed: 0.10 + Math.random() * 0.16 });
  }
  function stepPulses(dt) {
    for (let i = pulses.length - 1; i >= 0; i--) {
      pulses[i].t += pulses[i].speed * dt;
      if (pulses[i].t >= 1) pulses.splice(i, 1);
    }
    if (Math.random() < 0.62) spawnPulse();
  }

  function markFire(id, ms) {
    if (!id) return;
    if (/^g\d$/.test(id)) {
      const gs = gateScreen[parseInt(id[1], 10) - 1];
      if (gs) gs.fireUntil = Math.max(gs.fireUntil, performance.now() + ms);
      return;
    }
    const s = screen[id];
    if (!s) return;
    s.fireUntil = Math.max(s.fireUntil, performance.now() + ms);
    activeGlowIds.add(id);
  }
  // Returns null (not a partial list) unless EVERY hop resolves, so callers
  // never render a comet that silently skips a missing hop. `reversed` marks
  // segments only found under the opposite cache key (fromId/toId swapped —
  // e.g. exit_tally nodes are wired tally->g7, but an exit comet travels
  // g7->tally) so the renderer samples the bezier at (1-t), not t — without
  // this a reversed segment would visually TELEPORT to its far end at t=0.
  function pathEdges(ids) {
    const es = [];
    for (let i = 0; i < ids.length - 1; i++) {
      const fwd = edgeCache.get(ids[i] + '->' + ids[i + 1]);
      if (fwd) { es.push({ e: fwd, a: ids[i], b: ids[i + 1], reversed: false }); continue; }
      const rev = edgeCache.get(ids[i + 1] + '->' + ids[i]);
      if (rev) { es.push({ e: rev, a: ids[i], b: ids[i + 1], reversed: true }); continue; }
      return [];
    }
    return es;
  }

  /* ===== draw: background + static field (called once into staticCtx) ===== */
  function drawBackground(ctx) {
    ctx.globalCompositeOperation = 'source-over';
    const g = ctx.createRadialGradient(W * 0.5, H * 0.46, H * 0.05, W * 0.5, H * 0.5, H * 0.95);
    g.addColorStop(0, '#0a1420'); g.addColorStop(0.55, '#060a14'); g.addColorStop(1, '#03040a');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
  }
  function drawEdge(ctx, e, alphaMul) {
    ctx.beginPath();
    ctx.moveTo(e.x1, e.y1);
    ctx.bezierCurveTo(e.c1x, e.c1y, e.c2x, e.c2y, e.x2, e.y2);
    ctx.strokeStyle = (alphaMul == null || alphaMul === 1) ? e.strokeStyle : rgba(e.color, e.alpha * alphaMul);
    ctx.lineWidth = e.width;
    if (e.glow) { ctx.shadowColor = e.shadowColor; ctx.shadowBlur = 5; }
    ctx.stroke();
    ctx.shadowBlur = 0;
  }
  function drawDot(ctx, x, y, r, color, alpha, glowBlur) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = rgba(color, alpha);
    if (glowBlur) { ctx.shadowColor = color; ctx.shadowBlur = glowBlur; }
    ctx.fill();
    ctx.shadowBlur = 0;
  }
  // Jarvis callout leader line (Jin 2026-07-10, feat/jarvis-language): a
  // short one-bend hairline elbow from the node down to an offset label,
  // instead of text sitting flush under the dot. `side` (+1/-1) is a stable
  // per-id hash bit so neighbouring labels alternate left/right rather than
  // stacking into one unreadable column.
  function drawLeaderLabel(ctx, x, y, text, color, alpha, side, drop) {
    const midY = y + 7, endX = x + side * 9, endY = y + 13 + (drop || 0);
    ctx.beginPath();
    ctx.moveTo(x, y); ctx.lineTo(x, midY); ctx.lineTo(endX, endY);
    ctx.strokeStyle = rgba(color, alpha * 0.55);
    ctx.lineWidth = 0.6;
    ctx.stroke();
    ctx.textAlign = side > 0 ? 'left' : 'right';
    ctx.fillStyle = rgba(color, alpha);
    ctx.fillText(text, endX + side * 2, endY + 3);
  }
  // Static texture — background edges + the 'mkt' dust field only (baked
  // once; the single biggest per-frame cost at this node count).
  function renderStaticLayer(staticCtx) {
    drawBackground(staticCtx);
    ambientEdges.forEach((e) => { if (e.kind !== 'live-open' && e.kind !== 'feedback') drawEdge(staticCtx, e, 1); });
    allNodes.forEach((node) => {
      if (node.cluster !== 'mkt') return;
      if (migrations.has(node.id)) return; // drawn live at its migrated pos
      const s = screen[node.id];
      if (!s) return;
      if (s.isCand) return; // living layer — drawn per-frame with drift
      drawDot(staticCtx, s.x, s.y, s.r, s.color, s.baseAlpha, 0);
    });
    // Regime/strategy/probe/runner labels — the lifecycle braid routes
    // through these tiny nodes; unlabeled they read as a mystery knot (Jin
    // 2026-07-10). Jarvis callout leader lines (feat/jarvis-language): a
    // short one-bend hairline elbow replaces the flush-under label, with a
    // stable per-id left/right alternation so neighbours don't collide.
    staticCtx.font = '600 8px JetBrains Mono, monospace';
    allNodes.forEach((node) => {
      const s = screen[node.id];
      if (!s) return;
      const side = (hashStr(node.id) & 1) ? 1 : -1;
      // two-row stagger (Jin 2026-07-11 "너무 과밀집"): neighbouring labels
      // alternate between a near and a far callout row instead of piling
      // into one dense text band.
      const drop = (hashStr(node.id) & 2) ? 9 : 0;
      if (node.cluster === 'strat') {
        drawLeaderLabel(staticCtx, s.x, s.y, String(node.label || '').slice(0, 16).toLowerCase(),
          s.color || '#8a94b0', node.state === 'dormant' ? 0.22 : 0.7, side, drop);
        return;
      }
      if (node.cluster !== 'reg' && node.cluster !== 'probe' && node.cluster !== 'orbit') return;
      // orbit(러너/AI 위성) 라벨은 첫 세그먼트만 (session_mult:... → session_mult)
      const lbl = String(node.label || '').replace('regime_', '').split(':')[0].toLowerCase();
      drawLeaderLabel(staticCtx, s.x, s.y, lbl, '#8a94b0', node.state === 'dormant' ? 0.3 : 0.75, side, drop);
    });
  }
  // Per-frame: the live-pulsing edges (open-lifecycle + the gold feedback
  // strand), the micro-pulse pool riding the ambient mesh, graft 1c's
  // per-frame parallax bob on every NAMED (non-dust) node, and glow decay on
  // any node/gate a comet recently touched.
  function drawField(ctx, now, dt) {
    ambientEdges.forEach((e) => {
      if (e.kind === 'live-open') drawEdge(ctx, e, 0.75 + Math.sin(now / 500) * 0.15);
      else if (e.kind === 'feedback') drawEdge(ctx, e, 0.8 + Math.sin(now / 900) * 0.2); // gold strand breathes
    });
    ctx.globalCompositeOperation = 'lighter';
    pulses.forEach((p) => {
      const pt = bezierPoint(p.e, p.t);
      const fade = Math.sin(Math.min(1, p.t) * Math.PI);
      drawDot(ctx, pt.x, pt.y, 1.1, p.e.color.startsWith('#') ? p.e.color : GATE_HALO, 0.32 * fade, 3);
    });
    ctx.globalCompositeOperation = 'source-over';
    stepPulses(dt);
    for (const id of livingIds) {
      if (migrations.has(id)) continue; // traveler — drawn at its migrated pos
      const s = screen[id];
      if (!s) continue;
      const bx = s.x + Math.sin(now * 0.0006 * s.bobSpeed + s.phaseOff) * s.bobAmp;
      const by = s.y + Math.cos(now * 0.00042 * s.bobSpeed + s.phaseOff * 1.3) * s.bobAmp;
      drawDot(ctx, bx, by, s.r, s.color, s.baseAlpha, 0);
    }
    // Strategy slot pips (Jin 2026-07-11 "전략마다 활성화 개수 정해져있어?"
    // made visible): real open_n vs the strategy's own max_positions —
    // filled pip = an occupied concurrent slot. No fabrication: both fields
    // come straight from the registry metadata + live stats.
    allNodes.forEach((n) => {
      if (n.cluster !== 'strat' || !(n.max_open > 0)) return;
      const s = screen[n.id];
      if (!s) return;
      const total = Math.min(6, n.max_open); // 최대 캡 6 = 레지스트리 실최대 (리뷰 LOW)
      const open = Math.min(total, n.open_n || 0);
      const col = venueColorOf(n.exchange) || s.color;
      for (let p = 0; p < total; p++) {
        const px = s.x + (p - (total - 1) / 2) * 5;
        drawDot(ctx, px, s.y - (s.r + 5.5), 1.15, p < open ? col : '#8a94b0', p < open ? 0.9 : 0.2, 0);
      }
    });
    // LIVE interaction wires — brighter than base cloth, fade with age,
    // geometry from the shared bezier cache (distinct 'lw:' keys).
    const nowMs = now;
    liveWires.forEach((w, key) => {
      const age = nowMs - w.born;
      if (age > LIVE_WIRE_TTL) { liveWires.delete(key); return; }
      const a = screen[w.from], b = screen[w.to];
      if (!a || !b) { liveWires.delete(key); return; }
      const e = edgeFor('lw:' + w.from, w.to, a.x, a.y, b.x, b.y, { color: w.color, alpha: 0.34, width: 0.9, bowScale: 0.5 });
      drawEdge(ctx, e, 1 - age / LIVE_WIRE_TTL);
    });
    // Persistent venue-colored breathing glow on firing tickers (element-
    // local halo; additive so it blooms over the baked dust beneath it).
    // Migrating tickers glow at their CURRENT pipeline position instead.
    ctx.globalCompositeOperation = 'lighter';
    // Jarvis target-lock marker (Jin 2026-07-10, feat/jarvis-language):
    // supersedes the flat 3-layer halo — same core-glow dots, PLUS a
    // non-rotating hairline bracket (venue color) that contracts outside->
    // inside over the first 300ms of a firing id's life (markerBorn), then
    // sits as a settled lock bracket. lockAge=null (migrating travelers)
    // always draws settled — they're already mid-journey, not just latched.
    const glowAt = (id, x, y, boost, lockAge) => {
      const s = screen[id];
      if (!s) return;
      const breathe = 0.72 + 0.28 * Math.sin(now / 650 + s.phaseOff);
      const lvl = Math.min(1, ((s.fireLevel || 0.6) + (boost || 0)) * breathe);
      const col = s.venueColor || VENUE_COLOR[String((s.node && s.node.exchange) || '').slice(0, 3).toLowerCase()] || s.color;
      // Halo stays additive (bloom over the dust), but the CORE is stamped
      // opaque in source-over — additive stacking clips high channels and
      // burns every venue hue to white (Jin 2026-07-11 "왜 다 똑같은 색").
      drawDot(ctx, x, y, s.r * 3.2, col, 0.08 * lvl, 0);
      drawDot(ctx, x, y, s.r * 2.0, col, 0.20 * lvl, 0);
      ctx.globalCompositeOperation = 'source-over';
      drawDot(ctx, x, y, Math.max(1.6, s.r * 1.1), col, 0.95, 0);
      ctx.globalCompositeOperation = 'lighter';
      // slimmer bracket (Jin 2026-07-11 "너무 과밀집" — ornament weight down)
      // (orbit moon removed same day — Jin "삥삥 도는거 좀 징그러")
      drawTargetLock(ctx, x, y, Math.max(1.6, s.r * 1.1), col, 0.34 + 0.24 * lvl, lockAge);
    };
    firingIds.forEach((id) => {
      if (migrations.has(id)) return;
      const s = screen[id];
      if (!s) return;
      // glow rides the drifting dot (same wander formula as livingIds draw)
      const bx = s.x + Math.sin(now * 0.0006 * (s.bobSpeed || 0.5) + (s.phaseOff || 0)) * (s.bobAmp || 0);
      const by = s.y + Math.cos(now * 0.00042 * (s.bobSpeed || 0.5) + (s.phaseOff || 0) * 1.3) * (s.bobAmp || 0);
      const born = markerBorn.get(id);
      glowAt(id, bx, by, 0, born == null ? null : now - born);
    });
    // activated SYSTEM nodes light up (Jin 2026-07-11 "액티베이트된 레짐/
    // 엑싯/리플렉터는 색 들어와야"): dominant regime, recently-used exit
    // reasons, busy learners/probes — warm-white luminance lift (steel base
    // hue preserved; green/red/venue hues stay reserved).
    allNodes.forEach((n) => {
      const sysCl = n.cluster === 'reg' || n.cluster === 'exit' || n.cluster === 'exit_tally'
        || n.cluster === 'orbit' || n.cluster === 'probe' || n.cluster === 'action';
      if (!sysCl || (n.state !== 'firing' && n.state !== 'lit')) return;
      const s = screen[n.id];
      if (!s) return;
      const hot = n.state === 'firing';
      const breathe = 0.75 + 0.25 * Math.sin(now / 800 + (s.phaseOff || 0));
      if (hot) drawDot(ctx, s.x, s.y, (s.r || 2.5) * 2.4, '#dfe8ff', 0.14 * breathe, 0);
      drawDot(ctx, s.x, s.y, (s.r || 2.5) * 1.1, '#dfe8ff', (hot ? 0.5 : 0.28) * breathe, hot ? 4 : 0);
    });
    // active strategies glow too (Jin: "활성화 전략은 글로잉") — real state
    // from the roster (open positions / firing), venue-colored, breathing.
    allNodes.forEach((n) => {
      if (n.cluster !== 'strat' || n.state !== 'firing') return;
      const s = screen[n.id];
      if (!s) return;
      const breathe = 0.7 + 0.3 * Math.sin(now / 700 + (s.phaseOff || 0));
      const col = venueColorOf(n.exchange) || s.color;
      drawDot(ctx, s.x, s.y, s.r * 2.6, col, 0.12 * breathe, 0);
      drawDot(ctx, s.x, s.y, s.r * 1.5, col, 0.22 * breathe, 4);
    });
    // Pipeline migration: advance tweens, idle-decay parked dots, draw each
    // traveler at its interpolated position with a slightly boosted glow.
    migrations.forEach((m, id) => {
      const s = screen[id];
      if (!s) { migrations.delete(id); return; }
      if (m.t < 1) m.t = Math.min(1, m.t + dt / m.dur);
      else if (m.phase === 'out') {
        if (m.via) {
          // arrived at the strategy asteroid — dwell there, then descend
          if (!m.dwellUntil) m.dwellUntil = now + MIGRATE_DWELL_MS;
          else if (now >= m.dwellUntil) { m.via = false; m.dwellUntil = 0; maybeAdvance(id, m); }
        } else if (m.stops && m.stops.length) {
          if (m.gateIdx < 0) maybeAdvance(id, m);
          else if (!m.dwellUntil) m.dwellUntil = now + MIGRATE_DWELL_MS;
          else if (now >= m.dwellUntil) maybeAdvance(id, m);
        } else if (now - m.lastMs > MIGRATE_IDLE_MS) { migrateHome(id); return; }
      } else if (m.phase === 'return' && m.t >= 1) { migrations.delete(id); return; }
      const pt = migratePos(m, s);
      glowAt(id, pt.x, pt.y, 0.25, null);
    });
    activeGlowIds.forEach((id) => {
      // already carrying the persistent venue glow — a second additive
      // stamp here is what pushed firing cores past saturation
      if (firingIds.has(id) || migrations.has(id)) { activeGlowIds.delete(id); return; }
      const s = screen[id];
      if (!s) { activeGlowIds.delete(id); return; }
      const fireT = Math.max(0, Math.min(1, (s.fireUntil - now) / 900));
      if (fireT <= 0.01) { activeGlowIds.delete(id); return; }
      const alpha = Math.min(1, s.baseAlpha + fireT * 0.55);
      const r = s.r + fireT * s.r * 0.9;
      drawDot(ctx, s.x, s.y, r * 1.9, s.color, fireT * 0.22, 0);
      drawDot(ctx, s.x, s.y, r, s.color, alpha, 8 + fireT * 10);
    });
    ctx.globalCompositeOperation = 'source-over';
  }

  window.PolarisSpineField = {
    setSize, buildLayout, buildEdges, renderStaticLayer, drawField, refreshNodeState,
    migrateTicker, migrateHome, venueColorOf, touchWire, setProbeLinks,
    screenOf: (id) => screen[id],
    migrationOf: (id) => migrations.get(id),
    markFire, pathEdges, rgba, drawDot, bezierPoint,
    gateScreen: () => gateScreen,
    clusterColor: () => CLUSTER_COLOR,
    findNode: (pred) => allNodes.find(pred),
    nodeById: (id) => nodeById[id],
    nodesOf: (cluster) => allNodes.filter((n) => n.cluster === cluster),
  };
})();
