/*
 * Weekend tab — weekend operations board (DEMO/PAPER, display-only).
 *
 * READ-ONLY window onto the weekend shadow-first programme. Four stacked
 * sections, Bloomberg-dense (tables / one-line pathways, no cards):
 *   ① WEEK SETTLEMENT   — last-7d real-fee-net P&L per (strategy × venue) +
 *                         the single best / worst trade.
 *   ② SHADOW INCUBATOR  — weekend_shadow_orders would-be P&L (resolved offline
 *                         from stored bars, +1R/−1R rails) + a GATED live-promotion
 *                         gauge + the honest maker_fill_shadow persist status.
 *   ③ MONDAY READINESS  — next regional cash-session opens (countdown) + the
 *                         shadow-symbol bar freshness.
 *   ④ HOUSEKEEPING QUEUE— the funding-promotion gate checklist + recent research.
 *
 * Wiring mirrors chart.js to keep the shared-file edit surface minimal: this
 * module INJECTS its own `#pane-weekend` into `#board` + REGISTERS its renderer
 * on `window.PolarisBoardTabs`. The only edits elsewhere are the one TABS entry
 * in board.js and the one <script> include in index.html. Nothing here reads or
 * writes sizing/risk/orders — it fetches the read-only `/api/weekend` endpoint.
 * The fetch refreshes on tab-render (slow data: a 60s server cache backs it).
 */
(function () {
  'use strict';
  const B = window.PolarisBoard;
  const T = window.PolarisBoardTabs;
  if (!B || !T) return;            // board core absent → no-op (graceful)
  const esc = B.esc;
  const fmtUsd = B.fmtUsd;
  const pn = B.pn;                 // value → 'b-pos' / 'b-neg' / 'b-flat' class
  const hms = B.hms;               // seconds → "1h 02m" style duration

  // ── pane skeleton (self-injected once) ────────────────────────────────────
  const PANE_HTML = `
    <div class="panel wk-panel">
      <div class="p-head">
        <span>Weekend Ops</span>
        <span class="wk-badge" id="wk-badge"></span>
        <span class="wk-status" id="wk-status"></span>
      </div>
      <div class="p-body wk-body">
        <section class="wk-sec" id="wk-settlement"></section>
        <section class="wk-sec" id="wk-shadow"></section>
        <section class="wk-sec" id="wk-readiness"></section>
        <section class="wk-sec" id="wk-housekeeping"></section>
      </div>
    </div>`;

  const CSS = `
    #pane-weekend .wk-panel { grid-row: 1 / -1; display: flex; flex-direction: column; min-height: 0; }
    #pane-weekend .wk-badge { font: 10px ui-monospace, monospace; padding: 1px 6px;
      border-radius: 3px; background: #2a2f3a; color: #8b909c; margin-left: 8px; }
    #pane-weekend .wk-badge.live { background: #1f3a2a; color: #4ade80; }
    #pane-weekend .wk-status { margin-left: auto; font: 10px ui-monospace, monospace; color: #6b7280; }
    #pane-weekend .wk-body { overflow: auto; min-height: 0; display: flex; flex-direction: column; gap: 14px;
      padding: 4px 2px 12px; }
    #pane-weekend .wk-sec h4 { margin: 0 0 4px; font: 600 11px ui-monospace, monospace;
      color: #cfd3dc; letter-spacing: 0.04em; border-bottom: 1px solid #1b1f27; padding-bottom: 3px; }
    #pane-weekend .wk-sub { font: 10px ui-monospace, monospace; color: #8b909c; margin: 2px 0 6px; }
    #pane-weekend table.wk-tbl { width: 100%; border-collapse: collapse; font: 11px ui-monospace, monospace; }
    #pane-weekend table.wk-tbl th { text-align: right; color: #6b7280; font-weight: 500;
      padding: 1px 8px 3px; border-bottom: 1px solid #1b1f27; white-space: nowrap; }
    #pane-weekend table.wk-tbl th.l, #pane-weekend table.wk-tbl td.l { text-align: left; }
    #pane-weekend table.wk-tbl td { text-align: right; padding: 1px 8px; color: #cfd3dc; white-space: nowrap; }
    #pane-weekend table.wk-tbl tr:hover td { background: #0e1014; }
    #pane-weekend .b-pos { color: #4ade80; }
    #pane-weekend .b-neg { color: #ef5350; }
    #pane-weekend .b-flat { color: #8b909c; }
    #pane-weekend .wk-line { font: 11px ui-monospace, monospace; color: #cfd3dc; margin: 3px 0; }
    #pane-weekend .wk-line b { color: #f0b429; }
    #pane-weekend .wk-chk { font: 11px ui-monospace, monospace; margin: 2px 0; }
    #pane-weekend .wk-chk .ok { color: #4ade80; }
    #pane-weekend .wk-chk .no { color: #ef5350; }
    #pane-weekend .wk-chk .pend { color: #6b7280; }
    #pane-weekend .wk-empty { font: 10px ui-monospace, monospace; color: #5a6068; padding: 4px 0; }
    #pane-weekend .wk-note { font: 11px ui-monospace, monospace; color: #cfd3dc; margin: 2px 0; }
    #pane-weekend .wk-note .nm { color: #00b4d8; }`;

  function injectStyle() {
    if (document.getElementById('weekend-tab-style')) return;
    const s = document.createElement('style');
    s.id = 'weekend-tab-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  let _paneReady = false;
  function ensurePane() {
    if (_paneReady) return true;
    const board = document.getElementById('board');
    const tabs = document.getElementById('b-tabs');
    if (!board || !tabs) return false;          // skeleton not mounted yet
    if (document.getElementById('pane-weekend')) { _paneReady = true; return true; }
    injectStyle();
    const wrap = document.createElement('div');
    wrap.className = 'tab-pane';
    wrap.id = 'pane-weekend';
    wrap.innerHTML = PANE_HTML;
    board.appendChild(wrap);
    _paneReady = true;
    return true;
  }

  // ── small render helpers ──────────────────────────────────────────────────
  function setStatus(txt) {
    const el = document.getElementById('wk-status');
    if (el) el.textContent = txt;
  }
  function setHTML(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }
  // signed R, e.g. +1.00R / −0.15R. Plain text (caller wraps with a pn class).
  function fmtR(v) {
    const n = Number(v) || 0;
    return (n >= 0 ? '+' : '') + n.toFixed(2) + 'R';
  }
  function pct(v) { return (Number(v) || 0).toFixed(0) + '%'; }

  // ── ① WEEK SETTLEMENT ─────────────────────────────────────────────────────
  function renderSettlement(s) {
    const rows = (s && s.rows) || [];
    const head = '<h4>① WEEK SETTLEMENT — real-fee-net, last 7d</h4>';
    if (!rows.length) {
      setHTML('wk-settlement', head + '<div class="wk-empty">no closed trades in the last 7 days</div>');
      return;
    }
    let body = '<table class="wk-tbl"><thead><tr>'
      + '<th class="l">strategy</th><th class="l">venue</th><th>n</th>'
      + '<th>win%</th><th>net$</th></tr></thead><tbody>';
    rows.forEach(r => {
      body += '<tr>'
        + `<td class="l">${esc(r.strategy_id || '—')}</td>`
        + `<td class="l">${esc(r.venue || '—')}</td>`
        + `<td>${r.n || 0}</td>`
        + `<td>${pct(r.win_pct)}</td>`
        + `<td class="${pn(r.net_usd)}">${fmtUsd(r.net_usd, 0)}</td>`
        + '</tr>';
    });
    body += '</tbody></table>';
    const total = s.total_net_usd || 0;
    let summary = `<div class="wk-line">total net <span class="${pn(total)}">${fmtUsd(total, 0)}</span></div>`;
    if (s.best) {
      summary += `<div class="wk-line">top winner <b>${esc(s.best.symbol)}</b> `
        + `<span class="b-pos">${fmtUsd(s.best.net_usd, 0)}</span> `
        + `<span class="b-flat">(${esc(s.best.strategy_id)})</span></div>`;
    }
    if (s.worst && (!s.best || s.worst.net_usd !== s.best.net_usd)) {
      summary += `<div class="wk-line">top leak <b>${esc(s.worst.symbol)}</b> `
        + `<span class="b-neg">${fmtUsd(s.worst.net_usd, 0)}</span> `
        + `<span class="b-flat">(${esc(s.worst.strategy_id)})</span></div>`;
    }
    setHTML('wk-settlement', head + body + summary);
  }

  // ── ② SHADOW INCUBATOR ────────────────────────────────────────────────────
  function renderShadow(sh) {
    const strats = (sh && sh.strategies) || [];
    const head = '<h4>② SHADOW INCUBATOR — would-be P&L (offline, +1R/−1R rails)</h4>'
      + '<div class="wk-sub">shadow-first OKX makers · forward-resolved from stored bars, '
      + 'SAME ATR-R basis the sweep uses · no capital at risk</div>';
    if (!strats.length) {
      setHTML('wk-shadow', head + '<div class="wk-empty">no weekend shadow orders recorded yet</div>');
      return;
    }
    let body = '<table class="wk-tbl"><thead><tr>'
      + '<th class="l">strategy</th><th>n</th><th>tgt</th><th>stop</th><th>open</th>'
      + '<th>hit%</th><th>med R</th><th>mean R</th></tr></thead><tbody>';
    strats.forEach(r => {
      body += '<tr>'
        + `<td class="l">${esc(r.strategy_id || '—')}</td>`
        + `<td>${r.n || 0}</td>`
        + `<td class="b-pos">${r.target || 0}</td>`
        + `<td class="b-neg">${r.stop || 0}</td>`
        + `<td class="b-flat">${r.open || 0}</td>`
        + `<td>${pct(r.win_pct)}</td>`
        + `<td class="${pn(r.median_r)}">${fmtR(r.median_r)}</td>`
        + `<td class="${pn(r.mean_r)}">${fmtR(r.mean_r)}</td>`
        + '</tr>';
    });
    body += '</tbody></table>';
    // Maker-fill-shadow honest status (0 rows = persist not yet wired).
    const mk = sh.maker_fill_shadow_n || 0;
    const mkLine = sh.maker_persist_wired
      ? `<div class="wk-line">maker-fill-shadow <b>${mk}</b> real-fee rows accruing</div>`
      : '<div class="wk-line">maker-fill-shadow <span class="b-flat">0 rows — persist not wired (FIX-EXEC pending)</span></div>';
    // GATED live-promotion gauge — funding edge corroborated OOS (+0.222R 3×ATR
    // mean / +0.722 med / win 70%, [[weekend_maker_honest_rerun_2026-06-28]]),
    // promotion stays GATED on the three live criteria below.
    const gauge = '<div class="wk-line">funding edge: OOS <b>+0.222R</b> mean (3×ATR) · '
      + 'live promotion <span class="b-neg">GATED</span></div>';
    setHTML('wk-shadow', head + body + mkLine + gauge);
  }

  // ── ③ MONDAY READINESS ────────────────────────────────────────────────────
  function renderReadiness(rd) {
    const opens = (rd && rd.session_opens) || [];
    const fresh = (rd && rd.bar_freshness) || [];
    const head = '<h4>③ MONDAY READINESS — next cash-session opens · OKX trades 24/7</h4>';
    let body = '';
    if (opens.length) {
      body += '<table class="wk-tbl"><thead><tr>'
        + '<th class="l">region</th><th>opens in</th></tr></thead><tbody>';
      opens.forEach(o => {
        body += '<tr>'
          + `<td class="l">${esc((o.region || '').toUpperCase())}</td>`
          + `<td>${hms(o.seconds_until || 0)}</td>`
          + '</tr>';
      });
      body += '</tbody></table>';
    } else {
      body += '<div class="wk-empty">no upcoming session opens computed</div>';
    }
    if (fresh.length) {
      body += '<div class="wk-sub" style="margin-top:8px">shadow-symbol bar freshness</div>';
      body += '<table class="wk-tbl"><thead><tr>'
        + '<th class="l">venue</th><th class="l">symbol</th><th>last bar age</th></tr></thead><tbody>';
      fresh.forEach(f => {
        const stale = (f.age_sec || 0) > 600;   // >10m → grey-flag
        body += '<tr>'
          + `<td class="l">${esc(f.venue || '—')}</td>`
          + `<td class="l">${esc(f.symbol || '—')}</td>`
          + `<td class="${stale ? 'b-flat' : 'b-pos'}">${hms(f.age_sec || 0)}</td>`
          + '</tr>';
      });
      body += '</tbody></table>';
    }
    setHTML('wk-readiness', head + body);
  }

  // ── ④ HOUSEKEEPING QUEUE ──────────────────────────────────────────────────
  function renderHousekeeping(d) {
    const sh = d.shadow || {};
    const notes = d.notes || [];
    const head = '<h4>④ HOUSEKEEPING QUEUE — funding promotion gate + research</h4>';
    // The funding-promotion gate is a fixed 3-item checklist; live state is
    // derived from the shadow section (maker persist wired? orders accruing?).
    const persistOk = !!sh.maker_persist_wired;
    const ordersOk = (sh.order_n || 0) > 0;
    const chk = [
      ['live maker-fill-shadow real accrual', persistOk],
      ['ruler reconcile (offline R ↔ live R)', false],
      ['/debate promotion review', false],
    ];
    let body = '';
    chk.forEach(([label, done]) => {
      const mark = done ? '<span class="ok">[x]</span>' : '<span class="pend">[ ]</span>';
      body += `<div class="wk-chk">${mark} ${esc(label)}</div>`;
    });
    body += `<div class="wk-line" style="margin-top:4px">shadow orders accruing: `
      + `<span class="${ordersOk ? 'b-pos' : 'b-flat'}">${sh.order_n || 0}</span></div>`;
    if (notes.length) {
      body += '<div class="wk-sub" style="margin-top:8px">recent weekend research</div>';
      notes.forEach(n => {
        const take = n.takeaway ? ' — ' + esc(n.takeaway) : '';
        body += `<div class="wk-note"><span class="nm">${esc(n.title || n.name)}</span>${take}</div>`;
      });
    } else {
      body += '<div class="wk-empty" style="margin-top:8px">no weekend research notes found</div>';
    }
    setHTML('wk-housekeeping', head + body);
  }

  // ── badge + section dispatch ──────────────────────────────────────────────
  function renderBadge(d) {
    const el = document.getElementById('wk-badge');
    if (!el) return;
    if (d.is_weekend) {
      el.textContent = '● WEEKEND';
      el.classList.add('live');
    } else {
      el.textContent = 'weekday';
      el.classList.remove('live');
    }
  }

  function render(d) {
    if (!d || typeof d !== 'object') { setStatus('no data'); return; }
    // Each section is isolated: a throw in one must NOT blank the rest.
    try { renderBadge(d); } catch (e) { /* badge optional */ }
    try { renderSettlement(d.settlement || {}); } catch (e) { setHTML('wk-settlement', ''); }
    try { renderShadow(d.shadow || {}); } catch (e) { setHTML('wk-shadow', ''); }
    try { renderReadiness(d.readiness || {}); } catch (e) { setHTML('wk-readiness', ''); }
    try { renderHousekeeping(d); } catch (e) { setHTML('wk-housekeeping', ''); }
    setStatus('updated ' + new Date().toLocaleTimeString());
  }

  // ── fetch (own user-driven cadence; 60s server cache backs it) ─────────────
  let _inflight = false;
  function load() {
    if (_inflight) return;
    _inflight = true;
    setStatus('loading…');
    fetch('/api/weekend?t=' + Date.now(), { cache: 'no-store' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('http ' + r.status)))
      .then(d => { _inflight = false; render(d); })
      .catch(() => { _inflight = false; setStatus('fetch error'); });
  }

  // Renderer is called by board.js on poll/switch. We ignore the snapshot `d`
  // (the weekend board has its own slow endpoint) and just ensure the pane is
  // mounted + (re)load. A short throttle avoids a fetch on every 1s poll while
  // the tab is active (the data only moves on the 60s server cadence).
  let _lastLoad = 0;
  function renderWeekendTab() {
    if (!ensurePane()) return;
    const now = Date.now();
    if (now - _lastLoad > 15000) { _lastLoad = now; load(); }
  }

  T.register('weekend', renderWeekendTab);

  // Inject the pane as soon as the skeleton exists so a tab-switch finds it even
  // before the first render call. Retry briefly until board.js has mounted.
  function tryMount(attempt) {
    if (ensurePane() || attempt > 40) return;
    setTimeout(() => tryMount(attempt + 1), 50);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => tryMount(0));
  } else {
    tryMount(0);
  }
})();
