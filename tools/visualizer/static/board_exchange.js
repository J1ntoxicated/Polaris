/* board_exchange.js — Polaris E3 exchange selector (display-only).
 *
 * The clickable 3-stream exchange selector + ALL toggle (Jin 2026-05-31). A
 * single client-side ``activeExchange`` state ('all'|'okx'|'capital'|'alpaca',
 * default 'all') that (a) SCOPES every tab below to the selected venue's data
 * (client-side filter — no snapshot/pipeline change) and (b) FOCUSES the globe
 * via ``window.PolarisGlobe.focusExchange`` (globe-core.js). The 3-stream
 * summary strip itself always shows all 3 lanes; the selection only filters
 * what is BELOW + the globe.
 *
 * Loaded AFTER board.js (depends on ``window.PolarisBoard``) and BEFORE the tab
 * modules (which read the scope helpers it registers onto ``window.PolarisBoard``).
 * Pure DOM + state — no fetch / sizing / order / exit logic.
 */
(function () {
  'use strict';

  const B = window.PolarisBoard;
  if (!B) { return; }   // board.js must load first
  const { $, esc } = B;

  // ── style (selector chips + clickable-lane highlight) ─────────────────────
  const CSS = `
  /* Exchange selector — segmented control above the streams strip.
     ALL · OKX · CAPITAL · ALPACA. Scopes the tabs below + the globe focus.
     Display-only; the streams strip stays fully visible. */
  #board .xsel { display: flex; gap: 4px; align-items: center; flex-wrap: wrap; }
  #board .xsel .xsel-lbl {
    color: var(--p-dim); font-size: 9px; letter-spacing: 0.14em; text-transform: uppercase;
    margin-right: 4px;
  }
  #board .xsel .xchip {
    appearance: none; cursor: pointer;
    background: rgba(15,19,26,0.55);
    border: 1px solid rgba(95,135,175,0.22);
    color: var(--p-dim);
    font-family: var(--font-mono); font-size: 10px; font-weight: 700;
    letter-spacing: 0.12em; text-transform: uppercase;
    padding: 3px 11px;
  }
  #board .xsel .xchip:hover { color: var(--p-wht); }
  #board .xsel .xchip.active {
    color: var(--polaris-blue); border-color: var(--polaris-blue);
    background: rgba(95,135,175,0.12);
  }
  #board .xsel .xchip.x-okx.active     { color: var(--stream-a); border-color: var(--stream-a); background: rgba(95,223,255,0.10); }
  #board .xsel .xchip.x-capital.active { color: var(--stream-b); border-color: var(--stream-b); background: rgba(168,124,255,0.10); }
  #board .xsel .xchip.x-alpaca.active  { color: var(--stream-c); border-color: var(--stream-c); background: rgba(255,200,79,0.10); }

  /* Lane cards are clickable selectors. Selected lane = glowing border. */
  #board .lane { cursor: pointer; transition: box-shadow 0.15s ease, border-color 0.15s ease; }
  #board .lane.x-active {
    border-color: var(--polaris-blue);
    box-shadow: 0 0 0 1px var(--polaris-blue), 0 0 14px rgba(95,135,175,0.35);
  }
  #board .lane.lane-a.x-active { box-shadow: 0 0 0 1px var(--stream-a), 0 0 14px rgba(95,223,255,0.40); }
  #board .lane.lane-b.x-active { box-shadow: 0 0 0 1px var(--stream-b), 0 0 14px rgba(168,124,255,0.40); }
  #board .lane.lane-c.x-active { box-shadow: 0 0 0 1px var(--stream-c), 0 0 14px rgba(255,200,79,0.40); }
  /* When a non-ALL exchange is selected, the unselected lanes recede a touch. */
  #board .streams-strip.x-scoped .lane:not(.x-active) { opacity: 0.55; }
  `;
  (function injectStyle() {
    const s = document.createElement('style');
    s.id = 'board-exchange-style';
    s.textContent = CSS;
    document.head.appendChild(s);
  })();

  // ── state (persists across the 1s poll — module scope, never reset) ───────
  let _activeExchange = 'all';   // 'all'|'okx'|'capital'|'alpaca'
  function getActiveExchange() { return _activeExchange; }
  // Normalize a venue/exchange token. Tolerates full ('capital') + short ('cap')
  // forms — edge/cell rows carry full venue strings, graph short codes use the
  // 3-letter prefix.
  function normVenue(v) {
    const m = String(v || '').toLowerCase();
    if (m === 'okx') return 'okx';
    if (m === 'cap' || m === 'capital') return 'capital';
    if (m === 'alp' || m === 'alpaca') return 'alpaca';
    if (m === 'bin' || m === 'binance') return 'binance';
    return m || 'other';
  }
  // True if a row's venue is in scope (always true on 'all'). Tab renderers use
  // this to filter rows by venue, client-side.
  function venueMatches(venue) {
    if (_activeExchange === 'all') return true;
    return normVenue(venue) === _activeExchange;
  }
  // Filter a row list by the active venue (same array on 'all'). ``key`` selects
  // the venue-carrying field (default 'venue'; edge/cell rows use 'exchange').
  function venueFilter(rows, key) {
    if (_activeExchange === 'all') return rows || [];
    const k = key || 'venue';
    return (rows || []).filter(r => r && venueMatches(r[k]));
  }

  // ── selection + UI sync ───────────────────────────────────────────────────
  function setActiveExchange(which) {
    const w = (which === 'okx' || which === 'capital' || which === 'alpaca') ? which : 'all';
    if (w === _activeExchange) return;
    _activeExchange = w;
    syncExchangeUi();
    if (window.PolarisGlobe && window.PolarisGlobe.focusExchange) {
      window.PolarisGlobe.focusExchange(w);   // globe focus (dim non-matching wedge)
    }
    // Immediate re-render (streams highlight + the visible tab) — no poll wait.
    if (B.rerenderActive) B.rerenderActive();
  }
  // Reflect the active selection in the chips + lane highlight (no data change).
  // Called by board.js after every renderStreams (innerHTML rewrite clears it).
  function syncExchangeUi() {
    const sel = $('b-xsel');
    if (sel) {
      sel.querySelectorAll('.xchip').forEach(b =>
        b.classList.toggle('active', b.getAttribute('data-ex') === _activeExchange));
    }
    const strip = $('b-streams');
    if (strip) {
      strip.classList.toggle('x-scoped', _activeExchange !== 'all');
      strip.querySelectorAll('.lane').forEach(l =>
        l.classList.toggle('x-active',
          _activeExchange !== 'all' && l.getAttribute('data-ex') === _activeExchange));
    }
  }
  function initExchangeSelector() {
    const sel = $('b-xsel');
    if (sel) {
      sel.addEventListener('click', (e) => {
        const btn = e.target.closest('.xchip');
        if (btn) setActiveExchange(btn.getAttribute('data-ex'));
      });
    }
    // Lane cards (re-rendered each poll) — delegate from the strip container.
    const strip = $('b-streams');
    if (strip) {
      strip.addEventListener('click', (e) => {
        const lane = e.target.closest('.lane');
        if (!lane) return;
        const ex = lane.getAttribute('data-ex');
        // Click the active lane again → back to ALL (toggle off).
        setActiveExchange(ex === _activeExchange ? 'all' : ex);
      });
    }
  }

  // ── selector markup (chip strip) — board.js skeleton injects this ─────────
  function selectorMarkup() {
    return `<div class="xsel" id="b-xsel">
      <span class="xsel-lbl">scope</span>
      <button type="button" class="xchip x-all active" data-ex="all">ALL</button>
      <button type="button" class="xchip x-okx" data-ex="okx">OKX</button>
      <button type="button" class="xchip x-capital" data-ex="capital">CAPITAL</button>
      <button type="button" class="xchip x-alpaca" data-ex="alpaca">ALPACA</button>
    </div>`;
  }

  // Register the scope helpers onto window.PolarisBoard so the tab modules (which
  // load after this file) can filter their rows by the active venue.
  B.getActiveExchange = getActiveExchange;
  B.venueMatches = venueMatches;
  B.venueFilter = venueFilter;

  window.PolarisBoardExchange = {
    selectorMarkup: selectorMarkup,
    initExchangeSelector: initExchangeSelector,
    syncExchangeUi: syncExchangeUi,
    // E4: globe galaxy click syncs the board scope (calls setActiveExchange,
    // which in turn re-issues focusExchange — idempotent, no loop).
    setActiveExchange: setActiveExchange,
  };
})();
