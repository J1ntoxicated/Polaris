/* Polaris FLOW — Cloud River FX layer (Jin 2026-07-10, feat/cloud-river).
 *
 * Pure effects library for cloud.js's single canvas/rAF loop — split out to
 * keep cloud.js under the project's 500-LOC cap (same flow.js/flow_classes.js
 * split precedent). Owns NO canvas, NO poll/SSE of its own: cloud.js calls
 * spawn*()/tick(dt)/draw(ctx,W,H) once per frame from its own loop, so the
 * whole page still runs at most 2 rAF loops total (flow.js's river + this
 * one, folded into cloud.js).
 *
 * Concurrency: "large" effects (loot beam / sparkle burst / shatter) are
 * capped at MAX_LARGE concurrent — a 4th spawn queues instead of piling on
 * screen, and a duplicate spawn for the same (type, key) merges into the
 * existing/queued one rather than stacking. "small" effects (radar ping /
 * hit flash / damage number / comet tail) are cheap and uncapped. This is
 * the "동시 대형 이펙트 ≤3, 버스트 병합 큐" hard rule from the design spec.
 *
 * NO full-screen effects: every fx here is anchored to a dot's (x,y) —
 * there is no vignette/flash/warp that paints the whole canvas. The old
 * wall.js killcam vignette is abolished per spec (killcam replaced by the
 * local red shatter burst + kill-feed line, both already dot-anchored).
 *
 * Display-only — draws pixels, reads/writes nothing that gates/sizes/routes
 * a trade.
 */
(function () {
  function rgba(c, a) { return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const MAX_LARGE = 3;
  const large = [];   // active large fx: {type, key, x, y, t, dur, extra}
  const queue = [];   // pending large fx, same shape, drained as slots free
  const small = [];   // radar ping / hit flash / damage number / comet tail

  function isQueuedOrActive(type, key) {
    return large.some((f) => f.type === type && f.key === key)
      || queue.some((f) => f.type === type && f.key === key);
  }

  function activate(fx) {
    fx.t = 0;
    fx.dur = fx.type === 'loot' ? 1.5 : 0.9;
    large.push(fx);
  }
  function drainQueue() {
    while (large.length < MAX_LARGE && queue.length) activate(queue.shift());
  }
  function spawnLarge(type, key, x, y, extra) {
    if (isQueuedOrActive(type, key)) return; // burst merge — no duplicate pile-up
    const fx = { type, key, x, y, extra };
    if (large.length >= MAX_LARGE) { queue.push(fx); return; }
    activate(fx);
  }

  function spawnRadarPing(x, y, color) { small.push({ type: 'ping', x, y, color, t: 0, dur: 0.9 }); }
  function spawnHitFlash(x, y) { small.push({ type: 'flash', x, y, t: 0, dur: 0.06 }); }
  function spawnDamageNumber(x, y, pnl) {
    const text = (pnl >= 0 ? '+' : '-') + '$' + Math.abs(pnl).toFixed(0);
    small.push({ type: 'dmg', x, y, text, pos: pnl >= 0, t: 0, dur: 1.5 });
  }
  function spawnCometTail(x0, y0, x1, y1) { small.push({ type: 'comet', x0, y0, x1, y1, t: 0, dur: 1.1 }); }
  function spawnLootBeam(key, x, y) { spawnLarge('loot', key, x, y); }
  function spawnSparkleBurst(key, x, y) { spawnLarge('sparkle', key, x, y, { color: [0x87, 0xff, 0xaf] }); }
  function spawnShatter(key, x, y) { spawnLarge('shatter', key, x, y, { color: [0xff, 0x87, 0x87] }); }

  // ── Kill feed (top-right DOM panel, newest on top, capped 6 — same
  // element/markup wall.js used to own before it folded into cloud.js). ────
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

  function tick(dt) {
    for (let k = large.length - 1; k >= 0; k--) {
      const f = large[k];
      f.t += dt / f.dur;
      if (f.t >= 1) { large.splice(k, 1); drainQueue(); }
    }
    for (let k = small.length - 1; k >= 0; k--) {
      const f = small[k];
      f.t += dt / f.dur;
      if (f.t >= 1) small.splice(k, 1);
    }
  }

  function draw(ctx, W, H) {
    void W; void H; // no full-screen fx (killcam abolished — Jin 2026-07-10 explicit)
    for (const f of large) drawLarge(ctx, f);
    for (const f of small) drawSmall(ctx, f);
  }

  function drawLarge(ctx, f) {
    if (f.type === 'loot') drawLoot(ctx, f);
    else drawBurst(ctx, f, f.extra.color, f.type === 'sparkle');
  }
  // Diablo-style vertical loot beam — survivor-admission / PROVE promotion.
  function drawLoot(ctx, f) {
    const a = Math.sin(Math.min(1, f.t) * Math.PI);
    ctx.save();
    const grad = ctx.createLinearGradient(f.x, f.y - 90, f.x, f.y + 6);
    grad.addColorStop(0, 'rgba(255,236,150,0)');
    grad.addColorStop(0.55, `rgba(255,236,150,${0.55 * a})`);
    grad.addColorStop(1, 'rgba(255,236,150,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(f.x - 5, f.y - 90, 10, 96);
    ctx.restore();
  }
  // Win = outward sparkle burst, loss = inward-collapsing shatter — same
  // color language as the damage numbers (green profit / red loss).
  function drawBurst(ctx, f, color, outward) {
    const n = 10;
    const a = 1 - f.t;
    for (let i = 0; i < n; i++) {
      const ang = (i / n) * 6.2832 + f.t * 1.5;
      const dist = (outward ? f.t : (1 - f.t)) * 22 + 4;
      const x = f.x + Math.cos(ang) * dist, y = f.y + Math.sin(ang) * dist;
      ctx.fillStyle = rgba(color, a * 0.85);
      ctx.beginPath(); ctx.arc(x, y, outward ? 1.6 : 1.2, 0, 6.2832); ctx.fill();
    }
  }
  function drawSmall(ctx, f) {
    if (f.type === 'ping') {
      const r = 6 + f.t * 26, a = (1 - f.t) * 0.6;
      ctx.strokeStyle = rgba(f.color, a); ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.arc(f.x, f.y, r, 0, 6.2832); ctx.stroke();
    } else if (f.type === 'flash') {
      ctx.fillStyle = `rgba(255,255,255,${(1 - f.t) * 0.9})`;
      ctx.beginPath(); ctx.arc(f.x, f.y, 5, 0, 6.2832); ctx.fill();
    } else if (f.type === 'dmg') {
      const y = f.y - f.t * 40;
      ctx.save();
      ctx.globalAlpha = 1 - f.t;
      ctx.fillStyle = f.pos ? '#87ffaf' : '#ff8787';
      ctx.font = '700 13px JetBrains Mono, monospace';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(f.text, f.x, y);
      ctx.restore();
    } else if (f.type === 'comet') {
      drawComet(ctx, f);
    }
  }
  // G8 Reflector -> G1 re-spawn: a short comet-tail arc (gentle sine hump),
  // the "learning loop" visual per the design spec.
  function drawComet(ctx, f) {
    const tailN = 5;
    for (let i = 0; i < tailN; i++) {
      const tt = Math.max(0, f.t - i * 0.05);
      const tx = f.x0 + (f.x1 - f.x0) * tt;
      const ty = f.y0 + (f.y1 - f.y0) * tt - Math.sin(tt * Math.PI) * 18;
      ctx.fillStyle = `rgba(200,220,255,${(1 - i / tailN) * (1 - f.t) * 0.6})`;
      ctx.beginPath(); ctx.arc(tx, ty, 2.4 - i * 0.3, 0, 6.2832); ctx.fill();
    }
  }
  window.PolarisCloudFx = {
    spawnRadarPing, spawnHitFlash, spawnDamageNumber, spawnCometTail,
    spawnLootBeam, spawnSparkleBurst, spawnShatter,
    pushKillFeed, tick, draw,
  };
})();
