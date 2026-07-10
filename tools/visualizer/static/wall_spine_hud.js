/* Polaris FLOW — "Synaptic Spine" data wiring + HUD (Jin 2026-07-10, feat/
 * jarvis-wall). The ONLY module on this page that does network I/O — polls
 * /static/graph.json (1s) for the node roster + real lifecycle/kill/equity
 * data, polls /api/flow_stats (1s) for gate counts/drops/verdicts/summary/
 * pts-classes, and subscribes the shared /stream/events SSE bus
 * (events_bus.js) for real-time entry/exit fills + gate decisions. Every
 * bright comet on the canvas traces back to one of these three real sources
 * — nothing here is simulated (the offline concept_spine.html prototype's
 * SSE simulator is NOT ported).
 *
 * Drives window.PolarisSpine (wall_spine.js/wall_spine_field.js — the canvas
 * engine) and the DOM HUD (topbar summary strip, kill-feed, drop-panel, bot
 * log, equity ticker). The score_F gauge strip / EARN·BENCH badges / NEW
 * CANDIDATE flash are unchanged, still owned by flow_classes.js — this file
 * just feeds it the same /api/flow_stats payload it already expects.
 *
 * Display-only. Nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  const spine = window.PolarisSpine;
  if (!spine) return;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function fmtUsd(v) {
    if (v == null || isNaN(v)) return '–';
    return (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  function fmtPct(v) { return (v == null ? '–' : v.toFixed(1) + '%'); }

  /* ===== graph.json roster (1s poll) — node states + real lifecycle data ===== */
  let lastFingerprint = '';
  let killRows = [];
  function fingerprintOf(data) {
    const st = data.stats || {};
    // wiring hash (Jin 2026-07-10 "배선 실시간"): lifecycle strat->pos pairs,
    // watch set, probe links — ANY rewiring rebuilds edges within one poll.
    let h = 2166136261;
    const eat = (s) => { for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } };
    (data.lifecycle_paths || []).forEach((p) => eat(p.kind + (p.node_ids || []).join('>')));
    (data.nodes || []).forEach((n) => { if (n.cluster === 'watch') eat(n.id); });
    (data.probe_links || []).forEach((l) => eat(l.probe + '>' + l.pos));
    return st.node_count + ':' + st.open_count + ':' + st.cluster_count + ':' + (h >>> 0);
  }
  function renderKillFeed() {
    const el = document.getElementById('kill-feed');
    if (!el) return;
    el.innerHTML = killRows.slice(0, 6).map((k) => {
      const ageMin = Math.max(0, Math.round((Date.now() / 1000 - k.ts) / 60));
      return `<div class="kf-row neg"><span class="kf-g">g${k.gate_id}</span>${esc(k.ticker)} · killed · ${ageMin}m ago</div>`;
    }).join('');
  }
  function addKill(k) { killRows.unshift(k); killRows = killRows.slice(0, 6); renderKillFeed(); }

  function renderUniverseStat(data) {
    const el = document.getElementById('s-universe');
    if (!el) return;
    const mkt = (data.nodes || []).filter((n) => n.cluster === 'mkt');
    const firing = mkt.filter((n) => n.state === 'firing').length;
    el.textContent = firing + '/' + mkt.length;
  }
  function renderEquity(data) {
    const c = data.polaris_core;
    if (!c) return;
    // VIRTUAL ACCOUNT mode (Jin 2026-07-07 mandate, matches board.js:852 /
    // mobile.js:145): the ticker Jin reads at a glance is the fresh $100k x N
    // virtual ledger's TODAY figure (AEST-midnight-floored), not inception-to-
    // date P&L against a fixed starting_capital seed, and not the LEGACY
    // real-venue equity_now reconciliation — both of which read as ~2 months
    // of stale cumulative history under a 'TODAY' label once virtual mode is
    // on. Falls back to the legacy pair when virtual mode is off.
    const virt = !!c.virtual_account_enabled;
    const equity = virt ? c.virtual_equity_usd : c.equity_now;
    const today = virt ? c.virtual_daily_pnl_usd : c.equity_now - c.starting_capital;
    const eqEl = document.getElementById('bl-equity');
    const todayEl = document.getElementById('bl-today');
    if (eqEl) eqEl.textContent = fmtUsd(equity);
    if (todayEl) {
      todayEl.textContent = fmtUsd(today);
      todayEl.classList.toggle('pos', today >= 0);
      todayEl.classList.toggle('neg', today < 0);
    }
    // "equity pulse" vitality indicator (small HUD addition, radial-inspired)
    // — CSS-only breathing, no new per-frame JS: toggled by the same P&L sign
    // the number itself already carries.
    if (eqEl) eqEl.classList.toggle('pulse-pos', today >= 0);
    if (eqEl) eqEl.classList.toggle('pulse-neg', today < 0);
  }

  async function pollRoster() {
    try {
      const r = await fetch('/static/graph.json?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const data = await r.json();
      const fp = fingerprintOf(data);
      if (fp !== lastFingerprint) {
        lastFingerprint = fp;
        spine.boot(data);
        window.PolarisFlowClasses && window.PolarisFlowClasses.setColX([spine.firstGateX()]);
      } else {
        spine.refresh(data.nodes || []);
      }
      if (!killRows.length) killRows = (data.kills || []).slice().sort((a, b) => b.ts - a.ts);
      renderKillFeed();
      renderUniverseStat(data);
      renderEquity(data);
      // console v2 (Jin 2026-07-11): pushed every poll regardless of the
      // structural fingerprint match above — these numbers change every 1s
      // even when the node/edge SET doesn't. `polaris_core.virtual_daily_
      // pnl_usd` is the SAME source renderEquity() above already reads for
      // the botlog TODAY figure; `stats.firing_rate` is graph.json's
      // existing top-level scalar — both merged in here rather than
      // duplicated server-side in polaris_graph.py's _console_block.
      if (spine.setConsole) {
        const pc = data.polaris_core || {};
        spine.setConsole(data.console
          ? Object.assign({}, data.console, {
            firing_rate: (data.stats && data.stats.firing_rate) || 0,
            day_pnl: pc.virtual_daily_pnl_usd || 0,
          })
          : null);
      }
    } catch (e) { /* display-only — keep last frame */ }
  }

  /* ===== flow_stats (1s poll) — gate counts, drops, verdicts, summary ===== */
  let lastVerdictTs = 0;
  function renderSummary(d) {
    const s = d.summary || {};
    const set = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
    set('s-sig2sized', fmtPct(s.signal_to_sized_pct));
    set('s-sized2fill', fmtPct(s.sized_to_fill_pct));
    set('s-ai', String(s.ai_calls_n != null ? s.ai_calls_n : '–'));
    set('s-drops', String(s.drops_total != null ? s.drops_total : '–'));
    set('s-shadow', String(d.shadow_mismatch_n != null ? d.shadow_mismatch_n : '–'));
  }
  function renderDrops(d) {
    const totEl = document.getElementById('drop-total');
    const rowsEl = document.getElementById('drop-rows');
    const winEl = document.getElementById('drop-window');
    const sec = d.window_sec || 600;
    const winLabel = sec % 3600 === 0 ? (sec / 3600) + 'h' : Math.round(sec / 60) + 'm';
    if (winEl) winEl.textContent = 'DROP LANE (' + winLabel + ')';
    if (totEl) totEl.textContent = String((d.summary && d.summary.drops_total) || 0);
    if (!rowsEl) return;
    const drops = (d.drops || []).slice(0, 4);
    rowsEl.innerHTML = drops.length
      ? drops.map((r) => `<div class="row"><span>${esc(r.label)} · ${esc(r.reason || '—')}</span><span class="n">${r.n}</span></div>`).join('')
      : '<div class="empty">no drops in window</div>';
  }

  async function pollStats() {
    try {
      const r = await fetch('/api/flow_stats?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      spine.setGateCounts(d.stages || []);
      if (spine.setStrategyGauges) spine.setStrategyGauges(d.classes || []);
      if (spine.setVerdicts) spine.setVerdicts(d.verdicts_recent || []);
      if (spine.setFlowSummary) spine.setFlowSummary(d.summary || {});
      renderSummary(d);
      renderDrops(d);
      for (const v of d.verdicts_recent || []) {
        if (v.ts > lastVerdictTs) spine.fireVerdict(v);
      }
      if ((d.verdicts_recent || []).length) lastVerdictTs = Math.max(lastVerdictTs, ...d.verdicts_recent.map((v) => v.ts));
      if (window.PolarisFlowClasses) window.PolarisFlowClasses.onStatsPoll(d);
    } catch (e) { /* display-only — keep last frame */ }
  }

  /* ===== bot log (1s poll /api/botlog) + equity already above ===== */
  function classifyLog(line) {
    const l = line.toLowerCase();
    if (/\b(error|critical|fatal)\b/.test(l) || l.includes('exception') || l.includes('traceback')) return 'bl-err';
    if (/\b(warn|warning)\b/.test(l) || l.includes('rejected') || l.includes('retry')) return 'bl-warn';
    if (l.includes('order resp ok=true') || /\bopen(ed)?\b/.test(l) || /\bclos(e|ed)\b/.test(l) || /\bfill(ed)?\b/.test(l)) return 'bl-hi';
    return '';
  }
  function fmtLogLine(line) {
    const m = line.match(/^(\S+T\S+Z?)\s+(.*)$/);
    const cls = classifyLog(line);
    return m
      ? `<div class="bl-line ${cls}"><span class="ts">${esc(m[1])}</span> ${esc(m[2])}</div>`
      : `<div class="bl-line ${cls}">${esc(line)}</div>`;
  }
  const LOG_TAIL_N = 6;
  async function pollLog() {
    const body = document.getElementById('botlog-body');
    if (!body) return;
    try {
      const r = await fetch('/api/botlog?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const lines = ((await r.json()).lines || []).slice(-LOG_TAIL_N);
      if (!lines.length) return;
      body.innerHTML = lines.map(fmtLogLine).join('');
      body.scrollTop = body.scrollHeight;
    } catch (e) { /* keep last frame */ }
  }

  /* ===== SSE — real entry/exit fills + gate decisions (shared bus) ===== */
  function handleStream(payload) {
    for (const e of payload.events || []) {
      if (e.type === 'entry') spine.fireEntry(e);
      else if (e.type === 'exit') spine.fireExit(e);
    }
    for (const g of payload.gate_events || []) {
      spine.fireGateEvent(g);
      if (spine.markGatePulse) spine.markGatePulse(g.gate_id);
      if (g.decision === 'KILL') {
        spine.fireKill(g);
        addKill({ ticker: g.symbol, gate_id: g.gate_id, ts: g.ts || Math.floor(Date.now() / 1000) });
      }
    }
  }

  /* ===== boot ===== */
  pollRoster();
  setInterval(pollRoster, 1000);
  pollStats();
  setInterval(pollStats, 1000);
  pollLog();
  setInterval(pollLog, 1000);
  if (window.PolarisEvents) window.PolarisEvents.on(handleStream);
})();
