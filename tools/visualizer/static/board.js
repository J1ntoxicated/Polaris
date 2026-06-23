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
    /* 6 rows: head + KPIs + exchange selector + summary + tab strip + the active
       tab pane (the only 1fr — hidden display:none panes take no grid space).
       MUST be 6 tracks or the tab strip eats the 1fr and the pane spills to an
       implicit bottom row. Each pane owns its layout; lists scroll in .p-body. */
    grid-template-rows: auto auto auto auto auto minmax(0, 1fr);
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
  /* (k) Polaris star — bigger; LIVE = soft twinkle/glow shimmer, STALE = dim/calm.
     Glow built only from existing --polaris-blue token alpha (no new colours). */
  #board .b-head .star { color: var(--polaris-blue); font-size: 21px; line-height: 1;
    display: inline-block; transition: opacity .6s ease, text-shadow .6s ease; }
  #board .b-head .star.live { animation: starTwinkle 2.4s ease-in-out infinite; }
  #board .b-head .star.stale { opacity: 0.4; text-shadow: none; }
  @keyframes starTwinkle {
    0%,100% { opacity: 1;    text-shadow: 0 0 5px rgba(95,135,175,0.55), 0 0 11px rgba(95,135,175,0.30); transform: scale(1); }
    50%     { opacity: 0.78; text-shadow: 0 0 13px rgba(95,135,175,1), 0 0 26px rgba(95,135,175,0.55); transform: scale(1.10); }
  }
  #board .b-head .badge {
    font-size: 10px; letter-spacing: 0.18em; font-weight: 700;
    padding: 2px 8px; border: 1px solid var(--polaris-blue); color: var(--polaris-blue);
  }
  #board .b-head .meta { color: var(--p-gry); font-size: 11px; }
  #board .b-head .meta b { color: var(--p-wht); font-weight: 700; }
  #board .b-head .clock { margin-left: auto; color: var(--p-cyn); font-weight: 700; font-size: 14px; }
  #board .b-head .regime-lbl { color: var(--p-gry); font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; margin-right: 5px; }
  #board .b-head .regime-tag { color: var(--p-mag); font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; font-size: 11px; }

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
  /* KPI headline labels (clean English money headline — 2026-06-22). */
  #board #b-kpis .kk { color: var(--p-dim); font-size: 10px; letter-spacing: 0.06em;
    text-transform: uppercase; }
  /* (j) HEALTH one-liner — at-a-glance status above the money headline.
     state-colour comes from existing pnl tokens; no new colours. */
  #board #b-kpis .health {
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
    padding: 1px 2px 3px; font-size: 11px; border-bottom: 1px dotted rgba(95,135,175,0.14);
  }
  #board #b-kpis .health .hl { font-weight: 700; letter-spacing: 0.10em; text-transform: uppercase; }
  #board #b-kpis .health .dot { font-size: 9px; margin-right: 4px; }
  #board #b-kpis .health .anom { color: var(--p-ylw); }
  #board #b-kpis .health .anom.clear { color: var(--p-grn); }
  /* (f) main-area dual equity sparkline — axis-less Tufte; sits in the KPI block. */
  #board #b-kpis .eq-spark { display: flex; align-items: center; gap: 10px; }
  #board #b-kpis .eq-spark svg { display: block; width: 200px; height: 30px; flex: 0 0 auto; }
  #board #b-kpis .eq-spark .lg { font-size: 9px; color: var(--p-dim); letter-spacing: 0.04em;
    text-transform: uppercase; white-space: nowrap; }
  #board #b-kpis .eq-spark .lg .v { font-weight: 700; font-variant-numeric: tabular-nums; }
  /* (j-iii) anomaly strip — only things to watch, else 'all clear'. */
  #board #b-kpis .anoms { display: flex; align-items: center; gap: 9px; flex-wrap: wrap;
    font-size: 10px; color: var(--p-ylw); }
  #board #b-kpis .anoms .ax { white-space: nowrap; }
  #board #b-kpis .anoms .ax::before { content: '⚠ '; }
  #board #b-kpis .anoms.clear { color: var(--p-grn); }
  #board #b-kpis .anoms.clear .ax::before { content: '✓ '; }

  /* 3-stream exchange summary strip — ALWAYS visible (Jin: stays on top).
     Bloomberg de-card (2026-06-22): one dense tabular ROW per venue, no card
     chrome. server-fed d.streams. E3 row click→scope + highlight: board_exchange.js. */
  #board .streams-strip {
    border: 1px solid rgba(95,135,175,0.22); background: rgba(15,19,26,0.40);
    overflow: hidden;
  }
  #board .streams-tbl { width: 100%; border-collapse: collapse; font-size: 11px; table-layout: fixed; }
  #board .streams-tbl thead th {
    color: var(--p-dim); font-weight: 700; text-align: right; letter-spacing: 0.06em;
    padding: 3px 8px; font-size: 9px; text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    border-bottom: 1px solid var(--ghost); background: rgba(10,13,18,0.96);
  }
  #board .streams-tbl thead th.l { text-align: left; }
  #board .streams-tbl tbody td {
    padding: 3px 8px; text-align: right; font-variant-numeric: tabular-nums;
    border-bottom: 1px dotted rgba(95,135,175,0.08); color: var(--p-gry);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #board .streams-tbl tbody td.l { text-align: left; }
  #board .streams-tbl tbody tr.lane td:first-child { border-left: 3px solid var(--ghost); }
  #board .streams-tbl tbody tr.lane.lane-a td:first-child { border-left-color: var(--stream-a); }
  #board .streams-tbl tbody tr.lane.lane-b td:first-child { border-left-color: var(--stream-b); }
  #board .streams-tbl tbody tr.lane.lane-c td:first-child { border-left-color: var(--stream-c); }
  #board .streams-tbl td.ln-label { font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
  #board .streams-tbl tr.lane-a td.ln-label { color: var(--stream-a); }
  #board .streams-tbl tr.lane-b td.ln-label { color: var(--stream-b); }
  #board .streams-tbl tr.lane-c td.ln-label { color: var(--stream-c); }
  #board .streams-tbl td.ln-tag { font-size: 9px; letter-spacing: 0.04em; }
  #board .streams-tbl td.ln-eq { color: var(--p-wht); font-weight: 700; }
  #board .streams-tbl td.ln-rc .rc-item { margin-right: 6px; }
  #board .streams-tbl td.ln-rc .rc-sym { color: var(--p-gry); }
  #board .streams-tbl td.ln-rc .rc-pn { font-variant-numeric: tabular-nums; }

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

  // (j/k) Snapshot freshness — LIVE when the latest snapshot tick is recent.
  // d.ts_now = epoch seconds the snapshot was generated; compared to wall clock.
  // STALE_SEC = a few poll intervals of slack (poll = 1s, snapshot bg-refresh
  // is coarser). Returns { live, ageSec }.
  const STALE_SEC = 30;
  function freshness(d) {
    const ts = d && d.ts_now;
    if (!ts) return { live: false, ageSec: null };
    const age = Math.floor(Date.now() / 1000) - ts;
    return { live: age >= 0 && age <= STALE_SEC, ageSec: age };
  }
  // Axis-less Tufte sparkline path for a numeric series into a w×h box.
  function sparkPath(series, w, h, pad) {
    const n = series.length;
    if (n < 2) return '';
    const mn = Math.min(...series), mx = Math.max(...series);
    const span = (mx - mn) || 1;
    const x = i => pad + (i / (n - 1)) * (w - 2 * pad);
    const y = v => pad + (1 - (v - mn) / span) * (h - 2 * pad);
    let dd = '';
    series.forEach((v, i) => { dd += (i === 0 ? 'M' : 'L') + ' ' + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' '; });
    return dd;
  }
  // Shared min/max across two series so both sparklines share one y-scale.
  function sparkPathShared(series, mn, mx, w, h, pad) {
    const n = series.length;
    if (n < 2) return '';
    const span = (mx - mn) || 1;
    const x = i => pad + (i / (n - 1)) * (w - 2 * pad);
    const y = v => pad + (1 - (v - mn) / span) * (h - 2 * pad);
    let dd = '';
    series.forEach((v, i) => { dd += (i === 0 ? 'M' : 'L') + ' ' + x(i).toFixed(1) + ' ' + y(v).toFixed(1) + ' '; });
    return dd;
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
  // 6 composite tabs (2026-06-22 redesign). Each tab groups several existing
  // snapshot panels + new sub-renderers; the 6-row #board grid contract is
  // UNCHANGED — only the tab-strip content + per-tab pane grouping changed.
  const TABS = [
    { id: 'activity', label: 'Activity' },
    { id: 'performance', label: 'Performance' },
    { id: 'logic', label: 'Logic' },
    { id: 'build', label: 'Build' },
    { id: 'path', label: 'Roadmap' },
    { id: 'learned', label: 'Lessons' },
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
      <span class="regime-lbl">MARKETS</span><span class="regime-tag" id="b-regime">—</span>
      <span class="meta">Watching <b id="b-focus">—</b> symbols · <b id="b-cells">—</b> active cells · refreshed <b id="b-refresh">—</b></span>
      <span class="clock" id="b-clock">--:--:--</span>
    </div>

    <div class="kpis" id="b-kpis"></div>

    <!-- EXCHANGE SELECTOR (E3) — scopes tabs below + globe focus (board_exchange.js). -->
    ${window.PolarisBoardExchange ? window.PolarisBoardExchange.selectorMarkup() : ''}

    <!-- 3-STREAM EXCHANGE SUMMARY — always visible (stays on top). -->
    <div class="streams-strip" id="b-streams"></div>

    <!-- FULL-WIDTH TAB STRIP — POSITIONS default. -->
    <div class="b-tabs" id="b-tabs">${tabBtns}</div>

    ${panes}`;
  }

  // ── shared renderers (header + KPIs + exchange summary) ───────────────────
  // Per-instrument regime DISTRIBUTION (Jin 2026-06-23): regime is per-market
  // (60 markets, each own regime in regime_states), NOT one global regime. The
  // header shows the spread across markets ("39 choppy · 10 volatile · …") so it
  // never reads as a single global label. regime_bars already counts per-market.
  function regimeDist(d) {
    const bars = (d.regime_bars || []).filter(b => (b.count || 0) > 0)
      .slice().sort((a, b) => b.count - a.count);
    if (!bars.length) return null;
    const total = bars.reduce((s, b) => s + (b.count || 0), 0);
    return { total, parts: bars.map(b => b.count + ' ' + regimePlain(b.regime)) };
  }
  function renderHeader(d) {
    const dist = regimeDist(d);
    const tag = $('b-regime');
    if (dist) {
      // Jin 2026-06-23: regime notation was too much on screen. Compact to
      // total + dominant only; full per-regime breakdown stays in the tooltip
      // (+ the REGIME tab + Open Positions REGIME column).
      tag.textContent = dist.total + ' mkt · ' + dist.parts[0];
      tag.title = 'Regime is PER-INSTRUMENT — ' + dist.total
        + ' markets, each classified on its own. Counts: ' + dist.parts.join(', ')
        + '. (Per-market detail in the REGIME tab + Open Positions REGIME column.)';
    } else {
      tag.textContent = '—';
      tag.title = '';
    }
    $('b-focus').textContent = d.universe_focus_n ?? '—';
    $('b-cells').textContent = d.active_cells_n ?? '—';
    $('b-refresh').textContent = d.universe_last_refresh || '—';
    // (k) star LIVE/STALE — twinkle when the snapshot tick is fresh, dim when stale.
    const star = document.querySelector('#board .b-head .star');
    if (star) {
      const live = freshness(d).live;
      star.classList.toggle('live', live);
      star.classList.toggle('stale', !live);
    }
  }

  // (j) Anomaly scan — only things worth watching. Returns a short string list;
  // empty list ⇒ 'all clear'. Pure display heuristics off live snapshot fields:
  //  · stuck exits   = positions whose exit FSM left 'open' but still held long
  //  · stale price   = snapshot tick itself is stale (ts_now age)
  //  · drift losses  = positions reconciled away without a clean exit this session
  const STUCK_EXIT_SEC = 1800;   // 30m past the exit FSM leaving 'open'
  function scanAnomalies(d) {
    const out = [];
    const pos = d.positions || [];
    const stuck = pos.filter(p => {
      const st = String(p.exit_state || '').toLowerCase();
      return st && st !== 'open' && (p.held_sec || 0) > STUCK_EXIT_SEC;
    }).length;
    if (stuck) out.push(stuck + ' stuck exit' + (stuck > 1 ? 's' : ''));
    const fr = freshness(d);
    if (!fr.live) out.push('stale price feed' + (fr.ageSec != null ? ' (' + hms(fr.ageSec) + ')' : ''));
    const driftN = d.reconciled_loss_n || 0;
    const driftUsd = d.reconciled_loss_usd || 0;
    if (driftN > 0) out.push(driftN + ' tracking failure' + (driftN > 1 ? 's' : '') + ' (~-$' + Math.abs(driftUsd).toFixed(0) + ', not trades)');
    return out;
  }

  // Plain-English regime label (Jin 2026-06-22: no CHOP-style abbreviations).
  // Canonical regimes are bull_trend / bear_trend / chop / crisis (+ range /
  // trend); anything unmapped falls back to underscores→spaces.
  const REGIME_PLAIN = {
    bull_trend: 'trending up', bear_trend: 'trending down', chop: 'choppy',
    crisis: 'volatile', range: 'range-bound', trend: 'trending',
    calm: 'calm', quiet: 'quiet', neutral: 'neutral',
  };
  function regimePlain(r) {
    if (!r) return '—';
    const k = String(r).toLowerCase();
    return REGIME_PLAIN[k] || k.replace(/_/g, ' ');
  }

  // Reshaped (2026-06-22, Jin feedback): the always-visible KPI block is now a
  // CLEAN ENGLISH MONEY HEADLINE — no jargon clusters. Row 1 = status (bot /
  // market / updated). Row 2 = labelled money metrics. The old BLEED (worst
  // strategies) + HIDDEN (costs) blocks moved to the Performance tab.
  function renderKpis(d) {
    const el = $('b-kpis'); if (!el) return;
    el.style.display = 'block';
    const c = d.confidence || {};
    const pf = c.profit_factor, wr = c.win_rate_pct;
    // Regime is per-instrument (60 markets). Show the dominant share + a full
    // per-market breakdown on hover, never a single global label.
    const rdist = regimeDist(d);
    const top = rdist ? (d.regime_bars || []).slice().sort((a, b) => b.count - a.count)[0] : null;
    const market = rdist
      ? top.count + '/' + rdist.total + ' ' + regimePlain(top.regime)
      : '—';
    const marketTitle = rdist
      ? 'Per-instrument: ' + rdist.parts.join(', ') + ' (' + rdist.total + ' markets)'
      : '';
    // today's % vs starting capital (snapshot carries no daily_pnl_pct).
    const start = d.starting_capital || 0;
    const dayPct = start ? (d.daily_pnl_usd / start) * 100 : null;
    const profitable = (pf != null && pf >= 1);
    const pfTag = (pf == null) ? ''
      : profitable
        ? '<span class="b-pos" style="font-size:9px;letter-spacing:.1em">profitable</span>'
        : '<span class="b-neg" style="font-size:9px;letter-spacing:.1em">losing</span>';
    // (j-i) HEALTH one-liner — Bot LIVE/STALE by tick freshness · winning/losing
    //       · anomaly count. (j-ii) state colour: winning green / losing red.
    const fr = freshness(d);
    const botCls = fr.live ? 'b-pos' : 'b-neg';
    const botTxt = fr.live ? 'LIVE' : 'STALE';
    const botSub = fr.live ? '' : (fr.ageSec != null ? ' <span class="b-flat" style="font-weight:400">' + hms(fr.ageSec) + ' ago</span>' : '');
    const winning = (pf != null) ? profitable : (d.daily_pnl_usd >= 0);
    const winCls = winning ? 'b-pos' : 'b-neg';
    const winTxt = winning ? 'WINNING' : 'LOSING';
    const anoms = scanAnomalies(d);
    const anomTxt = anoms.length
      ? '<span class="anom">' + anoms.length + ' to watch</span>'
      : '<span class="anom clear">all clear</span>';
    const health =
      `<div class="health">
        <span><span class="dot ${botCls}">●</span><span class="hl ${botCls}">Bot ${botTxt}</span>${botSub}</span>
        <span class="hl ${winCls}">${winTxt}</span>
        ${anomTxt}
        ${dualSparkHtml(d)}
      </div>`;
    const status = health +
      `<div style="display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;padding:4px 2px;font-size:12px">
        <span><span class="kk">Bot</span> <span class="${botCls}" style="font-weight:700">${botTxt}</span></span>
        <span title="${esc(marketTitle)}"><span class="kk">Markets</span> <span style="color:var(--p-wht);font-weight:700">${esc(market)}</span></span>
        <span style="margin-left:auto"><span class="kk">Updated</span> <span class="b-flat" id="b-kpi-clock">${clockStr()}</span></span>
      </div>`;
    const metric = (label, valHtml, tag) =>
      `<span style="display:inline-flex;align-items:baseline;gap:6px">
        <span class="kk">${label}</span> <span style="font-weight:700">${valHtml}</span>${tag ? ' ' + tag : ''}</span>`;
    const metrics =
      `<div style="display:flex;gap:20px;align-items:baseline;flex-wrap:wrap;padding:4px 2px;margin-top:3px;border-top:1px solid rgba(255,255,255,.08);font-size:13px">
        ${metric('Equity', `<span title="OKX demo charges 70bps (7x real); real-fee-net = equity at live 10bps fees"><span class="b-flat">${fmtUsd(d.equity_now, 0)}</span> <span class="kk">demo</span> · <span class="${(start && d.equity_now_real_fee_net >= start) ? 'b-pos' : 'b-neg'}" style="font-weight:700">${fmtUsd(d.equity_now_real_fee_net, 0)}</span> <span class="kk">real-fee-net</span></span>`)}
        ${metric('Today', `<span class="${pn(d.daily_pnl_usd)}">${fmtUsd(d.daily_pnl_usd, 0)}${dayPct == null ? '' : ' (' + fmtSignedPct(dayPct, 2) + ')'}</span>`)}
        ${metric('Win rate', `<span class="b-flat">${wr == null ? '—' : wr.toFixed(0) + '%'}</span>`)}
        ${metric('Profit factor', `<span class="${profitable ? 'b-pos' : 'b-neg'}">${pf == null ? '—' : pf.toFixed(2)}</span>`, pfTag)}
        ${metric('Max drawdown', `<span class="b-neg">-${fmtPct(d.drawdown_pct, 1)}</span>`)}
      </div>`;
    // (j-iii) anomaly strip — only things to watch, else 'all clear'.
    const anomStrip = anoms.length
      ? `<div class="anoms">${anoms.map(a => `<span class="ax">${esc(a)}</span>`).join('')}</div>`
      : `<div class="anoms clear"><span class="ax">all clear — no stuck exits, fresh price, no drift spikes</span></div>`;
    el.innerHTML = status + metrics + anomStrip;
  }

  // (f) main-area dual-equity sparkline — equity_curve (demo, dashed dim) vs
  // equity_curve_real_fee_net (headline, green/red by trend). Axis-less, small,
  // shares one y-scale so the GAP (honest fee cost) reads at a glance. Renders
  // inline in the HEALTH row; graceful empty when the curves aren't ready.
  function dualSparkHtml(d) {
    const real = d.equity_curve_real_fee_net || [];
    const demo = d.equity_curve || [];
    if (real.length < 2 && demo.length < 2) return '';
    const W = 200, H = 30, pad = 2;
    const all = real.concat(demo);
    const mn = Math.min(...all), mx = Math.max(...all);
    const rUp = real.length >= 2 ? real[real.length - 1] >= real[0] : true;
    const rStroke = rUp ? 'var(--p-grn)' : 'var(--p-red)';
    let paths = '';
    if (demo.length >= 2) {
      paths += `<path d="${sparkPathShared(demo, mn, mx, W, H, pad)}" fill="none" stroke="rgba(158,158,158,0.55)" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
    }
    if (real.length >= 2) {
      paths += `<path d="${sparkPathShared(real, mn, mx, W, H, pad)}" fill="none" stroke="${rStroke}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`;
    }
    const dv = real.length >= 2 ? real[real.length - 1] - real[0] : null;
    const lg = dv == null ? ''
      : `<span class="lg">trend <span class="v ${pn(dv)}">${fmtUsd(dv, 0)}</span> after real fees</span>`;
    return `<span class="eq-spark" style="margin-left:auto" title="Equity trend — solid line = profit after REAL OKX fees (green up / red down); dim dashed = demo-actual. Gap = honest fee cost.">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${paths}</svg>${lg}</span>`;
  }

  // Per-stream summary strip — ALWAYS visible. Server-fed d.streams.
  // Bloomberg de-card (2026-06-22): each venue = ONE dense tabular row (no card
  // chrome / big padding). venue │ equity │ net PnL │ uPnL │ exposure │ open │
  // closed │ fee │ slip │ net-cost · recently-closed inline. Row click → scope
  // (data-ex preserved; board_exchange.js delegates the click + highlights).
  function renderStreams(d) {
    const el = $('b-streams');
    if (!el) return;
    const rows = d.streams || [];
    if (!rows.length) { el.innerHTML = ''; return; }
    const trs = rows.map(s => {
      const st = venueStream(s.venue);
      const lc = st.toLowerCase();
      const tagline = STREAM_TAGLINE[st] || '';
      const expPct = s.equity_usd ? (s.exposed_usd / s.equity_usd) * 100 : 0;
      const closed = (s.closed_n != null ? s.closed_n : (s.daily_trades || 0));
      const exKey = String(s.venue || '').toLowerCase();   // E3 click→scope key
      return `<tr class="lane lane-${lc}" data-ex="${esc(exKey)}" title="${esc(s.label)} — ${esc(tagline)} (${esc(s.product_class)}) · click to scope · start ${fmtUsd(s.starting_capital, 0)} · DD ${fmtPct(s.drawdown_pct)}">
        <td class="l ln-label">${esc(s.label)}</td>
        <td class="l ln-tag b-flat">${esc(tagline)}</td>
        <td class="num ln-eq">${fmtUsd(s.equity_usd, 0)}</td>
        <td class="num ${pn(s.net_pnl_usd)}">${fmtUsd(s.net_pnl_usd, 0)}</td>
        <td class="num ${pn(s.upnl_usd)}">${fmtUsd(s.upnl_usd, 0)}</td>
        <td class="num b-flat">${fmtUsd(s.exposed_usd, 0)}</td>
        <td class="num b-flat">${fmtPct(expPct, 0)}</td>
        <td class="num ${s.open_positions_n ? 'b-pos' : 'b-flat'}">${s.open_positions_n || 0}</td>
        <td class="num b-flat">${closed}</td>
        <td class="num b-flat" title="fees this session">${fmtUsd(s.fee_usd, 0)}</td>
        <td class="num b-flat" title="slippage this session">${fmtUsd(s.slippage_usd, 0)}</td>
        <td class="num ${pn(s.net_after_cost_usd)}" title="net after fees + slippage">${s.net_after_cost_usd == null ? '—' : fmtUsd(s.net_after_cost_usd, 0)}</td>
        <td class="l ln-rc b-flat">${recentClosedInline(s)}</td>
      </tr>`;
    }).join('');
    el.innerHTML =
      `<table class="streams-tbl"><colgroup>
        <col style="width:9%"><col style="width:14%"><col style="width:8%"><col style="width:7%">
        <col style="width:7%"><col style="width:7%"><col style="width:4%"><col style="width:4%">
        <col style="width:5%"><col style="width:5%"><col style="width:5%"><col style="width:6%">
        <col style="width:13%">
       </colgroup><thead><tr>
        <th class="l">VENUE</th><th class="l">TRACK</th><th>EQUITY</th><th>NET P&L</th>
        <th>uPnL</th><th>EXPOSURE</th><th>EXP%</th><th>OPEN</th>
        <th>CLOSED</th><th>FEE</th><th>SLIP</th><th title="net after fees + slippage">NET-COST</th>
        <th class="l">RECENTLY CLOSED</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
    // Re-apply the active-exchange highlight (innerHTML rewrite cleared it).
    if (window.PolarisBoardExchange) window.PolarisBoardExchange.syncExchangeUi();
  }

  // Recently-closed, inline (was a separate row in the old card). Compact.
  function recentClosedInline(s) {
    const rc = s.recent_closed || [];
    if (!rc.length) return '—';
    return rc.slice(0, 4).map(t =>
      `<span class="rc-item" title="${esc(t.symbol)} ${esc(t.strategy_id)} ${esc(t.exit_reason)} ${fmtUsd(t.pnl_usd, 2)}">`
      + `<span class="rc-sym">${esc(t.symbol)}</span> <span class="rc-pn ${pn(t.pnl_usd)}">${fmtUsd(t.pnl_usd, 0)}</span></span>`
    ).join(' ');
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
  // E3: re-render streams highlight + active tab now (called by board_exchange.js).
  function rerenderActive() {
    if (!_lastFrame) return;
    renderStreams(_lastFrame);
    if (window.PolarisBoardTabs) window.PolarisBoardTabs.renderTab(_activeTab, _lastFrame);
  }
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
    // Bridge optional probe data to the Neural Cloud (display-only). Probes pulse
    // their ticker node + venue galaxy on the globe; graceful no-op when absent.
    if (window.PolarisGlobe && window.PolarisGlobe.showProbes) {
      window.PolarisGlobe.showProbes(d.probe_events || d.probes || []);
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
  // Rolling tail (Jin: 한 줄씩 차례로 롤링; 페이지 전체 repaint 금지). The /api/botlog
  // tail overlaps heavily each poll, so we APPEND only the genuinely-new trailing
  // lines (matched by the last few shown lines as a stable block) instead of
  // rewriting innerHTML — no flicker, scroll stays pinned, old lines roll off the top.
  let _logShown = [];
  const _LOG_CAP = 600;
  function _newTail(prev, lines) {
    if (!prev.length) return lines;                 // first paint
    const k = Math.min(3, prev.length);
    const sig = prev.slice(prev.length - k);
    for (let i = lines.length - 1; i >= k - 1; i--) {
      let ok = true;
      for (let j = 0; j < k; j++) { if (lines[i - (k - 1) + j] !== sig[j]) { ok = false; break; } }
      if (ok) return lines.slice(i + 1);            // lines after the matched block
    }
    return null;                                    // discontinuity (rotation/gap) → full render
  }
  async function pollLog() {
    const body = $('botlog-body');
    if (!body) return;
    try {
      const r = await fetch('/api/botlog?t=' + Date.now(), { cache: 'no-store' });
      if (!r.ok) return;
      const lines = (await r.json()).lines || [];
      if (!lines.length) return;
      const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
      const nt = _newTail(_logShown, lines);
      let appended = false;
      if (nt === null) {                            // discontinuity: one full render
        body.innerHTML = lines.map(fmtLogLine).join('');
        _logShown = lines.slice();
        appended = true;
      } else if (nt.length) {                        // normal: append new lines only
        body.insertAdjacentHTML('beforeend', nt.map(fmtLogLine).join(''));
        _logShown = _logShown.concat(nt);
        appended = true;
      }
      if (appended) {
        while (_logShown.length > _LOG_CAP) {        // roll old lines off the top
          _logShown.shift();
          if (body.firstChild) body.removeChild(body.firstChild);
        }
        if (atBottom) body.scrollTop = body.scrollHeight;
      }
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
    freshness: freshness,
    // E3: re-render hook for exchange-select. The scope helpers
    // (getActiveExchange/venueMatches/venueFilter) are added by board_exchange.js.
    rerenderActive: rerenderActive,
  };

  function init() {
    injectStyle();
    const board = $('board');
    if (board) {
      board.innerHTML = skeleton();
      initTabs();
      if (window.PolarisBoardExchange) window.PolarisBoardExchange.initExchangeSelector();
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
