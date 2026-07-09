/* Polaris FLOW — Visual Wall "Director Mode" (Jin 2026-07-10,
 * feat/visual-wall-director): a cinematic camera autopilot + FX overlay
 * meant to run unattended on a monitor, layered on TOP of the reused globe
 * engine (globe-core/satellites/flows/funnel.js — untouched, self-binding to
 * canvas#sphere). This file owns exactly THREE things:
 *
 *   1. Director camera state machine — cycles wide shot -> dolly into each
 *      exchange galaxy -> wide shot, driving the globe's OWN public camera
 *      API (window.PolarisGlobe.focusExchange/resetView, globe-core.js).
 *      User drag/wheel on the globe pauses the autopilot for 30s.
 *   2. Warp-streak transition + floating damage-number FX — drawn on a
 *      separate inert overlay canvas (#wall-fx, pointer-events:none) so it
 *      never fights the globe's own mouse/wheel/click handlers.
 *   3. Killcam cut + kill feed — a large (|pnl_usd| >= $50) exit event
 *      force-cuts the camera to that venue's galaxy for a 3s hold, and every
 *      exit appends a line to the top-right kill feed (#kill-feed, capped 6).
 *
 * Self-contained: own SSE connection to /stream/events (the same endpoint
 * flow.js and globe-flows.js already each independently subscribe to on
 * this page — one connection per module is the established pattern here,
 * not new coupling). Display-only — nothing here issues, sizes, gates or
 * throttles a trade.
 */
(function () {
  const sphereCanvas = document.getElementById('sphere');
  const fxCanvas = document.getElementById('wall-fx');
  if (!sphereCanvas || !fxCanvas) return;
  const ctx = fxCanvas.getContext('2d');

  let dpr = Math.min(2, window.devicePixelRatio || 1);
  let W = 0, H = 0;
  function fit() {
    W = fxCanvas.clientWidth; H = fxCanvas.clientHeight;
    fxCanvas.width = Math.floor(W * dpr); fxCanvas.height = Math.floor(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', fit);
  fit();

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── Director camera state machine ─────────────────────────────────────
  // 0 = wide shot, 1..3 = dolly into GALAXIES[idx-1].
  const GALAXIES = ['okx', 'capital', 'alpaca'];
  const WIDE_MS = 8000, DOLLY_MS = 6000, PAUSE_MS = 30000, KILLCAM_HOLD_MS = 3000;
  const KILLCAM_PNL_THRESHOLD = 50;

  let stateIdx = 0;
  let stateUntil = 0;
  let userPausedUntil = 0;
  let killcamActive = false;
  let killcamUntil = 0;

  function applyState(idx) {
    if (!window.PolarisGlobe) return;
    if (idx === 0) { if (window.PolarisGlobe.resetView) window.PolarisGlobe.resetView(); }
    else if (window.PolarisGlobe.focusExchange) window.PolarisGlobe.focusExchange(GALAXIES[idx - 1]);
  }

  function nextState(now) {
    stateIdx = (stateIdx + 1) % (GALAXIES.length + 1);
    stateUntil = now + (stateIdx === 0 ? WIDE_MS : DOLLY_MS);
    applyState(stateIdx);
    spawnWarp();
  }

  function tickDirector(now) {
    if (killcamActive) {
      if (now >= killcamUntil) {
        killcamActive = false;
        stateIdx = 0;
        stateUntil = now + WIDE_MS;
        applyState(0);
        spawnWarp();
      }
      return;
    }
    if (now < userPausedUntil) return;   // user drag/wheel — let them look around
    if (now >= stateUntil) nextState(now);
  }

  function pauseAutopilot() { userPausedUntil = performance.now() + PAUSE_MS; }
  sphereCanvas.addEventListener('mousedown', pauseAutopilot);
  sphereCanvas.addEventListener('wheel', pauseAutopilot, { passive: true });

  function venueKeyFromExchange(ex) {
    if (window.PolarisGlobe_venueKey) return window.PolarisGlobe_venueKey(ex) || 'okx';
    const e = (ex || '').toLowerCase().slice(0, 3);
    if (e === 'cap') return 'capital';
    if (e === 'alp') return 'alpaca';
    return 'okx';
  }

  function triggerKillcam(venue) {
    killcamActive = true;
    killcamUntil = performance.now() + KILLCAM_HOLD_MS;
    if (window.PolarisGlobe && window.PolarisGlobe.focusExchange) window.PolarisGlobe.focusExchange(venue);
    spawnWarp();
  }

  // ── Warp-streak transition (radial line streak, 0.6s, own overlay) ─────
  let warp = null;   // {t, dur}
  function spawnWarp() { warp = { t: 0, dur: 0.6 }; }
  function drawWarp(dt) {
    if (!warp) return;
    warp.t += dt / warp.dur;
    if (warp.t >= 1) { warp = null; return; }
    const a = Math.sin(Math.min(1, warp.t) * Math.PI);   // fade in then out
    const cx = W / 2, cy = H / 2;
    const n = 36;
    ctx.save();
    ctx.globalAlpha = a * 0.5;
    ctx.strokeStyle = '#dfe8ff';
    for (let i = 0; i < n; i++) {
      const ang = (i / n) * Math.PI * 2;
      const len = 30 + warp.t * Math.max(W, H) * 0.6;
      const x0 = cx + Math.cos(ang) * 18, y0 = cy + Math.sin(ang) * 18;
      const x1 = cx + Math.cos(ang) * (18 + len), y1 = cy + Math.sin(ang) * (18 + len);
      ctx.lineWidth = 1 + (i % 3 === 0 ? 1 : 0);
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    }
    ctx.restore();
  }

  // ── Floating damage numbers (+$/-$, 1.5s rise+fade over the galaxy) ─────
  const damageNumbers = [];   // {x, y, text, pos, t, dur}
  function galaxyScreenPos(venue) {
    const gs = window.PolarisGlobe_galaxyState && window.PolarisGlobe_galaxyState[venue];
    if (gs && gs._screen) return { x: gs._screen.sx, y: gs._screen.sy };
    return { x: W / 2, y: H / 2 };
  }
  function spawnDamageNumber(venue, pnl) {
    const p = galaxyScreenPos(venue);
    const text = (pnl >= 0 ? '+' : '-') + '$' + Math.abs(pnl).toFixed(0);
    damageNumbers.push({ x: p.x, y: p.y, text, pos: pnl >= 0, t: 0, dur: 1.5 });
  }
  function drawDamageNumbers(dt) {
    for (let k = damageNumbers.length - 1; k >= 0; k--) {
      const d = damageNumbers[k];
      d.t += dt / d.dur;
      if (d.t >= 1) { damageNumbers.splice(k, 1); continue; }
      const y = d.y - d.t * 44;
      ctx.save();
      ctx.globalAlpha = 1 - d.t;
      ctx.fillStyle = d.pos ? '#87ffaf' : '#ff8787';
      ctx.font = '700 15px JetBrains Mono, monospace';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(d.text, d.x, y);
      ctx.restore();
    }
  }

  // ── Kill feed (top-right, newest on top, capped 6) ──────────────────────
  const KILL_FEED_MAX = 6;
  function pushKillFeed(ev) {
    const el = document.getElementById('kill-feed');
    if (!el) return;
    const pnl = parseFloat(ev.pnl_usd) || 0;
    const cls = pnl >= 0 ? 'pos' : 'neg';
    const rTxt = (ev.r_multiple != null)
      ? (ev.r_multiple >= 0 ? '+' : '') + ev.r_multiple.toFixed(2) + 'R'
      : (pnl >= 0 ? '+' : '-') + '$' + Math.abs(pnl).toFixed(0);
    const row = document.createElement('div');
    row.className = 'kf-row ' + cls;
    row.innerHTML = `<span class="kf-strat">${esc(ev.strategy_id || '?')}</span>${esc(ev.ticker || '')} ${esc(rTxt)}`;
    el.insertBefore(row, el.firstChild);
    while (el.children.length > KILL_FEED_MAX) el.removeChild(el.lastChild);
  }

  // ── SSE — exit events drive killcam + damage numbers + kill feed ────────
  function connectStream() {
    let es;
    try { es = new EventSource('/stream/events'); } catch (e) { return; }
    es.onmessage = (ev) => {
      let payload;
      try { payload = JSON.parse(ev.data); } catch (e) { return; }
      for (const e of payload.events || []) {
        if (e.type !== 'exit') continue;
        const pnl = parseFloat(e.pnl_usd) || 0;
        const venue = venueKeyFromExchange(e.exchange);
        pushKillFeed(e);
        spawnDamageNumber(venue, pnl);
        if (Math.abs(pnl) >= KILLCAM_PNL_THRESHOLD) triggerKillcam(venue);
      }
    };
    es.onerror = () => { /* EventSource auto-reconnects */ };
  }

  // ── Draw loop ────────────────────────────────────────────────────────────
  let lastT = performance.now();
  function frame(now) {
    const dt = Math.min(0.1, (now - lastT) / 1000);
    lastT = now;
    tickDirector(now);
    ctx.clearRect(0, 0, W, H);
    drawWarp(dt);
    drawDamageNumbers(dt);
    requestAnimationFrame(frame);
  }

  // ── Boot ────────────────────────────────────────────────────────────────
  stateUntil = performance.now() + WIDE_MS;
  connectStream();
  requestAnimationFrame((t) => { lastT = t; requestAnimationFrame(frame); });
})();
