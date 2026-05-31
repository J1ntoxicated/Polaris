/* board.js — Polaris right-half analytics board core + left-column bot log
 * (DEMO/PAPER display-only). Native HTML/CSS. 1s polling: /api/snapshot →
 * #board, /api/botlog → #botlog-body. All fetches cache-busted (?t=Date.now())
 * + no-store. No ANSI mirror. No sizing/order logic.
 *
 * E2 Trading-IA rebuild (Jin 2026-05-31): the right pane is HEADER (KPIs) +
 * 3-STREAM EXCHANGE SUMMARY (always visible) + a FULL-WIDTH TAB STRIP below
 * (POSITIONS / TRADES / REGIME / STRATEGY / EXIT / AI / EDGE / RISK). NO
 * side-by-side positions/trades. The per-tab renderers + their CSS live in
 * board_tabs.js (loaded after this file); shared helpers + the active-tab
 * gate are exposed on ``window.PolarisBoard`` so the split modules cooperate.
 */
(function () {
  'use strict';

  // ── Style injection (core shell + header + exchange summary + tab strip) ──
  const CSS = `
  #board {
    height: 100vh; min-height: 0; overflow: hidden;
    display: grid;
    /* header (auto) + exchange summary (auto) + tab strip (auto) + the active
       tab pane (fills rest). Each tab pane owns its inner layout; long lists
       scroll inside each panel's .p-body, never the page. */
    grid-template-rows: auto auto auto minmax(0, 1fr);
    gap: 8px;
    padding: 10px 16px;
    box-sizing: border-box;
    background: linear-gradient(180deg, rgba(8,11,17,0.92), rgba(5,7,11,0.96));
    border-left: 1px solid var(--ghost);
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--p-wht);
  }
  #board .b-pos { color: var(--p-grn); }
  #board .b-neg { color: var(--p-red); }
  #board .b-flat { color: var(--p-dim); }
  #board .num { font-variant-numeric: tabular-nums; }

  /* Header */
  #board .b-head {
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    padding-bottom: 4px; border-bottom: 1px solid var(--ghost);
  }
  #board .b-head .title { font-weight: 700; letter-spacing: 0.18em; color: var(--p-wht); font-size: 15px; }
  #board .b-head .star { color: var(--polaris-blue); }
  #board .b-head .badge {
    font-size: 10px; letter-spacing: 0.18em; font-weight: 700;
    padding: 2px 8px; border: 1px solid var(--polaris-blue); color: var(--polaris-blue);
  }
  #board .b-head .meta { color: var(--p-gry); font-size: 11px; }
  #board .b-head .meta b { color: var(--p-wht); font-weight: 700; }
  #board .b-head .clock { margin-left: auto; color: var(--p-cyn); font-weight: 700; font-size: 14px; }
  #board .b-head .regime-tag { color: var(--p-mag); font-weight: 700; letter-spacing: 0.10em; text-transform: uppercase; }

  /* KPI cards — header strip, all in one row */
  #board .kpis {
    display: grid; grid-template-columns: repeat(8, 1fr); gap: 7px;
  }
  #board .kpi {
    border: 1px solid rgba(95,135,175,0.22);
    background: rgba(15,19,26,0.55);
    padding: 6px 9px;
    min-width: 0; overflow: hidden;
  }
  #board .kpi .k { color: var(--p-dim); font-size: 9px; letter-spacing: 0.10em; text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #board .kpi .v { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 2px; line-height: 1.1;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #board .kpi .sub { color: var(--p-gry); font-size: 9px; margin-top: 1px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* 3-stream exchange summary strip — ALWAYS visible (Jin: stays on top).
     server-fed d.streams, 3 venue lanes. Display-only rollup. */
  #board .streams-strip {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px;
  }
  #board .lane {
    border: 1px solid rgba(95,135,175,0.22);
    border-left: 4px solid var(--ghost);
    background: rgba(15,19,26,0.55);
    padding: 5px 9px; min-width: 0; overflow: hidden;
  }
  #board .lane.lane-a { border-left-color: var(--stream-a); }
  #board .lane.lane-b { border-left-color: var(--stream-b); }
  #board .lane.lane-c { border-left-color: var(--stream-c); }
  #board .lane .ln-top {
    display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  }
  #board .lane .ln-label {
    font-size: 10px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #board .lane.lane-a .ln-label { color: var(--stream-a); }
  #board .lane.lane-b .ln-label { color: var(--stream-b); }
  #board .lane.lane-c .ln-label { color: var(--stream-c); }
  #board .lane .ln-eq { font-size: 14px; font-weight: 700; font-variant-numeric: tabular-nums;
    color: var(--p-wht); white-space: nowrap; }
  #board .lane .ln-tagline {
    font-size: 8px; letter-spacing: 0.06em; color: var(--p-dim);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px;
  }
  #board .lane .ln-stats {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px 8px; margin-top: 3px;
    font-size: 10px;
  }
  #board .lane .ln-stats .s { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .lane .ln-stats .s .lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.08em; text-transform: uppercase; }
  #board .lane .ln-stats .s .lv { font-variant-numeric: tabular-nums; }
  #board .lane .ln-cost {
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 2px 8px; margin-top: 3px;
    font-size: 9px; border-top: 1px dotted rgba(255,255,255,0.08); padding-top: 3px;
  }
  #board .lane .ln-cost .s { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .lane .ln-cost .s .lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.06em; text-transform: uppercase; }
  #board .lane .ln-cost .s .lv { font-variant-numeric: tabular-nums; }
  #board .lane .ln-closed {
    margin-top: 3px; font-size: 9px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    border-top: 1px dotted rgba(255,255,255,0.08); padding-top: 3px;
  }
  #board .lane .ln-closed .rc-lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.06em; text-transform: uppercase; }
  #board .lane .ln-closed .rc-item { margin-right: 7px; }
  #board .lane .ln-closed .rc-sym { color: var(--p-gry); }
  #board .lane .ln-closed .rc-pn { font-variant-numeric: tabular-nums; }

  /* ── Tab strip (E2) — full bottom width, 8 tabs, each pane full-width. ──── */
  #board .b-tabs {
    display: flex; gap: 4px; align-items: stretch; flex-wrap: wrap;
    border-bottom: 1px solid var(--ghost); padding-bottom: 2px;
  }
  #board .b-tab {
    appearance: none; cursor: pointer;
    background: rgba(15,19,26,0.55);
    border: 1px solid rgba(95,135,175,0.22); border-bottom: none;
    color: var(--p-dim);
    font-family: var(--font-mono); font-size: 11px; font-weight: 700;
    letter-spacing: 0.12em; text-transform: uppercase;
    padding: 5px 13px;
  }
  #board .b-tab:hover { color: var(--p-wht); }
  #board .b-tab.active {
    color: var(--polaris-blue);
    border-color: var(--polaris-blue);
    background: rgba(95,135,175,0.10);
  }
  #board .b-tab .tab-cnt { color: var(--p-cyn); font-weight: 700; letter-spacing: 0; margin-left: 5px; font-size: 10px; }
  /* Tab panes: only the active one renders; it fills the whole bottom width. */
  #board .tab-pane { display: none; min-height: 0; }
  #board .tab-pane.active { display: grid; min-height: 0; grid-template-rows: minmax(0, 1fr); }

  /* Stream lane color tokens — used by the always-visible streams strip AND
     (in board_tabs.js) by the per-row grouped-table lane heads. */
  #board { --stream-a: #5fdfff; --stream-b: #a87cff; --stream-c: #ffc84f; }
  /* Panel shell + tables + price-flash + lane-head CSS (used only by tab
     content) live in board_tabs.js to keep this core-shell module compact. */
  `;

  function injectStyle() {
    const s = document.createElement('style');
    s.id = 'board-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // ── helpers (shared with board_tabs.js via window.PolarisBoard) ───────────
  const $ = (id) => document.getElementById(id);
  function fmtUsd(v, dp = 0) {
    if (v == null || isNaN(v)) return '—';
    const sign = v < 0 ? '-' : '';
    const a = Math.abs(v);
    return sign + '$' + a.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  function fmtPct(v, dp = 2) { return (v == null || isNaN(v)) ? '—' : v.toFixed(dp) + '%'; }
  function fmtSignedPct(v, dp = 2) {
    if (v == null || isNaN(v)) return '—';
    return (v >= 0 ? '+' : '') + v.toFixed(dp) + '%';
  }
  function pn(v) { return v > 0 ? 'b-pos' : v < 0 ? 'b-neg' : 'b-flat'; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
  function clockStr() {
    const d = new Date();
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
  }
  function hms(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (h) return h + 'h' + String(m).padStart(2, '0') + 'm';
    if (m) return m + 'm' + String(s).padStart(2, '0') + 's';
    return s + 's';
  }
  function hhmmss(epoch) {
    const d = new Date(epoch * 1000);
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, '0')).join(':');
  }
  // Compact price formatter — adapts dp to magnitude so a $0.0042 alt and a
  // $68,000 BTC both read cleanly.
  function fmtPx(v) {
    if (v == null || isNaN(v) || v <= 0) return '—';
    const a = Math.abs(v);
    const dp = a >= 1000 ? 1 : a >= 1 ? 3 : a >= 0.01 ? 5 : 8;
    return v.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  function fmtR(v, dp = 2) {
    if (v == null || isNaN(v)) return '—';
    return (v >= 0 ? '+' : '') + v.toFixed(dp) + 'R';
  }

  // Client-side venue→stream group-by. A=okx B=capital C=alpaca. Display-only.
  const VENUE_TO_STREAM = { okx: 'A', capital: 'B', alpaca: 'C' };
  const STREAM_ORDER = ['A', 'B', 'C', 'X'];
  const STREAM_LABEL = { A: 'OKX', B: 'CAPITAL', C: 'ALPACA', X: 'OTHER' };
  const STREAM_TAGLINE = {
    A: 'SPOT CRYPTO · long · 24/7',
    B: 'CFD · LONG/SHORT · sessions',
    C: 'US EQUITY · long · RTH/PDT',
    X: 'unmapped venue',
  };
  function venueStream(venue) {
    return VENUE_TO_STREAM[String(venue || '').toLowerCase()] || 'X';
  }
  function laneGroups(rows) {
    const by = {};
    rows.forEach(r => { const s = venueStream(r.venue); (by[s] = by[s] || []).push(r); });
    return STREAM_ORDER.filter(s => by[s] && by[s].length).map(s => ({ stream: s, rows: by[s] }));
  }

  // ── one-time skeleton ─────────────────────────────────────────────
  // HEADER (KPIs) + 3-STREAM EXCHANGE SUMMARY (always visible) + full-width
  // TAB STRIP + the 8 tab panes (only the active one renders). board_tabs.js
  // owns each pane's inner markup + render fn.
  const TABS = [
    { id: 'positions', label: 'Positions' },
    { id: 'trades', label: 'Trades' },
    { id: 'regime', label: 'Regime' },
    { id: 'strategy', label: 'Strategy' },
    { id: 'exit', label: 'Exit' },
    { id: 'ai', label: 'AI' },
    { id: 'edge', label: 'Edge' },
    { id: 'risk', label: 'Risk' },
  ];

  function skeleton() {
    const tabBtns = TABS.map((t, i) =>
      `<button type="button" class="b-tab${i === 0 ? ' active' : ''}" data-tab="${t.id}">`
      + `${esc(t.label)}<span class="tab-cnt" id="tabcnt-${t.id}"></span></button>`
    ).join('');
    const panes = window.PolarisBoardTabs
      ? window.PolarisBoardTabs.paneMarkup(TABS)
      : '';
    return `
    <div class="b-head">
      <span class="title"><span class="star">★</span> POLARIS</span>
      <span class="badge">DEMO·PAPER</span>
      <span class="regime-tag" id="b-regime">—</span>
      <span class="meta">focus <b id="b-focus">—</b> · cells <b id="b-cells">—</b> · refresh <b id="b-refresh">—</b></span>
      <span class="clock" id="b-clock">--:--:--</span>
    </div>

    <div class="kpis" id="b-kpis"></div>

    <!-- 3-STREAM EXCHANGE SUMMARY — always visible (stays on top). -->
    <div class="streams-strip" id="b-streams"></div>

    <!-- FULL-WIDTH TAB STRIP — POSITIONS default. -->
    <div class="b-tabs" id="b-tabs">${tabBtns}</div>

    ${panes}`;
  }

  // ── shared renderers (header + KPIs + exchange summary) ───────────────────
  function renderHeader(d) {
    const topRegime = (d.regime_bars || []).slice().sort((a, b) => b.count - a.count)[0];
    $('b-regime').textContent = topRegime ? topRegime.regime.replace(/_/g, ' ') : '—';
    $('b-focus').textContent = d.universe_focus_n ?? '—';
    $('b-cells').textContent = d.active_cells_n ?? '—';
    $('b-refresh').textContent = d.universe_last_refresh || '—';
  }

  function renderKpis(d) {
    let cw = 0, cn = 0;
    (d.strategy_stats || []).forEach(s => { cw += (s.wr_pct || 0) * (s.closed_n || 0); cn += (s.closed_n || 0); });
    const wr = cn ? cw / cn : null;
    const aiCost = (d.gpt_stats || []).reduce((a, g) => a + (g.cost_24h_proj_usd || 0), 0);
    const expPct = d.equity_now ? (d.exposed_usd / d.equity_now) * 100 : 0;
    const feeReal = d.real_fee_total || 0, feeDemo = d.demo_fee_total || 0;
    const cards = [
      { k: 'Net PnL (session)', v: fmtUsd(d.daily_pnl_usd, 2), cls: pn(d.daily_pnl_usd), sub: (d.daily_trades || 0) + ' trades' },
      { k: 'Equity', v: fmtUsd(d.equity_now, 0), cls: '', sub: 'start ' + fmtUsd(d.starting_capital, 0) },
      { k: 'Drawdown', v: '-' + fmtPct(d.drawdown_pct), cls: (d.drawdown_pct > 0 ? 'b-neg' : 'b-flat'), sub: 'peak ' + fmtUsd(d.peak_equity, 0) },
      { k: 'uPnL', v: fmtUsd(d.upnl_total, 2), cls: pn(d.upnl_total), sub: (d.open_positions_n || 0) + ' pos' },
      { k: 'Exposure', v: fmtUsd(d.exposed_usd, 0), cls: '', sub: fmtPct(expPct, 1) + ' of eq' },
      { k: 'Win Rate', v: (wr == null ? '—' : fmtPct(wr, 1)), cls: '', sub: cn + ' closed' },
      { k: 'Turnover', v: ((d.confidence && d.confidence.turnover_ratio || 0).toFixed(2)) + '×', cls: '', sub: 'Σnotl/eq' },
      { k: 'FeeDrag real|demo', v: fmtUsd(feeReal, 0), cls: 'b-flat', sub: 'demo ' + fmtUsd(feeDemo, 0) },
    ];
    $('b-kpis').innerHTML = cards.map(c =>
      `<div class="kpi"><div class="k">${esc(c.k)}</div><div class="v num ${c.cls}">${c.v}</div><div class="sub">${esc(c.sub)}</div></div>`
    ).join('');
  }

  // Per-stream summary strip — ALWAYS visible. Server-fed d.streams.
  function renderStreams(d) {
    const el = $('b-streams');
    if (!el) return;
    const rows = d.streams || [];
    if (!rows.length) { el.innerHTML = ''; return; }
    el.innerHTML = rows.map(s => {
      const st = venueStream(s.venue);
      const lc = st.toLowerCase();
      const tagline = STREAM_TAGLINE[st] || '';
      const expPct = s.equity_usd ? (s.exposed_usd / s.equity_usd) * 100 : 0;
      const hasCost = s.net_after_cost_usd != null;
      const costRow = hasCost ? `
        <div class="ln-cost">
          <span class="s"><span class="lk">Fee</span> <span class="lv b-flat">${fmtUsd(s.fee_usd, 2)}</span></span>
          <span class="s"><span class="lk">Slip</span> <span class="lv b-flat">${fmtUsd(s.slippage_usd, 2)}</span></span>
          <span class="s" title="AI cost attributed to this lane only (position-linked gate calls); pre-position G1-G5 LLM spend is unattributable and excluded"><span class="lk">AI$*</span> <span class="lv b-flat">${fmtUsd(s.ai_cost_usd, 4)}</span></span>
          <span class="s"><span class="lk">Net-Cost</span> <span class="lv ${pn(s.net_after_cost_usd)}">${fmtUsd(s.net_after_cost_usd, 2)}</span></span>
        </div>` : '';
      return `<div class="lane lane-${lc}" title="${esc(s.label)} — ${esc(tagline)} (${esc(s.product_class)}) · start ${fmtUsd(s.starting_capital, 0)} · DD ${fmtPct(s.drawdown_pct)}">
        <div class="ln-top">
          <span class="ln-label">${esc(s.label)}</span>
          <span class="ln-eq">${fmtUsd(s.equity_usd, 0)}</span>
        </div>
        <div class="ln-tagline">${esc(tagline)}</div>
        <div class="ln-stats">
          <span class="s"><span class="lk">Net PnL</span> <span class="lv ${pn(s.net_pnl_usd)}">${fmtUsd(s.net_pnl_usd, 2)}</span></span>
          <span class="s"><span class="lk">uPnL</span> <span class="lv ${pn(s.upnl_usd)}">${fmtUsd(s.upnl_usd, 2)}</span></span>
          <span class="s"><span class="lk">Exposure</span> <span class="lv b-flat">${fmtUsd(s.exposed_usd, 0)}</span></span>
          <span class="s" title="currently-open positions"><span class="lk">Open</span> <span class="lv ${s.open_positions_n ? 'b-pos' : 'b-flat'}">${s.open_positions_n || 0}</span></span>
          <span class="s" title="closed trades this session"><span class="lk">Closed</span> <span class="lv b-flat">${(s.closed_n != null ? s.closed_n : (s.daily_trades || 0))}</span></span>
          <span class="s"><span class="lk">Exp%</span> <span class="lv b-flat">${fmtPct(expPct, 1)}</span></span>
        </div>${costRow}${recentClosedRow(s)}
      </div>`;
    }).join('');
  }

  function recentClosedRow(s) {
    const rc = s.recent_closed || [];
    if (!rc.length) return '';
    const items = rc.slice(0, 4).map(t => {
      const cls = pn(t.pnl_usd);
      return `<span class="rc-item" title="${esc(t.symbol)} ${esc(t.strategy_id)} ${esc(t.exit_reason)} ${fmtUsd(t.pnl_usd, 2)}">`
        + `<span class="rc-sym">${esc(t.symbol)}</span> <span class="rc-pn ${cls}">${fmtUsd(t.pnl_usd, 1)}</span></span>`;
    }).join('');
    return `<div class="ln-closed" title="recently-closed (distinct from currently-open)"><span class="rc-lk">closed:</span> ${items}</div>`;
  }

  // ── tab switcher ──────────────────────────────────────────────────────────
  // Pure DOM visibility — no data/snapshot/trading change. Tracks the active
  // tab id so the poll loop only re-renders the visible pane (cheap).
  let _activeTab = TABS[0].id;
  function initTabs() {
    const tabs = $('b-tabs');
    if (!tabs) return;
    tabs.addEventListener('click', (e) => {
      const btn = e.target.closest('.b-tab');
      if (!btn) return;
      const which = btn.getAttribute('data-tab');
      if (!which) return;
      _activeTab = which;
      tabs.querySelectorAll('.b-tab').forEach(b =>
        b.classList.toggle('active', b === btn));
      TABS.forEach(t => {
        const pane = $('pane-' + t.id);
        if (pane) pane.classList.toggle('active', t.id === which);
      });
      // Re-render the just-activated pane immediately from the last frame.
      if (_lastFrame && window.PolarisBoardTabs) {
        window.PolarisBoardTabs.renderTab(which, _lastFrame);
      }
    });
  }

  // ── poll loop ─────────────────────────────────────────────────────────────
  let _lastFrame = null;
  function render(d) {
    _lastFrame = d;
    renderHeader(d);
    renderKpis(d);
    renderStreams(d);
    if (window.PolarisBoardTabs) {
      window.PolarisBoardTabs.renderTabCounts(d, TABS);
      // Render only the visible tab (the rest re-render on switch from cache).
      window.PolarisBoardTabs.renderTab(_activeTab, d);
    }
  }

  async function poll() {
    try {
      const r = await fetch('/api/snapshot?t=' + Date.now(), { cache: 'no-store' });
      if (r.ok) render(await r.json());
    } catch (e) { /* keep last frame */ }
  }

  // ── Bot log (bottom-left pane) ────────────────────────────────────────────
  function classifyLog(line) {
    const l = line.toLowerCase();
    if (/\b(error|critical|fatal)\b/.test(l) || l.includes('exception') || l.includes('traceback'))
      return 'bl-err';
    if (/\b(warn|warning)\b/.test(l) || l.includes('rejected') || l.includes('retry'))
      return 'bl-warn';
    if (l.includes('order resp ok=true') || /\bopen(ed)?\b/.test(l)
        || /\bclos(e|ed)\b/.test(l) || /\bfill(ed)?\b/.test(l) || /\[tick \d+\]/.test(l))
      return 'bl-hi';
    return '';
  }
  function fmtLogLine(line) {
    const m = line.match(/^(\S+T\S+Z?)\s+(.*)$/);
    const cls = classifyLog(line);
    if (m) {
      return `<div class="bl-line ${cls}"><span class="ts">${esc(m[1])}</span> ${esc(m[2])}</div>`;
    }
    return `<div class="bl-line ${cls}">${esc(line)}</div>`;
  }
  let _lastLogSig = '';
  async function pollLog() {
    const body = $('botlog-body');
    if (!body) return;
    try {
      const r = await fetch('/api/botlog?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const d = await r.json();
      const lines = d.lines || [];
      const sig = lines.length + '|' + (lines[lines.length - 1] || '');
      if (sig === _lastLogSig) return;
      _lastLogSig = sig;
      const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
      body.innerHTML = lines.map(fmtLogLine).join('');
      if (atBottom) body.scrollTop = body.scrollHeight;
    } catch (e) { /* keep last frame */ }
  }

  // ── Sphere freeze watchdog ────────────────────────────────────────────────
  function startSphereWatchdog() {
    let lastHB = -1, stalledMs = 0, nudged = false;
    setInterval(function () {
      if (document.hidden) { stalledMs = 0; nudged = false; lastHB = window.__sphereHB || 0; return; }
      const hb = window.__sphereHB || 0;
      if (hb !== lastHB) {
        lastHB = hb; stalledMs = 0; nudged = false;
        return;
      }
      stalledMs += 1000;
      if (stalledMs >= 10000) {
        location.reload();
      } else if (stalledMs >= 5000 && !nudged) {
        nudged = true;
        window.dispatchEvent(new Event('resize'));
      }
    }, 1000);
  }

  // Expose shared helpers for board_tabs.js (loaded after this file).
  window.PolarisBoard = {
    $: $, fmtUsd: fmtUsd, fmtPct: fmtPct, fmtSignedPct: fmtSignedPct,
    fmtPx: fmtPx, fmtR: fmtR, pn: pn, esc: esc, hms: hms, hhmmss: hhmmss,
    venueStream: venueStream, laneGroups: laneGroups,
    STREAM_LABEL: STREAM_LABEL, STREAM_TAGLINE: STREAM_TAGLINE,
  };

  function init() {
    injectStyle();
    const board = $('board');
    if (board) {
      board.innerHTML = skeleton();
      initTabs();
      setInterval(() => { const c = $('b-clock'); if (c) c.textContent = clockStr(); }, 1000);
      poll();
      setInterval(poll, 1000);
    }
    if ($('botlog-body')) {
      pollLog();
      setInterval(pollLog, 1000);
    }
    startSphereWatchdog();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
