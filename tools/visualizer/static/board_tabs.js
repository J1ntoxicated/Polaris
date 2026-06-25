/* board_tabs.js — Polaris E2 Trading-IA full-width tab renderers (display-only).
 *
 * The 8 tabs below the always-visible 3-stream exchange summary (Jin 2026-05-31
 * IA rebuild). Each tab is full-width + richly columned. Loaded AFTER board.js;
 * consumes the shared helpers on ``window.PolarisBoard`` and exposes
 * ``window.PolarisBoardTabs`` = { paneMarkup, renderTab, renderTabCounts }.
 *
 * Pure DOM render off the /api/snapshot frame — no fetch, no sizing/order/exit
 * logic, no snapshot mutation. Tabs: POSITIONS / TRADES / REGIME / STRATEGY /
 * EXIT / AI / EDGE / RISK.
 */
(function () {
  'use strict';

  const B = window.PolarisBoard;
  if (!B) { return; }   // board.js must load first
  const { $, fmtUsd, fmtPct, fmtSignedPct, fmtPx, fmtPxCcy, fmtR, sparkline, pn,
    esc, hms, hhmmss, venueStream, laneGroups, STREAM_LABEL, STREAM_TAGLINE,
    getActiveExchange, venueFilter } = B;

  // E3 (Jin 2026-05-31): when an exchange is selected (not 'all'), each tab
  // scopes its venue-carrying rows to that venue (client-side filter; no data
  // change). A small inline note is appended to a scoped panel head; tabs whose
  // data carries no venue render with an 'all venues' note rather than a faked
  // per-venue split. ``scopeLabel`` = uppercase venue name or '' on ALL.
  function scopeLabel() {
    const ex = getActiveExchange();
    return ex === 'all' ? '' : ex.toUpperCase();
  }

  // ── tab CSS (panel shell + tables + lane heads + inner layouts) ───────────
  // These styles back the tab CONTENT only; the always-visible header / KPIs /
  // streams strip / tab strip live in board.js. The --stream-* color tokens are
  // defined on #board in board.js (shared with the streams strip).
  const CSS = `
  /* Panel shell (reused by every tab). */
  #board .panel {
    border: 1px solid rgba(95,135,175,0.22);
    background: rgba(15,19,26,0.40);
    display: flex; flex-direction: column; min-height: 0; overflow: hidden;
  }
  #board .panel .p-head {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 4px 10px; border-bottom: 1px solid var(--ghost); flex: 0 0 auto;
    color: var(--polaris-blue); font-size: 10px; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase;
  }
  #board .panel .p-head .cnt { color: var(--p-cyn); letter-spacing: 0; }
  /* collapsible panel: chevron in the header (down=expanded / right=collapsed). */
  #board .panel .p-head-toggle { cursor: pointer; user-select: none; gap: 6px; }
  #board .panel .p-head-toggle .ttl { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .panel .p-head-toggle:hover { color: var(--p-cyn); }
  #board .panel .p-head-toggle:focus-visible { outline: 1px solid var(--p-cyn); outline-offset: -1px; }
  #board .panel .p-head-toggle .chev {
    flex: 0 0 auto; width: 0; height: 0; align-self: center;
    border-left: 4px solid currentColor; border-top: 4px solid transparent; border-bottom: 4px solid transparent;
    transform: rotate(90deg); transition: transform 0.12s ease; opacity: 0.85;
  }
  #board .panel.collapsed .p-head-toggle .chev { transform: rotate(0deg); }
  #board .panel.collapsed .p-body { display: none; }
  #board .panel .p-body { overflow: auto; flex: 1 1 0; min-height: 0; }
  #board .panel .p-body::-webkit-scrollbar { width: 5px; height: 5px; }
  #board .panel .p-body::-webkit-scrollbar-thumb { background: var(--ghost); }
  /* Panels in CONTENT-SIZED grid tracks (auto / min-content) must size their
     body to its content. With flex-basis:0 (the fill-and-scroll default used by
     panels in 1fr / min-height tracks) a content-track flex column collapses to
     header-only — the grow has no definite height to fill, so the body renders
     at 0px (count shows in the header, content invisible). flex-basis:auto makes
     the body's natural height the basis so the panel grows to fit. Scoped to the
     two content-track contexts: the Logic-tab scroll stack + the always-expanded
     Gate Funnel / Live Gate Activity panels at the top of the Activity stack. */
  #board .logic-scroll > .panel > .p-body { flex: 1 1 auto; }
  /* gate/probe quad panel bodies size to content (capped + scroll). The quad
     sits in an auto grid track with no definite height, so a flex-fill body
     would collapse to 0 — pin the body to its natural height instead. */
  #board .gate-quad > .panel > .p-body { flex: 0 0 auto; }

  /* Tables (reused by every tab). */
  #board table { width: 100%; border-collapse: collapse; font-size: 11px; table-layout: fixed; }
  #board thead th {
    position: sticky; top: 0; background: rgba(10,13,18,0.96);
    color: var(--p-dim); font-weight: 700; text-align: right; letter-spacing: 0.04em;
    padding: 4px 7px; font-size: 10px; text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #board thead th.l { text-align: left; }
  #board tbody td {
    padding: 3px 7px; text-align: right; font-variant-numeric: tabular-nums;
    border-bottom: 1px dotted rgba(95,135,175,0.08); color: var(--p-gry);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  #board tbody td.l { text-align: left; }
  #board tbody td.tk { color: var(--p-wht); font-weight: 700; }
  #board tbody td.ex { color: var(--p-cyn); font-size: 10px; font-weight: 700; }
  #board tbody td.dir.long, #board tbody td.dir.buy { color: var(--p-grn); font-weight: 700; }
  #board tbody td.dir.short, #board tbody td.dir.sell { color: var(--p-red); font-weight: 700; }
  #board tbody tr:hover td { background: rgba(95,135,175,0.06); }
  #board .empty { color: var(--p-dim); padding: 8px; text-align: center; font-size: 10px; }
  #board .stack-badge { cursor: help; border-bottom: 1px dotted var(--p-dim); }
  /* Symbol sparkline — tiny inline recent-close trend next to the symbol cell.
     vertical-align middle so it sits on the text baseline; never wraps. */
  #board svg.spark { vertical-align: middle; margin-left: 6px; opacity: 0.85; flex: 0 0 auto; }

  /* Per-cell value-change flash (Bloomberg per-cell diff) — only a cell whose
     live value CHANGED this poll flashes; unchanged cells keep their DOM. ~400ms
     green-up / red-down fade. Used by syncTable() for Open Positions + streams. */
  #board td.px-flash-up   { animation: pxup 0.42s ease-out; }
  #board td.px-flash-down { animation: pxdn 0.42s ease-out; }
  @keyframes pxup { 0% { background: rgba(135,215,135,0.45); } 100% { background: transparent; } }
  @keyframes pxdn { 0% { background: rgba(215,135,135,0.45); } 100% { background: transparent; } }

  /* Per-row lane heads + lane left-borders (grouped POSITIONS / TRADES tables). */
  #board td.lane-head {
    padding: 2px 7px 2px 6px; border-left: 4px solid var(--ghost);
    border-bottom: 1px solid var(--ghost);
    font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
    text-align: left; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    color: var(--p-dim); background: rgba(15,19,26,0.55);
  }
  #board td.lane-head .ln-cnt { color: var(--p-gry); letter-spacing: 0; font-weight: 400; margin-left: 5px; }
  #board td.lane-head .ln-tag { color: var(--p-dim); letter-spacing: 0.06em; font-weight: 400; font-size: 8px; margin-left: 7px; text-transform: none; }
  #board td.lane-head.lane-a { border-left-color: var(--stream-a); color: var(--stream-a); }
  #board td.lane-head.lane-b { border-left-color: var(--stream-b); color: var(--stream-b); }
  #board td.lane-head.lane-c { border-left-color: var(--stream-c); color: var(--stream-c); }
  #board tbody tr.row-a td:first-child { border-left: 3px solid var(--stream-a); }
  #board tbody tr.row-b td:first-child { border-left: 3px solid var(--stream-b); }
  #board tbody tr.row-c td:first-child { border-left: 3px solid var(--stream-c); }
  #board tbody tr.row-x td:first-child { border-left: 3px solid var(--ghost); }

  /* Two-up / three-up panel grids used by several tabs. */
  #board .tab-grid-1 { display: grid; grid-template-rows: minmax(0,1fr); gap: 8px; min-height: 0; }
  #board .tab-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; min-height: 0; }
  #board .tab-grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; min-height: 0; }
  #board .tab-rows-2 { display: grid; grid-template-rows: auto minmax(0,1fr); gap: 8px; min-height: 0; }
  /* Activity stack (Jin 2026-06-23 4-section rebuild): a 2×2 gate/probe QUAD of
     real tables (Gate Decisions | Probe List · Live Gate Activity | Probe
     Activity) on top, then Open Positions + Recent Trades full-width below.
     The quad is content-auto; the two tables share the remaining 1fr each. */
  /* Open Positions + Recent Trades rows have a height FLOOR (minmax 240px) so on
     a short viewport the stack overflows into the pane's outer scroll (Jin
     2026-06-24 "전체 페이지 스크롤") instead of crushing the tables to nothing.
     On a tall viewport they still share the free space as 1fr. */
  #board .activity-stack { display: grid; grid-template-rows: auto minmax(240px,1fr) minmax(240px,1fr); gap: 8px; min-height: 0; }
  /* Activity tab (Jin 2026-06-24): gate-quad moved to the Gates tab, so the
     stack is just Open Positions + Recent Trades — two rows, no outer scroll
     (each table's own .p-body scrolls; the pane fits the 1fr grid row). */
  #board .activity-stack.act-clean { grid-template-rows: minmax(0,1fr) minmax(0,1fr); }
  /* 2×2 gate/probe quad. Row 1 = decision/state (auto, capped). Row 2 = live
     streams (capped scroll ticker). Each cell is a real <table> panel. */
  #board .gate-quad { display: grid; grid-template-columns: 1fr 1fr; grid-auto-rows: min-content; gap: 8px; min-height: 0; }
  #board .gate-quad > .panel { min-width: 0; }
  #board .p-body.gq-state { max-height: 172px; }
  #board .p-body.gq-stream { max-height: 150px; }
  /* Open Positions (always expanded) + Recent Trades (collapsible) share the
     two minmax(0,1fr) tracks. A collapsed Recent Trades (body display:none)
     would keep its full free-space share, so drop its track to header height. */
  #board .activity-stack:has(.collapsible.collapsed:nth-child(3)) { grid-template-rows: auto minmax(240px,1fr) min-content; }
  /* act-clean (positions + collapsible Recent Trades): when Recent Trades
     (nth-child 2) is collapsed, give all free space to Open Positions. */
  #board .activity-stack.act-clean:has(.collapsible.collapsed:nth-child(2)) { grid-template-rows: minmax(0,1fr) min-content; }

  /* mini key/value rows (REGIME / STRATEGY / EXIT / AI / EDGE / RISK panels). */
  #board .mini { font-size: 11px; }
  #board .mini .row {
    display: grid; align-items: center; gap: 8px; padding: 2px 10px;
    border-bottom: 1px dotted rgba(95,135,175,0.08);
  }
  #board .mini .row > * { min-width: 0; }
  #board .mini .row .name { color: var(--p-wht); font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .mini .row .num { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .mini .row .sub { color: var(--p-dim); font-size: 9px; }

  /* horizontal bar (regime confidence / agreement / gate funnel). */
  #board .hbar { height: 8px; background: rgba(95,135,175,0.12); position: relative; }
  #board .hbar > i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--p-cyn); }
  #board .hbar.bar-pos > i { background: var(--p-grn); }
  #board .hbar.bar-neg > i { background: var(--p-red); }
  #board .hbar.bar-warn > i { background: var(--p-ylw); }

  /* chips (FSM states / exit reasons / evidence layers). */
  #board .chips { display: flex; flex-wrap: wrap; gap: 5px; padding: 6px 10px; }
  #board .chip {
    border: 1px solid rgba(95,135,175,0.30); background: rgba(15,19,26,0.6);
    padding: 2px 8px; font-size: 10px; white-space: nowrap;
    display: inline-flex; align-items: center; gap: 5px;
  }
  #board .chip .cn { color: var(--p-cyn); font-weight: 700; font-variant-numeric: tabular-nums; }
  #board .chip .cl { color: var(--p-gry); letter-spacing: 0.06em; }
  #board .chip.ev-l1 { border-left: 3px solid #b48cff; }
  #board .chip.ev-l2 { border-left: 3px solid #ffaa55; }
  #board .chip.ev-l3 { border-left: 3px solid #87ffd7; }

  /* equity chart (EDGE tab) — reused from the legacy board. */
  #board .eq-wrap {
    border: 1px solid rgba(95,135,175,0.22);
    background: rgba(15,19,26,0.40);
    padding: 5px 10px 3px;
  }
  #board .eq-wrap .eq-head {
    display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
    font-size: 10px; letter-spacing: 0.12em; color: var(--p-dim); text-transform: uppercase;
  }
  #board .eq-wrap .eq-head .h-title { color: var(--polaris-blue); font-weight: 700; }
  #board .eq-wrap .eq-head .v { color: var(--p-wht); font-weight: 700; }
  /* eq block is a non-panel collapsible — eq-head toggles, eq-body folds. */
  #board .eq-wrap .eq-head.collapse-toggle { cursor: pointer; user-select: none; }
  #board .eq-wrap .eq-head.collapse-toggle:hover .h-title { color: var(--p-cyn); }
  #board .eq-wrap .eq-head.collapse-toggle:focus-visible { outline: 1px solid var(--p-cyn); outline-offset: 2px; }
  #board .eq-wrap .eq-head .chev {
    flex: 0 0 auto; width: 0; height: 0; align-self: center;
    border-left: 4px solid var(--polaris-blue); border-top: 4px solid transparent; border-bottom: 4px solid transparent;
    transform: rotate(90deg); transition: transform 0.12s ease; opacity: 0.85; margin-right: -8px;
  }
  #board .eq-wrap.collapsed .eq-head .chev { transform: rotate(0deg); }
  #board .eq-wrap.collapsed .eq-body { display: none; }
  #board .eq-wrap svg { display: block; width: 100%; height: 90px; }
  #board .conf-strip {
    display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px;
    margin-top: 3px; padding-top: 3px; border-top: 1px dotted rgba(95,135,175,0.14);
    font-size: 9.5px; letter-spacing: 0.04em; color: var(--p-dim);
  }
  #board .conf-strip .ck { color: var(--p-dim); text-transform: uppercase; letter-spacing: 0.10em; }
  #board .conf-strip .cv { color: var(--p-wht); font-weight: 700; }
  #board .conf-strip .cell { display: inline-flex; align-items: center; gap: 4px; padding: 0 5px; border-left: 2px solid var(--ghost); }
  #board .conf-strip .cell.lcb-pos { border-left-color: var(--p-grn); }
  #board .conf-strip .cell.lcb-neg { border-left-color: var(--p-red); }
  #board .conf-strip .cell .nm { color: var(--p-wht); font-weight: 700; }
  #board .conf-strip .cell .rg { color: var(--p-dim); }

  /* rotation telemetry strip (RISK tab). */
  #board .rot-strip {
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    border: 1px solid rgba(95,135,175,0.22); border-left: 4px solid var(--p-mag);
    background: rgba(15,19,26,0.55); padding: 4px 10px; font-size: 10px;
    min-width: 0; overflow: hidden;
  }
  #board .rot-strip .rt-title { font-size: 9px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: var(--p-mag); }
  #board .rot-strip .rt-kv { white-space: nowrap; }
  #board .rot-strip .rt-kv .lk { color: var(--p-dim); font-size: 8px; letter-spacing: 0.08em; text-transform: uppercase; }
  #board .rot-strip .rt-kv .lv { font-variant-numeric: tabular-nums; color: var(--p-wht); font-weight: 700; }
  #board .rot-strip .rt-last { color: var(--p-gry); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  #board .rot-strip .rt-last b { color: var(--p-cyn); }
  #board .rot-strip .rt-none { color: var(--p-dim); }

  #board .lvl-ERROR, #board .lvl-CRITICAL { color: var(--p-red); font-weight: 700; }
  #board .lvl-WARN, #board .lvl-WARNING { color: var(--p-ylw); }
  #board .lvl-INFO { color: var(--p-cyn); }
  #board .verdict-edge { color: var(--p-grn); }
  #board .verdict-anti { color: var(--p-red); }
  #board .verdict-neutral { color: var(--p-ylw); }
  /* Logic tab — many panels stacked; the pane itself scrolls (the rest fit). */
  #board .logic-scroll { display: grid; grid-auto-rows: min-content; gap: 8px; min-height: 0; overflow: auto; align-content: start; }
  #board .logic-scroll::-webkit-scrollbar { width: 5px; }
  #board .logic-scroll::-webkit-scrollbar-thumb { background: var(--ghost); }
  #board .logic-scroll .tab-grid-3 { min-height: 130px; }
  /* Logic 2-up dense rows (Bloomberg 2026-06-22): the small analytics panels
     pair up; the regime + pathway + learner lists run FULL-WIDTH above. */
  #board .logic-cols-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; min-height: 110px; }
  /* Performance tab — equity (auto) + a scrolling stack of analytics grids. */
  #board .perf-scroll { display: grid; grid-auto-rows: min-content; gap: 8px; min-height: 0; overflow: auto; align-content: start; }
  #board .perf-scroll::-webkit-scrollbar { width: 5px; }
  #board .perf-scroll::-webkit-scrollbar-thumb { background: var(--ghost); }
  #board .perf-scroll .tab-grid-3, #board .perf-scroll .tab-grid-2 { min-height: 130px; }
  /* shared inline label token (eq-head money labels, etc.). */
  #board .kk { color: var(--p-dim); letter-spacing: 0.06em; text-transform: uppercase; }
  `;

  function injectStyle() {
    const s = document.createElement('style');
    s.id = 'board-tabs-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  // Restore a persisted collapse state for any non-panel collapsible section
  // (e.g. the equity block). Returns { cls, expanded } so the markup can stamp
  // the .collapsed class + aria up front (default expanded).
  function collapseState(key) {
    let collapsed = false;
    try { collapsed = localStorage.getItem(key) === '1'; } catch (e) { /* private mode */ }
    return { collapsed, cls: collapsed ? ' collapsed' : '', aria: collapsed ? 'false' : 'true' };
  }

  // Single full-width panel helper.
  function panel(title, bodyId, bodyCls) {
    return `<div class="panel"><div class="p-head"><span>${esc(title)}</span>`
      + `<span class="cnt" id="${bodyId}-cnt"></span></div>`
      + `<div class="p-body ${bodyCls || ''}" id="${bodyId}"></div></div>`;
  }

  // Collapsible full-width panel: a chevron in the header toggles the body and
  // persists per-panel state in localStorage (default expanded). Used for the
  // Gate Funnel + Live Gate Activity panels so Jin can fold either away.
  function collapsiblePanel(title, bodyId, bodyCls, panelCls) {
    const key = 'pb.collapse.' + bodyId;
    let collapsed = false;
    try { collapsed = localStorage.getItem(key) === '1'; } catch (e) { /* private mode */ }
    return `<div class="panel collapsible${collapsed ? ' collapsed' : ''}${panelCls ? ' ' + panelCls : ''}" data-collapse-key="${esc(key)}">`
      + `<div class="p-head p-head-toggle" role="button" tabindex="0" aria-expanded="${collapsed ? 'false' : 'true'}">`
      + `<span class="chev" aria-hidden="true"></span>`
      + `<span class="ttl">${esc(title)}</span>`
      + `<span class="cnt" id="${bodyId}-cnt"></span></div>`
      + `<div class="p-body ${bodyCls || ''}" id="${bodyId}"></div></div>`;
  }

  // Delegated toggle: one listener on #board handles every collapsible panel.
  // Click (or Enter/Space on the keyboard-focused header) folds the body and
  // writes the state to localStorage. Attached once after the skeleton mounts.
  function wireCollapsibles() {
    const root = document.getElementById('board');
    if (!root || root._collapseWired) return;
    root._collapseWired = true;
    function toggle(head) {
      const p = head.closest('.collapsible'); if (!p) return;
      const collapsed = p.classList.toggle('collapsed');
      head.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      const key = p.getAttribute('data-collapse-key');
      try { if (key) localStorage.setItem(key, collapsed ? '1' : '0'); } catch (e) { /* private mode */ }
    }
    root.addEventListener('click', (e) => {
      const head = e.target.closest('.p-head-toggle, .collapse-toggle');
      if (head && root.contains(head)) toggle(head);
    });
    root.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const head = e.target.closest('.p-head-toggle, .collapse-toggle');
      if (head && root.contains(head)) { e.preventDefault(); toggle(head); }
    });
  }

  // ── pane markup (called once by board.js skeleton) ────────────────────────
  // 6 COMPOSITE panes (2026-06-22 redesign). Every legacy body-id is PRESERVED
  // (pos-body / trd-body / regime-body / strat-body / eq-svg / b-confidence /
  // b-benchmark / edge-body / exit-fsm / exit-reasons / exit-gates / ai-gpt /
  // ai-shadow / cell-body / alert-body / risk-admit / b-rotation) so the
  // existing renderers bind unchanged — they're just regrouped under the new
  // tabs, plus 5 new sub-renderer mounts (gate-funnel / ticker-body / learner-
  // body / aifree-banner / dual-eq). build/path/learned = P3 placeholders.
  function paneMarkup() {
    return `
    <!-- TAB 1 · 활동 (activity) — Open Positions + Recent Trades ONLY (Jin
         2026-06-24). The gate/probe quad moved to its own Gates tab so the
         activity pane stays clean (positions + trades). -->
    <div class="tab-pane active" id="pane-activity">
      <div class="activity-stack act-clean">
        ${panel('Open Positions · 3 Streams', 'pos-body')}
        ${collapsiblePanel('Recent Trades', 'trd-body')}
      </div>
    </div>

    <!-- TAB · Gates — 4-section gate/probe quad (moved out of Activity).
         Row1 decision/state: Gate Decisions | Probe List. Row2 live streams:
         Live Gate Activity | Probe Activity. All real aligned tables. -->
    <div class="tab-pane" id="pane-gates">
      <div class="gate-quad">
        ${collapsiblePanel('Gate Decisions · G1→G8 what each gate decided (1h)', 'gate-funnel', 'gq-state')}
        ${collapsiblePanel('Probe List · EXIT-ONLY (G6) · positions under probe · lean / conf', 'probe-list', 'gq-state')}
        ${collapsiblePanel('Live Gate Activity · newest first', 'gate-feed', 'gq-stream')}
        ${collapsiblePanel('Probe Activity · EXIT-ONLY (G6) observe-only · per-probe readings · newest first', 'probe-activity', 'gq-stream')}
      </div>
    </div>

    <!-- TAB 2 · Performance — equity + strategy + per-ticker + edge + worst + costs. -->
    <div class="tab-pane" id="pane-performance">
      <div class="tab-rows-2">
        <div class="eq-wrap collapsible${collapseState('pb.collapse.eq-wrap').cls}" data-collapse-key="pb.collapse.eq-wrap">
          <div class="eq-head collapse-toggle" role="button" tabindex="0" aria-expanded="${collapseState('pb.collapse.eq-wrap').aria}">
            <span class="chev" aria-hidden="true"></span>
            <span class="h-title" title="Headline line = profit after REAL OKX fees (0.10% taker). Dimmed line = demo-actual (0.7% demo fee). Go-live trigger = the real-fee line trending up. The gap between the two lines = the honest fee cost.">Equity · after real fees vs demo (gap = honest fee cost)</span>
            <span><span class="kk">After real fees</span> <span class="v" id="eq-real-delta">—</span></span>
            <span class="b-flat"><span class="kk">Demo</span> <span class="v" id="eq-demo-delta">—</span></span>
            <span title="Extra fees the demo charges vs the real OKX schedule (demo is a 7x penalty)."><span class="kk">Demo fee penalty</span> <span class="v" id="eq-fee-wedge">—</span></span>
          </div>
          <div class="eq-body">
            <svg id="eq-svg" viewBox="0 0 600 90" preserveAspectRatio="none"></svg>
            <div class="conf-strip" id="b-confidence"></div>
            <div class="conf-strip" id="b-benchmark" title="Offline deterministic replay benchmark (real OKX fee, baseline clock). 3-tier gate: relative / risk-adjusted / statistical. Edge significance on held-out bars."></div>
          </div>
        </div>
        <div class="perf-scroll">
          <div class="tab-grid-3">
            ${collapsiblePanel('Per-Strategy · win rate / net R / per-regime cell value', 'strat-body', 'mini')}
            ${collapsiblePanel('Per-Ticker · cumulative R by symbol', 'ticker-body', 'mini')}
            ${collapsiblePanel('Edge Validation · per strategy × ticker × regime', 'edge-body', 'mini')}
          </div>
          <div class="tab-grid-2">
            ${collapsiblePanel('Worst strategies · biggest money losers', 'worst-body', 'mini')}
            ${collapsiblePanel('Costs & hidden losses', 'costs-body', 'mini')}
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 3 · 로직 (logic) — AI-free banner + regime + cell + exit + ai-shadow + learner.
         Every legacy body-id is mounted here (exit-fsm chips + exit-reasons +
         exit-gates + ai-gpt + ai-shadow + cell-body + alert-body + risk-admit +
         b-rotation) so no renderer is orphaned. -->
    <div class="tab-pane" id="pane-logic">
      <div class="logic-scroll">
        <div class="aifree-banner" id="aifree-banner"></div>
        <div class="rot-strip" id="b-rotation"></div>
        <div class="chips" id="exit-fsm"></div>
        ${collapsiblePanel('Active Strategies · grouped by strategy × regime · which is performing how', 'stratgrp-body', 'mini')}
        ${collapsiblePanel('Active Exits · positions leaving open + why', 'activeexit-body', 'mini')}
        ${collapsiblePanel('Decision Pathways · strategy → regime → cell → exit → edge (active flows)', 'pathway-body', 'mini')}
        ${collapsiblePanel('Regime · venue × asset-group · L1·L2·L3 evidence', 'regime-body', 'mini')}
        ${collapsiblePanel('Learner Network · value · Δ1h tremor', 'learner-body', 'mini')}
        <div class="logic-cols-2">
          ${collapsiblePanel('Exit Reasons · histogram', 'exit-reasons', 'mini')}
          ${collapsiblePanel('Exit Gates · G6 Monitor / G7 Adaptive', 'exit-gates', 'mini')}
        </div>
        <div class="logic-cols-2">
          ${collapsiblePanel('Conductor Shadow · technical vs GPT agreement', 'ai-shadow', 'mini')}
          ${collapsiblePanel('Per-Gate GPT · calls/h · real cost · ok%', 'ai-gpt', 'mini')}
        </div>
        <div class="logic-cols-2">
          ${collapsiblePanel('Admission Shadow · would-suppress (edge-first, net real fee)', 'risk-admit', 'mini')}
          ${collapsiblePanel('Alerts · Halts', 'alert-body', 'mini')}
        </div>
        ${collapsiblePanel('Cell Matrix · top / bottom (per strategy × ticker × regime)', 'cell-body', 'mini')}
      </div>
    </div>

    <!-- TAB · CONTEXT/INTEL — every alt-data context input the bot's regime fuser
         weighs (funding · crypto fear&greed · FRED macro · CFTC COT · news
         sentiment when present), one Bloomberg-dense line per source. "The bot's
         eyes" surfaced read-only — never feeds sizing/gating/exit. -->
    <div class="tab-pane" id="pane-context">
      <div class="tab-grid-1">${collapsiblePanel('Context / Intel · alt-data inputs the bot weighs · source · latest · freshness · lean', 'context-body', 'mini')}</div>
    </div>

    <!-- TAB 4 · Build — commit timeline / digest / test-health (GET /api/buildlog). -->
    <div class="tab-pane" id="pane-build">
      <div class="tab-grid-1">${collapsiblePanel('Build · commit timeline · wave digest · test-health', 'build-body', 'mini')}</div>
    </div>

    <!-- TAB 5 · Roadmap — phase ladder / plan kanban / next strikes (GET /api/roadmap). -->
    <div class="tab-pane" id="pane-path">
      <div class="tab-grid-1">${collapsiblePanel('Roadmap · phase ladder · plan kanban · next strikes', 'path-body', 'mini')}</div>
    </div>

    <!-- TAB 6 · Lessons — lessons feed / anti-pattern wall / root-cause (GET /api/lessons). -->
    <div class="tab-pane" id="pane-learned">
      <div class="tab-grid-1">${collapsiblePanel('Lessons · lessons feed · anti-pattern wall · root-cause', 'learned-body', 'mini')}</div>
    </div>

    <!-- TAB 7 · AI — where AI is used (NOT the trading loop). Header states the
         truth: in-loop trading AI = 0 calls (W3 AI-free core). Two tables below:
         dev debates (GPT/Gemini) + probe escalation (observe-only). GET
         /api/ai_activity (own endpoint, fetched once per activation). -->
    <div class="tab-pane" id="pane-ai">
      <div class="tab-grid-1">${collapsiblePanel('AI · in-loop trading=0 · dev debates (GPT/Gemini) · probe escalation (observe)', 'ai-activity-body', 'mini')}</div>
    </div>`;
  }

  // Hidden mounts (exit-fsm / ai-gpt / ai-shadow head / risk-admit / b-rotation)
  // are legacy body-ids that some renderers still write into. To preserve those
  // renderers without faking extra panels, the composites that call them target
  // ids that now live inside the new panes (exit-reasons / exit-gates / ai-shadow
  // / cell-body). The unused legacy ids (exit-fsm / ai-gpt / risk-admit /
  // b-rotation) are mounted off-pane so a stray write is a graceful no-op.

  // ── tab counts (header badges per tab) ────────────────────────────────────
  function setCnt(id, val) { const el = $(id); if (el) el.textContent = (val === '' || val == null) ? '' : ('· ' + val); }
  function renderTabCounts(d) {
    // Counts reflect the active-exchange scope (venue-carrying tabs). New
    // composite tabs: activity = open positions, performance = strategies,
    // logic = live regime states. build/path/learned = P3 (no count yet).
    setCnt('tabcnt-activity', venueFilter(d.positions).length);
    setCnt('tabcnt-gates', (d.gate_decisions || []).length || '');
    setCnt('tabcnt-performance', (d.strategy_stats || []).length);
    setCnt('tabcnt-logic', venueFilter(d.regime_states).length);
    setCnt('tabcnt-context', (d.context_intel || []).length || '');
    setCnt('tabcnt-build', '');
    setCnt('tabcnt-path', '');
    setCnt('tabcnt-learned', '');
    setCnt('tabcnt-ai', '');   // own endpoint (/api/ai_activity), not the snapshot
  }

  // ── TAB 1 · POSITIONS (expanded columns, flashing CURRENT price) ──────────
  const POS_COLS = 14;
  const _lastPx = {};   // (venue|symbol|strat|side) → last CURRENT price for flash
  // symbol → human full name (display-only; data has no names). Truncates if long.
  const SYM_NAME = {
    BTC:'Bitcoin', ETH:'Ethereum', SOL:'Solana', XRP:'XRP', ADA:'Cardano', DOT:'Polkadot',
    AVAX:'Avalanche', LINK:'Chainlink', MATIC:'Polygon', ATOM:'Cosmos', UNI:'Uniswap', LTC:'Litecoin',
    BCH:'Bitcoin Cash', NEAR:'NEAR Protocol', APT:'Aptos', ARB:'Arbitrum', OP:'Optimism', SUI:'Sui',
    INJ:'Injective', TIA:'Celestia', SEI:'Sei', CRV:'Curve DAO', AAVE:'Aave', MKR:'Maker',
    SUSHI:'SushiSwap', COMP:'Compound', SNX:'Synthetix', LDO:'Lido DAO', FET:'Fetch.ai', RNDR:'Render',
    RENDER:'Render', GRT:'The Graph', FIL:'Filecoin', ALGO:'Algorand', XLM:'Stellar', HBAR:'Hedera',
    VET:'VeChain', THETA:'Theta Network', FLOKI:'Floki', PEPE:'Pepe', SHIB:'Shiba Inu', DOGE:'Dogecoin',
    WIF:'dogwifhat', BONK:'Bonk', JUP:'Jupiter', PYTH:'Pyth Network', JTO:'Jito', ENA:'Ethena',
    ONDO:'Ondo', KP3R:'Keep3rV1', SPK:'Spark', HYPE:'Hyperliquid', DEP:'DEAPcoin', LIT:'Litentry',
    XMR:'Monero', ETC:'Ethereum Classic', TON:'Toncoin', ICP:'Internet Computer', IMX:'Immutable',
    STX:'Stacks', RUNE:'THORChain', GALA:'Gala', SAND:'The Sandbox', MANA:'Decentraland', NC:'Nano Coin',
    EURUSD:'Euro / US Dollar', GBPUSD:'Pound / US Dollar', USDJPY:'US Dollar / Yen', AUDUSD:'Aussie / US Dollar',
    USDCAD:'US Dollar / Loonie', EURGBP:'Euro / Pound', NZDUSD:'Kiwi / US Dollar', USDCHF:'US Dollar / Franc',
    EURJPY:'Euro / Yen', GBPJPY:'Pound / Yen', AUDUSD_ZERO:'Aussie / US Dollar', USDCNH:'US Dollar / Yuan',
    US100:'US Tech 100 (Nasdaq)', US500:'S&P 500', US30:'Dow Jones 30', US200:'US 2000 (Russell)',
    J225:'Nikkei 225', GER40:'DAX 40', UK100:'FTSE 100', HK50:'Hang Seng 50', EU50:'Euro Stoxx 50',
    VIX:'Volatility Index', OIL_CRUDE:'Crude Oil (WTI)', OIL_BRENT:'Brent Crude', GASOIL:'Gas Oil',
    NATURALGAS:'Natural Gas', GOLD:'Gold', SILVER:'Silver', PALLADIUM:'Palladium', PLATINUM:'Platinum',
    COPPER:'Copper', SOXL:'Semis Bull 3x', TQQQ:'Nasdaq Bull 3x', SPXL:'S&P Bull 3x', INTC:'Intel',
    ADBE:'Adobe', AAPL:'Apple', NVDA:'Nvidia', TSLA:'Tesla', AMD:'AMD', VERU:'Veru Inc',
  };
  function symName(p){
    const s=String(p.symbol||'').toUpperCase();
    const base=s.split(':').pop().split('-')[0].split('/')[0];
    return SYM_NAME[s] || SYM_NAME[base] || '';
  }
  const POS_SHELL =
    `<table><colgroup>
        <col style="width:5%"><col style="width:21%"><col style="width:5%"><col style="width:8%">
        <col style="width:8%"><col style="width:5%"><col style="width:6%"><col style="width:7%">
        <col style="width:6%"><col style="width:6%"><col style="width:8%"><col style="width:6%">
        <col style="width:7%"><col style="width:7%">
       </colgroup><thead><tr>
        <th class="l">VEN</th><th class="l">SYMBOL</th><th>SIDE</th><th>ENTRY</th>
        <th>CURRENT</th><th>Δ%</th><th>SIZE$</th><th>uPnL$</th>
        <th>uPnL%</th><th>HELD</th><th class="l">STRAT</th><th class="l">REGIME</th>
        <th>STOP</th><th title="max-favorable / max-adverse excursion in per-trade-ATR R (atr_r) — distinct from the per-stream ledger R">MFE/MAE</th>
      </tr></thead><tbody></tbody></table>`;
  // numeric flash direction for a value that changed since the last poll.
  function _posFlash(prev, cur) {
    if (prev == null || cur == null || cur === prev) return '';
    return cur > prev ? 'up' : 'down';
  }
  function renderPositions(d) {
    const rows = venueFilter(d.positions);   // E3 venue scope
    const body = $('pos-body'); if (!body) return;
    setCnt('pos-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no open positions${sc ? ' · ' + esc(sc) : ''}</div>`;
      body.__structKey = null;   // force a fresh shell when positions return
      return;
    }
    // MIXED list (Jin 2026-06-01): no per-exchange grouping/lane-heads — all
    // positions in ONE table, sorted by size so venues interleave. The per-row
    // left-border colour (row-{a/b/c}) stays only as a subtle venue cue; the VEN
    // column already names the venue. (The streams-strip on top keeps per-venue
    // summary.) Bloomberg per-cell diff (2026-06-24): keyed rows, only the cells
    // whose value moved flash — CURRENT / uPnL$ / Δ% on a numeric direction.
    const specRows = rows.slice().sort(
      (a, b) => (b.size_usd || 0) - (a.size_usd || 0)
    ).map(p => {
      const lc = venueStream(p.venue).toLowerCase();
      const key = [p.venue, p.symbol, p.strategy_id, p.side].join('|');
      const prev = _lastPx[key] || {};
      const pxFlash = _posFlash(prev.px, p.last_price);
      const upFlash = _posFlash(prev.upnl, p.upnl_usd);
      const dFlash = _posFlash(prev.delta, p.delta_pct);
      _lastPx[key] = { px: p.last_price, upnl: p.upnl_usd, delta: p.delta_pct };
      const rc = (p.row_count > 1)
        ? ` <span class="b-flat stack-badge" title="${p.row_count} stacked positions on this (symbol, strategy, side); SIZE$ is the aggregate">×${p.row_count}</span>`
        : '';
      const dpc = fmtSignedPct(p.delta_pct, 2);
      const upnlPct = fmtSignedPct(p.upnl_pct, 2);
      // issue 4: price cells carry the quote-currency symbol (J225 → ¥); SIZE$ /
      // uPnL$ stay USD. issue 3: REGIME shows the immutable entry regime.
      const qccy = p.quote_ccy || 'USD';
      const stop = (p.stop_price > 0) ? fmtPxCcy(p.stop_price, qccy) : '—';
      const reg = p.entry_regime || p.regime || '';
      // Hardening #6: the per-trade-ATR EXCURSION ruler (*_atr_r) — distinct from
      // the per-stream ledger R (AVG-R below). Fall back to the legacy keys for a
      // stale cached snapshot during rollover.
      const mfeMae = `${fmtR(p.mfe_atr_r ?? p.mfe_r, 1)}/${fmtR(p.mae_atr_r ?? p.mae_r, 1)}`;
      const symHtml = esc(p.symbol) + rc + (symName(p) ? `<span style="color:var(--p-dim);font-size:9px;margin-left:6px">${esc(symName(p))}</span>` : '') + sparkline(p.spark);
      return {
        key: key,
        trCls: 'row-' + lc,
        cells: [
          { text: p.venue, cls: 'l ex', title: p.venue },
          { html: symHtml, cls: 'l tk', title: `${p.symbol}${p.row_count > 1 ? ' ×' + p.row_count : ''}${symName(p) ? ' — ' + symName(p) : ''}` },
          { text: p.side, cls: 'dir ' + p.side, title: `${p.side}${lc === 'b' ? ' (CFD — long/short)' : ''}` },
          { text: fmtPxCcy(p.entry_price, qccy), cls: 'num b-flat', title: 'entry ' + fmtPxCcy(p.entry_price, qccy) },
          { text: fmtPxCcy(p.last_price, qccy), cls: 'num', title: 'current (last close) ' + fmtPxCcy(p.last_price, qccy), flash: pxFlash },
          { text: dpc, cls: 'num ' + pn(p.delta_pct), title: 'price move since entry', flash: dFlash },
          { text: fmtUsd(p.size_usd, 0), cls: 'num' },
          { text: fmtUsd(p.upnl_usd, 2), cls: 'num ' + pn(p.upnl_usd), flash: upFlash },
          { text: upnlPct, cls: 'num ' + pn(p.upnl_pct) },
          { text: hms(p.held_sec), cls: 'num b-flat', title: 'time held' },
          { text: p.strategy_id, cls: 'l b-flat', title: p.strategy_id },
          { text: reg || '—', cls: 'l b-flat', title: 'entry regime ' + (p.entry_regime || '—') + ' · live ' + (p.regime || '—') },
          { text: stop, cls: 'num b-flat', title: 'protective stop ' + stop },
          { text: mfeMae, cls: 'num b-flat', title: `MFE/MAE in per-trade-ATR excursion R (atr_r) — distinct from the per-stream ledger R · exit-FSM ${p.exit_state}` },
        ],
      };
    });
    // The first SYMBOL cell carries inline ellipsis style; the shell colgroup +
    // td white-space handle clipping. Apply the symbol-cell clamp once per cell.
    B.syncTable(body, { tableHtml: () => POS_SHELL, structKey: 'pos-v1', rows: specRows });
    // symbol cell needs the max-width:0 clamp (kept off the diff'd className).
    const tb = body.querySelector('tbody');
    if (tb) tb.querySelectorAll('tr > td.tk').forEach(td => {
      if (!td.style.maxWidth) { td.style.maxWidth = '0'; td.style.overflow = 'hidden'; td.style.textOverflow = 'ellipsis'; td.style.whiteSpace = 'nowrap'; }
    });
  }

  // ── TAB 2 · TRADES (expanded, more rows) ──────────────────────────────────
  const TRD_COLS = 14;
  function renderTrades(d) {
    const rows = venueFilter(d.recent_trades).slice(0, 40);   // E3 venue scope
    const body = $('trd-body'); if (!body) return;
    setCnt('trd-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no recent trades${sc ? ' · ' + esc(sc) : ''}</div>`;
      return;
    }
    // MIXED (Jin 2026-06-01): no per-exchange grouping — recent trades in ONE
    // time-ordered list (newest first); per-row colour is a subtle venue cue only.
    const groups = rows.map(t => {
      const lc = venueStream(t.venue).toLowerCase();
      // issue 2: show the POSITION direction (long/short), NOT the close FILL
      // side (sell = a long exit) which made longs read as shorts.
      const dir = t.position_side || '';
      // issue 3: the immutable ENTRY regime (positions.entry_regime), not "-".
      const reg = t.entry_regime || t.regime || '';
      // issue 4: price cells carry the quote-currency symbol (J225 → ¥).
      const qccy = t.quote_ccy || 'USD';
      // issue 1: gross / fee / net are SEPARATE columns (Jin "수익이랑 피랑 따로
      // 적어"). net = gross − real fee is the single truth; FEE = gross − net so
      // the three columns always reconcile. A small gross that nets negative
      // once the fee bites reads honestly.
      const net = (t.net_usd != null) ? t.net_usd : (t.pnl_usd - (t.real_fee_usd || 0));
      const fee = t.pnl_usd - net;
      return `<tr class="row-${lc}">
          <td class="l b-flat">${hhmmss(t.ts_close)}</td>
          <td class="l ex" title="${esc(t.venue)}">${esc(t.venue)}</td>
          <td class="l tk" title="${esc(t.symbol)}">${esc(t.symbol)}${sparkline(t.spark)}</td>
          <td class="dir ${esc(dir)}" title="position ${esc(dir || '—')} · close fill ${esc(t.side_close)}">${esc(dir || '—')}</td>
          <td class="l b-flat" title="${esc(t.strategy_id)}">${esc(t.strategy_id)}</td>
          <td class="l b-flat" title="entry regime ${esc(reg || '—')}">${esc(reg || '—')}</td>
          <td class="num b-flat" title="entry ${fmtPxCcy(t.entry_price, qccy)}">${fmtPxCcy(t.entry_price, qccy)}</td>
          <td class="num b-flat" title="exit ${fmtPxCcy(t.exit_price, qccy)}">${fmtPxCcy(t.exit_price, qccy)}</td>
          <td class="num ${pn(t.pnl_usd)}" title="gross PnL (pre-fee)">${fmtUsd(t.pnl_usd, 2)}</td>
          <td class="num b-neg" title="real venue fee · demo ${fmtUsd(t.fee_usd, 4)}">${fmtUsd(-fee, 2)}</td>
          <td class="num ${pn(net)}" title="net = gross − fee">${fmtUsd(net, 2)}</td>
          <td class="num ${pn(t.pnl_pct)}" title="gross PnL as % of entry notional">${fmtSignedPct(t.pnl_pct, 2)}</td>
          <td class="num b-flat">${hms(t.held_sec)}</td>
          <td class="l b-flat" title="exit ${esc(t.exit_reason)}">${esc(t.exit_reason)}</td>
        </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:7%"><col style="width:5%"><col style="width:11%"><col style="width:5%">
        <col style="width:10%"><col style="width:8%"><col style="width:8%"><col style="width:8%">
        <col style="width:7%"><col style="width:6%"><col style="width:7%"><col style="width:5%">
        <col style="width:5%"><col style="width:6%">
       </colgroup><thead><tr>
        <th class="l">TIME</th><th class="l">VEN</th><th class="l">SYMBOL</th><th title="position direction (long/short) — not the close fill side">SIDE</th>
        <th class="l">STRAT</th><th class="l" title="regime at entry">REGIME</th><th>ENTRY</th><th>EXIT</th>
        <th title="gross PnL before fees">GROSS$</th><th title="real venue fee">FEE$</th><th title="net = gross − fee">NET$</th>
        <th title="gross PnL as % of entry notional">PnL%</th><th>HELD</th><th class="l">REASON</th>
      </tr></thead><tbody>${groups}</tbody></table>`;
  }

  // ── TAB 3 · REGIME ────────────────────────────────────────────────────────
  const REGIME_COLS = 8;
  function renderRegime(d) {
    const rows = venueFilter(d.regime_states);   // E3 venue scope
    const body = $('regime-body'); if (!body) return;
    setCnt('regime-body-cnt', rows.length);
    if (!rows.length) {
      const sc = scopeLabel();
      body.innerHTML = `<div class="empty">no regime state yet${sc ? ' · ' + esc(sc) : ''}</div>`;
      return;
    }
    const trs = rows.map(r => {
      const lc = venueStream(r.venue).toLowerCase();
      const confPct = Math.max(0, Math.min(100, (r.confidence || 0) * 100));
      const barCls = confPct >= 66 ? 'bar-pos' : confPct >= 33 ? 'bar-warn' : 'bar-neg';
      const ev = [];
      if (r.evidence_l1) ev.push(`<span class="chip ev-l1"><span class="cl">L1</span> ${esc(r.evidence_l1)}</span>`);
      if (r.evidence_l2) ev.push(`<span class="chip ev-l2"><span class="cl">L2</span> ${esc(r.evidence_l2)}</span>`);
      if (r.evidence_l3) ev.push(`<span class="chip ev-l3"><span class="cl">L3</span> ${esc(r.evidence_l3)}</span>`);
      const evHtml = ev.length ? ev.join(' ') : '<span class="b-flat">—</span>';
      const cons = (r.consecutive_candidate && r.consecutive_count)
        ? `${esc(r.consecutive_candidate)} ×${r.consecutive_count}` : '—';
      return `<tr class="row-${lc}">
        <td class="l ex">${esc(r.venue)}</td>
        <td class="l tk" title="${esc(r.group_id)}">${esc(r.group_id)}</td>
        <td class="l" style="color:var(--p-mag);font-weight:700">${esc((r.regime || '').replace(/_/g, ' '))}</td>
        <td class="num">${confPct.toFixed(0)}%</td>
        <td><span class="hbar ${barCls}"><i style="width:${confPct.toFixed(0)}%"></i></span></td>
        <td class="l">${evHtml}</td>
        <td class="l b-flat" title="2-consecutive hysteresis candidate">${cons}</td>
        <td class="num b-flat">${r.updated_ts ? hhmmss(r.updated_ts) : '—'}</td>
      </tr>`;
    }).join('');
    body.innerHTML =
      `<table><colgroup>
        <col style="width:7%"><col style="width:11%"><col style="width:13%"><col style="width:6%">
        <col style="width:14%"><col style="width:31%"><col style="width:12%"><col style="width:6%">
       </colgroup><thead><tr>
        <th class="l">VEN</th><th class="l">GROUP</th><th class="l">REGIME</th><th>CONF</th>
        <th class="l">—</th><th class="l">EVIDENCE (L1·L2·L3)</th><th class="l">2-CONSEC</th><th>UPD</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }

  // ── TAB 4 · STRATEGY ──────────────────────────────────────────────────────
  function renderStrategy(d) {
    // StrategyStat carries no venue — genuinely global. When scoped, show an
    // 'all venues' note rather than fabricating a per-venue split.
    const rows = d.strategy_stats || [];
    const body = $('strat-body'); if (!body) return;
    setCnt('strat-body-cnt', rows.length);
    if (!rows.length) { body.innerHTML = '<div class="empty">no strategy stats</div>'; return; }
    const sc = scopeLabel();
    const note = sc ? `<div class="empty" style="text-align:left;color:var(--p-dim)">scope ${esc(sc)} — strategy stats are cross-venue (all venues)</div>` : '';
    // per-(strategy×regime) confidence cells for the EV column.
    const cells = (d.confidence && d.confidence.cells) || [];
    const cellsByStrat = {};
    cells.forEach(c => { (cellsByStrat[c.strategy_id] = cellsByStrat[c.strategy_id] || []).push(c); });
    // SINCE-RESET (new logic) per-strategy forward edge — keyed by strategy_id.
    // Empty when no main-logic reset is stamped (the column shows '—' / all-time).
    const srByStrat = {};
    (d.strategy_since_reset || []).forEach(s => { srByStrat[s.strategy_id] = s; });
    const hasReset = !!d.since_reset;
    const trs = rows.map(s => {
      const cs = cellsByStrat[s.strategy_id] || [];
      const evHtml = cs.length ? cs.slice(0, 4).map(c => {
        const lcb = c.lcb_real_fee_net_r || 0;
        const cls = lcb > 0 ? 'b-pos' : lcb < 0 ? 'b-neg' : 'b-flat';
        return `<span class="chip" title="${esc(c.regime)} n=${c.n} E[R]=${(c.expected_real_fee_net_r||0).toFixed(2)} LCB=${lcb.toFixed(2)}">`
          + `<span class="cl">${esc(c.regime)}</span> <span class="${cls}">${c.lcb_sign}${Math.abs(lcb).toFixed(2)}R</span></span>`;
      }).join(' ') : '<span class="b-flat">—</span>';
      const pf = (s.pf >= 9.99) ? '∞' : (s.pf || 0).toFixed(2);
      // SINCE-RESET cell — the new-logic forward edge for this strategy (net$ +
      // n + WR). '—' when no reset is stamped or this strategy has no post-reset
      // trade yet — so the column reads as a distinct forward-window slice.
      const sr = srByStrat[s.strategy_id];
      const srHtml = !hasReset
        ? '<span class="b-flat" title="no main-logic reset stamped">—</span>'
        : sr
          ? `<span class="${pn(sr.net_usd)}" title="since reset: ${sr.n} trades · WR ${(sr.wr_pct || 0).toFixed(0)}% · PF ${(sr.pf >= 9.99 ? '∞' : (sr.pf || 0).toFixed(2))} · avgR ${(sr.avg_r || 0).toFixed(2)}">${fmtUsd(sr.net_usd, 0)}</span> <span class="b-flat" style="font-size:9px">n${sr.n} ${(sr.wr_pct || 0).toFixed(0)}%</span>`
          : '<span class="b-flat" title="no trade opened under new logic yet">—</span>';
      return `<tr>
        <td class="l tk" title="${esc(s.strategy_id)}">${esc(s.strategy_id)}</td>
        <td class="num b-flat">${s.open_n || 0}</td>
        <td class="num b-flat">${s.closed_n || 0}</td>
        <td class="num">${(s.wr_pct || 0).toFixed(1)}%</td>
        <td class="num b-flat">${pf}</td>
        <td class="num ${pn(s.avg_r)}">${fmtR(s.avg_r, 2)}</td>
        <td class="num ${pn(s.pnl_usd)}">${fmtUsd(s.pnl_usd, 2)}</td>
        <td class="num">${srHtml}</td>
        <td class="num b-flat">${fmtUsd(s.notional_usd, 0)}</td>
        <td class="l" title="per-regime real-fee-net LCB cells">${evHtml}</td>
      </tr>`;
    }).join('');
    body.innerHTML = note +
      `<table><colgroup>
        <col style="width:13%"><col style="width:5%"><col style="width:6%"><col style="width:6%">
        <col style="width:5%"><col style="width:7%"><col style="width:9%"><col style="width:13%">
        <col style="width:9%"><col style="width:27%">
       </colgroup><thead><tr>
        <th class="l">STRATEGY</th><th>OPEN</th><th>CLOSED</th><th>WR</th>
        <th>PF</th><th title="stream-common R: pnl_usd / R_budget (2% of stream starting equity) — comparable across venues; distinct from per-trade MFE/MAE-R">AVG-R</th><th>PnL$</th>
        <th title="SINCE RESET (new logic): net$ + trade count + WR over trades OPENED after the latest main-logic reset">SINCE RESET</th>
        <th>NOTIONAL</th>
        <th class="l" title="per-regime cell expectancy — real-fee-net LCB (+EV / -EV)">REGIME CELL EV</th>
      </tr></thead><tbody>${trs}</tbody></table>`;
  }


  // ── dispatch registry ─────────────────────────────────────────────────────
  // board_tabs.js owns the table renderers (positions / trades / regime /
  // strategy); board_tabs_ext.js owns the analytics renderers (exit / ai / edge
  // / risk) + the 5 new sub-renderers, and registers the 6 COMPOSITE tab
  // renderers (activity / performance / logic / build / path / learned) via
  // ``register``. The base table renderers are exposed on ``renderers`` so the
  // composites can reuse them. Unregistered tabs are a graceful no-op.
  const RENDERERS = {};
  const BASE = {
    positions: renderPositions,
    trades: renderTrades,
    regime: renderRegime,
    strategy: renderStrategy,
  };
  function register(which, fn) { RENDERERS[which] = fn; }
  function renderTab(which, d) {
    wireCollapsibles();   // idempotent — attaches the collapse listener once
    const fn = RENDERERS[which];
    if (fn) fn(d);
  }

  injectStyle();
  window.PolarisBoardTabs = {
    paneMarkup: paneMarkup,
    renderTab: renderTab,
    renderTabCounts: renderTabCounts,
    register: register,
    setCnt: setCnt,
    renderers: BASE,   // base table renderers for composite reuse (ext.js)
  };
})();
