// Polaris mobile page — lightweight, independent of the desktop board_*.js.
// Polls /api/snapshot every 1s (matches desktop; snapshot is ~1ms + upnl is
// recomputed live from latest prices, so P&L ticks in real time) and paints a
// single-column iPhone view focused on
// the two things Jin watches on the phone: OPEN POSITIONS + live GATE ACTIVITY.
// A compact status strip (equity + today P&L) sits above; the equity/win-rate/PF/
// drawdown stat block and the venues/recent-trades lists are intentionally dropped
// to keep the screen scannable. DEMO/PAPER, display-only. English chrome; live
// data (symbols/strategy ids) may be venue-native. No new colors — #board palette.
'use strict';

(function () {
  var POLL_MS = 1000;
  var $ = function (id) { return document.getElementById(id); };

  function usd(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var sign = n < 0 ? '-' : '';
    var a = Math.abs(n);
    return sign + '$' + a.toLocaleString('en-US', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }
  function signed(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    // usd() already prefixes '-$...' for negatives — keep the minus so losses
    // read as losses; only add '+' for non-negatives.
    return n >= 0 ? '+' + usd(n) : usd(n);
  }
  function pnlClass(n) {
    if (!n) return 'flat';
    return n > 0 ? 'pos' : 'neg';
  }
  // Compact USD for exposure (size_usd): 1580 → "$1.6k", 158000 → "$158k", 950 → "$950".
  function kusd(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var sign = n < 0 ? '-' : '';
    var a = Math.abs(n);
    if (a >= 1000) return sign + '$' + (a / 1000).toFixed(a >= 10000 ? 0 : 1) + 'k';
    return sign + '$' + a.toFixed(0);
  }
  // Price with scale-adaptive precision (BTC 108250 · AUDUSD 0.6543 · $5.02).
  function fmtPx(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    var a = Math.abs(n);
    var dp = a >= 1000 ? 0 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6;
    return n.toFixed(dp);
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  // Same 8-hue cool->warm progression as wall_spine.js's / wall_console_
  // readouts.js's / globe-funnel.js's GATE_COLORS (Jin 2026-07-16 P4b) —
  // duplicated here rather than plumbed cross-module for 8 stable literal hex
  // strings (established precedent in this codebase — see the identical
  // duplication comment in wall_console_readouts.js): shared visual language,
  // not runtime state. G1..G8 now read their own hue instead of one flat cyan.
  var GATE_COLORS = ['#5fa8ff', '#5fdfff', '#6fffc4', '#9dff6f', '#ffe066', '#ffb454', '#ff7a9e', '#c48aff'];
  function gateColor(gateId) {
    var i = ((+gateId || 1) - 1) % 8;
    return GATE_COLORS[i < 0 ? i + 8 : i];
  }
  // Stable per-row key — venue|symbol|strategy|side. Shared by the position
  // GROUP fold (below), the /stream/prices SSE flash key, and the Chart tap-
  // to-pin selection (rowKey === the SSE key already stamped as data-key).
  function rowKey(p) {
    return [p.venue, p.symbol, p.strategy_id, p.side].join('|');
  }
  // Venue normalizer + short tag — mirrors board_exchange.normVenue (mobile.js is
  // standalone, no board_*.js import). Tolerates full ('capital') + short ('cap')
  // forms. Returns {key, tag}: key drives the --stream-a/b/c colour class
  // (v-okx/v-capital/v-alpaca), tag is the uppercase chip text (OKX/CAP/ALP).
  function venueInfo(v) {
    var m = String(v || '').toLowerCase();
    if (m === 'okx') return { key: 'okx', tag: 'OKX' };
    if (m === 'cap' || m === 'capital') return { key: 'capital', tag: 'CAP' };
    if (m === 'alp' || m === 'alpaca') return { key: 'alpaca', tag: 'ALP' };
    return { key: 'other', tag: (m ? m.slice(0, 3).toUpperCase() : '—') };
  }
  // hh:mm:ss — matches the desktop gate feed (board_tabs_ext hhmmss).
  function hhmmss(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    var hh = ('0' + d.getHours()).slice(-2);
    var mm = ('0' + d.getMinutes()).slice(-2);
    var ss = ('0' + d.getSeconds()).slice(-2);
    return hh + ':' + mm + ':' + ss;
  }

  // Verdict mapping mirrors desktop board_tabs_ext.gateVerdict — keep the
  // verbatim decision verb, colour by FAMILY (flow_not_block: gates emit
  // decisions, not pass/kill). green pass-through / cyan HOLD / amber intervene
  // / red hard exit.
  function gateVerdict(e) {
    var raw = String(e.decision || e.verdict || e.action || '').toUpperCase();
    if (!raw) return { txt: '—', cls: 'other' };
    if (/EXIT_NOW|KILL|REJECT|STOP_HIT/.test(raw)) return { txt: raw, cls: 'kill' };
    if (/ADJUST|WIDEN|SWAP/.test(raw)) return { txt: raw, cls: 'other' };
    if (/PASS|PROCEED|SIZED|REFLECTED|ADMIT|VALID|ENTER/.test(raw)) return { txt: raw, cls: 'pass' };
    if (/HOLD/.test(raw)) return { txt: raw, cls: 'info' };
    return { txt: raw, cls: 'other' };
  }

  // Clean trading symbol from a probe row. Backend parses
  // pos_<hash>_<venue>_<SYMBOL>_<ts> now, but defend in JS so a raw 40-char id
  // never leaks into a cell. Returns {sym, venue}.
  var POS_ID_RE = /_(okx|capital|alpaca)_(.+)_\d+$/i;
  function probeSym(pr) {
    var raw = String(pr.ticker || pr.symbol || '');
    var venue = pr.venue || '';
    var sym = raw;
    var m = raw.match(POS_ID_RE);
    if (m) { if (!venue) venue = m[1].toLowerCase(); sym = m[2]; }
    return { sym: sym, venue: venue };
  }
  // Normalize the probe stream into clean rows (NOT merged into the gate feed —
  // probes get their own section). Returns [] when absent (graceful).
  function probeRows(s) {
    var out = [];
    var pe = s.probe_events || s.probes || [];
    for (var i = 0; i < pe.length; i++) {
      var pr = pe[i] || {};
      var vs = probeSym(pr);
      out.push({
        name: pr.name || pr.probe || pr.id || 'probe',
        sym: vs.sym, venue: vs.venue,
        kind: pr.kind || '',
        lean: (pr.lean != null) ? +pr.lean : null,
        conf: (pr.confidence != null) ? +pr.confidence : null,
        action: pr.action || '',
        ts: pr.ts || pr.ts_unix || 0,
      });
    }
    return out;
  }

  // SINCE RESET — forward edge since the latest main-logic reset. Mirrors the
  // desktop board sinceResetHtml, condensed to one phone line. Hidden when no
  // reset is stamped. Net/PF colour by winning (pf>=1 or net>=0).
  // VIRTUAL mode (Jin 2026-07-08 dashboard-live-net fix): reads
  // ``virtual_since_reset`` (fresh $100k×3 ledger, per-venue anchor) instead
  // of the LEGACY ``since_reset`` (unfiltered fills scan, pre-virtual history
  // mixed in) — this is the main board Jin actually watches on the phone, so
  // it must show the SAME scope the mode banner claims, never the legacy one.
  function paintSinceReset(s) {
    var el = $('sreset'); if (!el) return;
    var virt = !!s.virtual_account_enabled;
    var sr = virt ? s.virtual_since_reset : s.since_reset;
    if (!sr) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    var pf = (sr.pf >= 9.99) ? '∞' : (sr.pf || 0).toFixed(2);
    var winning = (sr.pf != null && sr.pf >= 1) || (sr.net_usd >= 0);
    var netCls = winning ? 'pos' : 'neg';
    var lbl = virt ? 'virtual ledger' : (sr.label ? esc(sr.label) : 'reset');
    var tag = virt ? 'SINCE VIRTUAL RESET' : 'SINCE RESET';
    // Board-parity (Jin 2026-07-10 "mobile out of sync with the board"): same
    // metric set as the desktop SINCE line (adds Avg-R) + Session when it
    // differs from Today — one wording, one metric set on both surfaces.
    var avgR = sr.avg_r == null ? '—'
      : (sr.avg_r >= 0 ? '+' : '') + sr.avg_r.toFixed(2) + 'R';
    var daily = virt ? s.virtual_daily_pnl_usd : s.daily_pnl_usd;
    var session = virt ? s.virtual_session_pnl_usd : s.session_pnl_usd;
    var sessionDiffers = Math.abs((session || 0) - (daily || 0)) > 0.005;
    el.innerHTML =
      '<span class="tag">' + tag + '</span>' +
      '<span class="lbl" title="' + lbl + '">' + lbl + '</span>' +
      '<span class="kv">Net<b class="' + netCls + '">' + signed(sr.net_usd) + '</b></span>' +
      '<span class="kv">Trades<b>' + (sr.n || 0) + '</b></span>' +
      '<span class="kv">Win<b>' + (sr.win_pct || 0).toFixed(0) + '%</b></span>' +
      '<span class="kv">PF<b class="' + netCls + '">' + pf + '</b></span>' +
      '<span class="kv">Avg-R<b class="' + pnlClass(sr.avg_r) + '">' + avgR + '</b></span>' +
      (sessionDiffers
        ? '<span class="kv">Session<b class="' + pnlClass(session) + '">' + signed(session) + '</b></span>'
        : '');
  }

  // Recent Trades — TIME · SYM · side · P&L · R · why (newest first).
  function paintRecentTrades(s) {
    var body = $('recenttrades'); if (!body) return;
    var rt = (s.recent_trades || s.trades || []).slice()
      .sort(function (a, b) { return (b.ts_close || 0) - (a.ts_close || 0); });
    if ($('rt-cnt')) $('rt-cnt').textContent = rt.length || '';
    if (!rt.length) { body.innerHTML = '<div class="empty">no closed trades yet</div>'; return; }
    var hdr = '<div class="thr rt-grid"><span></span><span>TIME</span><span>SYM</span><span class="ta-c">S</span>' +
      '<span class="ta-r">P&amp;L</span><span class="ta-r">R</span><span class="ta-r">WHY</span></div>';
    body.innerHTML = hdr + rt.slice(0, 60).map(function (t) {
      var tstr = t.ts_close ? hhmmss(t.ts_close) : '';
      // issue 2: tag by POSITION direction (long/short), not the close FILL
      // side (sell = a long exit, which mislabelled longs as shorts). Fall back
      // to the close-side inference when position_side is absent (legacy rows).
      var ps = String(t.position_side || '').toLowerCase();
      var isShort = ps ? (ps.charAt(0) === 's')
        : (String(t.side_close || '').toLowerCase().charAt(0) === 'b');
      var sideTag = isShort ? 'S' : 'L';
      var r = (t.r_units != null) ? ((t.r_units >= 0 ? '+' : '') + t.r_units.toFixed(2) + 'R') : '—';
      // Jin 2026-07-16 P4b: venue was previously absent from every visible
      // cell (only in the title tooltip) — a leading .vchip fixes that, same
      // chip already used on Open Positions.
      var vi = venueInfo(t.venue);
      return '<div class="tr rt-grid" title="' + esc(t.venue) + ' ' + esc(t.symbol) + ' ' + esc(t.strategy_id) +
          ' · ' + esc(t.exit_reason) + ' · ' + esc(tstr) + '">' +
        '<span class="vchip v-' + vi.key + '">' + esc(vi.tag) + '</span>' +
        '<span class="t num">' + esc(tstr) + '</span>' +
        '<span class="sym" title="' + esc(t.symbol) + '">' + esc(t.symbol) + '</span>' +
        '<span class="s ' + (sideTag === 'S' ? 'side-short' : 'side-long') + '">' + sideTag + '</span>' +
        '<span class="pl num ' + pnlClass(t.pnl_usd) + '">' + signed(t.pnl_usd) + '</span>' +
        '<span class="r num">' + r + '</span>' +
        '<span class="why" title="' + esc(t.exit_reason) + '">' + esc(t.exit_reason || '—') + '</span>' +
        '</div>';
    }).join('');
  }

  // LOGIC tab — per-strategy edge + learners + regime, all from the snapshot.
  function paintLogic(s) {
    var sl = $('stratlist');
    if (sl) {
      var ss = (s.strategy_stats || []).slice()
        .sort(function (a, b) { return (b.pnl_usd || 0) - (a.pnl_usd || 0); });
      if ($('strat-cnt')) $('strat-cnt').textContent = ss.length || '';
      if (!ss.length) { sl.innerHTML = '<div class="empty">no strategy stats</div>'; }
      else {
        var sh = '<div class="thr kv-grid"><span>STRATEGY</span><span class="ta-r">WR</span><span class="ta-r">PF</span><span class="ta-r">P&amp;L</span></div>';
        sl.innerHTML = sh + ss.map(function (x) {
          var pf = (x.pf >= 9.99) ? '∞' : (x.pf || 0).toFixed(1);
          return '<div class="tr kv-grid" title="' + esc(x.strategy_id) + ' · n=' + (x.closed_n || 0) + '">' +
            '<span class="nm" title="' + esc(x.strategy_id) + '">' + esc(x.strategy_id) + '</span>' +
            '<span class="a num">' + (x.wr_pct || 0).toFixed(0) + '%</span>' +
            '<span class="b num">' + pf + '</span>' +
            '<span class="c num ' + pnlClass(x.pnl_usd) + '">' + signed(x.pnl_usd) + '</span>' +
            '</div>';
        }).join('');
      }
    }
    var ll = $('lrnlist');
    if (ll) {
      var lr = s.learners || [];
      if ($('lrn-cnt')) $('lrn-cnt').textContent = lr.length || '';
      if (!lr.length) { ll.innerHTML = '<div class="empty">no learners</div>'; }
      else {
        var lh = '<div class="thr kv-grid"><span>LEARNER</span><span class="ta-r">VAL</span><span class="ta-r">Δ1h</span><span class="ta-r">n</span></div>';
        ll.innerHTML = lh + lr.map(function (x) {
          return '<div class="tr kv-grid" title="' + esc(x.learner_id) + ' ' + esc(x.key) + '">' +
            '<span class="nm" title="' + esc(x.key) + '">' + esc(x.key || x.learner_id) + '</span>' +
            '<span class="a num">' + (+x.value || 0).toFixed(2) + '</span>' +
            '<span class="b num ' + pnlClass(x.delta_1h) + '">' + ((x.delta_1h >= 0 ? '+' : '') + (+x.delta_1h || 0).toFixed(2)) + '</span>' +
            '<span class="c num">' + Math.round(x.n_eff || 0) + '</span>' +
            '</div>';
        }).join('');
      }
    }
    var rl = $('rgmlist');
    if (rl) {
      var rb = (s.regime_bars || []).slice().sort(function (a, b) { return (b.count || 0) - (a.count || 0); });
      if ($('rgm-cnt')) $('rgm-cnt').textContent = rb.length || '';
      if (!rb.length) { rl.innerHTML = '<div class="empty">no regime data</div>'; }
      else {
        rl.innerHTML = rb.slice(0, 20).map(function (x) {
          return '<div class="li"><span class="what">' + esc(x.regime || '—') + '</span>' +
            '<span class="when num">' + (x.count || 0) + '</span></div>';
        }).join('');
      }
    }
  }

  // AI tab (Jin 2026-07-10 restructure) — 3 sections (bot / harness / cowork
  // intel), fetch-once from GET /api/ai_activity like Build/Roadmap below
  // (loadAi(), wired in showTab()) rather than the 1s snapshot poll — the
  // AI tab's own data lives behind its own endpoint, not `s`.

  // ── Open Positions GROUP fold (Jin 2026-07-16 P4b — ported from
  // wall_side.js grpIndex/expanded): >1 position sharing venue|symbol folds
  // to a Σ header, tap to expand, persisted in localStorage under its own
  // mobile-scoped key (separate from /flow's — the two panes can differ). ──
  var _lastSnap = null;   // last /api/snapshot payload — re-render on tap w/o waiting for the next 1s poll
  var POS_GRP_KEY = 'm.grp.open';
  var expandedGrp = new Set();
  try { expandedGrp = new Set(JSON.parse(localStorage.getItem(POS_GRP_KEY) || '[]')); } catch (e) { /* private mode */ }
  function saveExpandedGrp() {
    try { localStorage.setItem(POS_GRP_KEY, JSON.stringify(Array.from(expandedGrp))); } catch (e) { /* private mode */ }
  }

  // ── Inspect chart (Jin 2026-07-16 P4b — ported from wall_side.js
  // CHART_STATE/chartPick): default = auto-follow the most-recently-opened
  // position (min held_sec); tapping a position row pins it until a
  // genuinely NEW top position appears, which unpins back to auto-follow.
  // Reuses chart_inspect.js's buildChartSvg (P4a) — same glance-only chart
  // as /flow's side panel. ──
  var CHART_STATE = { sel: null, topKey: null };
  function chartPick(all) {
    if (!all.length) { CHART_STATE.topKey = null; return null; }
    var top = all[0];
    for (var i = 1; i < all.length; i++) {
      var hs = all[i].held_sec == null ? Infinity : all[i].held_sec;
      var th = top.held_sec == null ? Infinity : top.held_sec;
      if (hs < th) top = all[i];
    }
    var tk = rowKey(top);
    if (tk !== CHART_STATE.topKey) { CHART_STATE.topKey = tk; CHART_STATE.sel = null; }
    if (CHART_STATE.sel) {
      for (var j = 0; j < all.length; j++) { if (rowKey(all[j]) === CHART_STATE.sel) return all[j]; }
      CHART_STATE.sel = null; // pinned position closed — fall back to auto-follow
    }
    return top;
  }
  function regimeLabel(p) {
    var a = p.entry_regime, b = p.regime;
    if (a && b && a !== b) return esc(a) + ' → ' + esc(b);
    return esc(b || a || '');
  }
  function renderChart(p) {
    var hdr = $('chart-hdr'), body = $('chart-body');
    if (!hdr || !body) return;
    if (!p) {
      hdr.innerHTML = '';
      body.innerHTML = '<div class="side-chart-empty">no open position</div>';
      return;
    }
    var u = p.upnl_usd != null ? p.upnl_usd : p.pnl_usd;
    var vi = venueInfo(p.venue);
    var sideCls = (String(p.side || '').toLowerCase().indexOf('s') === 0) ? 'side-short' : 'side-long';
    hdr.innerHTML =
      '<span class="vchip v-' + vi.key + '" title="' + esc(p.venue) + '">' + esc(vi.tag) + '</span>' +
      '<span class="sym">' + esc(String(p.symbol || '')) + '</span>' +
      (p.strategy_id ? '<span class="strat">' + esc(p.strategy_id) + '</span>' : '') +
      '<span class="side ' + sideCls + '">' + esc(String(p.side || '').toUpperCase()) + '</span>' +
      '<span class="num ' + pnlClass(u) + '">' + signed(u) + '</span>' +
      '<span class="reg">' + regimeLabel(p) + '</span>';
    body.innerHTML = ((window.PolarisChartInspect || {}).buildChartSvg
      || function () { return ''; })(p); // load guard (P4b review LOW)
  }

  // ── Open Positions — grouped by venue|symbol, chip · symbol · side ·
  // current(live) · profit · % · exp. Split out of paint() so a group-toggle
  // / chart-pin tap can re-render immediately from `_lastSnap` instead of
  // waiting up to 1s for the next poll. ──
  function renderPositions(s) {
    _lastSnap = s;
    var positions = $('positions');
    var ps = s.positions || [];
    $('pos-cnt').textContent = ps.length || '';
    if (!ps.length) {
      positions.innerHTML = '<div class="empty">No open positions</div>';
      renderChart(null);
      return;
    }
    var groups = {};
    ps.forEach(function (p) {
      var gk = String(p.venue || '') + '|' + String(p.symbol || '');
      (groups[gk] = groups[gk] || []).push(p);
    });
    var ordered = Object.keys(groups).map(function (gk) {
      var g = groups[gk];
      var upnl = g.reduce(function (a, p) { return a + (p.upnl_usd || 0); }, 0);
      g.sort(function (a, b) { return Math.abs(b.upnl_usd || 0) - Math.abs(a.upnl_usd || 0); });
      return { gk: gk, g: g, upnl: upnl };
    }).sort(function (a, b) { return Math.abs(b.upnl) - Math.abs(a.upnl); });

    var selP = chartPick(ps);
    var selKey = selP ? rowKey(selP) : null;

    // visible list: group>1 = header (+ children when expanded), single = row.
    var visible = [];
    ordered.forEach(function (o) {
      if (o.g.length > 1) {
        visible.push({ hdr: o });
        if (expandedGrp.has(o.gk)) o.g.forEach(function (p) { visible.push({ row: p, ingrp: true }); });
      } else {
        visible.push({ row: o.g[0] });
      }
    });
    visible = visible.slice(0, 30);

    var posHdr = '<div class="thr pos-grid">' +
      '<span></span><span>SYM</span><span class="ta-c">S</span>' +
      '<span class="ta-r">CUR</span><span class="ta-r">P&amp;L</span>' +
      '<span class="ta-r">%</span><span class="ta-r">EXP</span></div>';

    positions.innerHTML = posHdr + visible.map(function (v) {
      if (v.hdr) {
        var o = v.hdr;
        var open = expandedGrp.has(o.gk);
        var vi0 = venueInfo(o.g[0].venue);
        var gsz = o.g.reduce(function (a, p) { return a + (p.size_usd || 0); }, 0);
        return '<div class="tr pos-grid grp" data-g="' + esc(o.gk) + '" title="tap to ' + (open ? 'collapse' : 'expand') + '">' +
          '<span class="caret">' + (open ? '▾' : '▸') + '</span>' +
          '<span class="a" title="' + esc(o.g[0].symbol) + '">' + esc(vi0.tag) + ' ' + esc(o.g[0].symbol) + ' ×' + o.g.length + '</span>' +
          '<span></span><span></span>' +
          '<span class="sp num ta-r ' + pnlClass(o.upnl) + '">' + signed(o.upnl) + '</span>' +
          '<span></span>' +
          '<span class="exp num ta-r" title="exposure (size USD)">' + kusd(gsz) + '</span>' +
          '</div>';
      }
      var p = v.row;
      var sideCls = (String(p.side || '').toLowerCase().indexOf('s') === 0 ||
        String(p.side || '').toLowerCase() === 'short') ? 'side-short' : 'side-long';
      var vi = venueInfo(p.venue);
      var pct = (p.upnl_pct || 0);
      var sideTag = (String(p.side || '').toLowerCase().charAt(0) === 's') ? 'S' : 'L';
      // data-key matches the server /stream/prices key (venue|symbol|strategy|side)
      // so streamed cell pushes flash the live CUR / P&L / % cells of THIS row,
      // and doubles as the Chart tap-to-pin selection key.
      var key = rowKey(p);
      return '<div class="tr row pos pos-grid' + (v.ingrp ? ' ingrp' : '') + (key === selKey ? ' sel' : '') + '" data-key="' + esc(key) + '">' +
        '<span class="vchip v-' + vi.key + '" title="' + esc(p.venue) + '">' + esc(vi.tag) + '</span>' +
        '<span class="a" title="' + esc(p.symbol) + '">' + esc(p.symbol) + '</span>' +
        '<span class="b ' + sideCls + '" title="' + esc(p.side) + '">' + sideTag + '</span>' +
        '<span class="pxc num" title="current (live)">' + fmtPx(p.last_price) + '</span>' +
        '<span class="sp num ' + pnlClass(p.upnl_usd) + '">' + signed(p.upnl_usd) + '</span>' +
        '<span class="pct num ' + pnlClass(pct) + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%</span>' +
        '<span class="exp num" title="exposure (size USD)">' + kusd(p.size_usd) + '</span>' +
        '</div>';
    }).join('');

    renderChart(selP);
  }

  function paint(s) {
    // Header status — alive if we have a snapshot.
    $('dot').classList.add('live');
    $('status').textContent = 'live';
    paintSinceReset(s);
    paintRecentTrades(s);
    paintLogic(s);

    // Compact status strip: equity + today P&L (small, not a hero block).
    // VIRTUAL mode (Jin 2026-07-08 dashboard-live-net fix): shows the fresh
    // $100k×3 virtual ledger (Σ streams[].virtual_equity_usd / virtual_daily_
    // pnl_usd) instead of the LEGACY equity_now/daily_pnl_usd (an unfiltered
    // fills-table scan that still mixes in the pre-virtual real-roundtrip
    // history) — this status strip is the main board Jin actually watches on
    // the phone, so it must match the mode banner's claimed scope. REAL mode
    // (virtual OFF) stays byte-identical to before this change.
    var virt = !!s.virtual_account_enabled;
    var eq = $('equity');
    var eqSub = $('equity-sub');
    var pnlVal;
    if (virt) {
      var streams = s.streams || [];
      var virtEq = streams.reduce(function (a, x) { return a + (x.virtual_equity_usd != null ? x.virtual_equity_usd : 0); }, 0);
      var virtSeed = streams.reduce(function (a, x) { return a + (x.virtual_seed_usd != null ? x.virtual_seed_usd : 0); }, 0);
      eq.textContent = usd(virtEq) + ' virtual';
      eq.title = 'VIRTUAL $100k x 3 ledger — zero venue calls, seed ' + usd(virtSeed);
      if (eqSub) eqSub.textContent = 'seed ' + usd(virtSeed);
      pnlVal = s.virtual_daily_pnl_usd;
    } else {
      // Show BOTH: demo (70bps fee-burdened) · real-fee-net (go-live truth at 10bps).
      eq.textContent = usd(s.equity_now) + ' demo';
      eq.title = 'OKX demo charges 70bps (7x real); real-fee-net = equity at live 10bps fees';
      if (eqSub) eqSub.textContent = usd(s.equity_now_real_fee_net) + ' real-fee-net';
      pnlVal = s.daily_pnl_usd;
    }
    var pnl = $('pnl');
    pnl.textContent = signed(pnlVal);
    pnl.className = 'v num ' + pnlClass(pnlVal);
    var pnlPct = $('pnl-pct');
    if (pnlPct) {
      // Board-parity (Jin 2026-07-10): the desktop board denominates Today %
      // on the SEED (virt) / starting capital (real) — the old day-start-
      // equity base here made the same $ figure show a different % than the
      // board. One ruler on both surfaces.
      var pctBase = virt
        ? (s.streams || []).reduce(function (a, x) { return a + (x.virtual_seed_usd || 0); }, 0)
        : (s.starting_capital || 0);
      var dp = pctBase > 0 ? (pnlVal / pctBase) * 100 : 0;
      pnlPct.textContent = (dp >= 0 ? '+' : '') + dp.toFixed(2) + '%';
      pnlPct.className = 'sub num ' + pnlClass(dp);
    }

    // ── Open Positions + Chart — see renderPositions (group fold + tap-to-
    // pin chart, split out so a tap can re-render immediately). ──
    renderPositions(s);

    // ── Gate Decisions — G# · what it decided · n (aligned table). ──
    var gdec = $('gatedec');
    var gd = s.gate_decisions || [];
    if ($('gd-cnt')) $('gd-cnt').textContent = gd.length || '';
    if (gdec) {
      if (!gd.length) {
        gdec.innerHTML = '<div class="empty">no gate decisions</div>';
      } else {
        var gdHdr = '<div class="thr gd-grid"><span>G#</span><span>DECIDED</span><span class="ta-r">n</span></div>';
        gdec.innerHTML = gdHdr + gd.map(function (g) {
          return '<div class="tr gd-grid" title="G' + esc(g.gate_id) + ' ' + esc(g.label) + ' · ' + esc(g.headline) + '">' +
            '<span class="g" style="color:' + gateColor(g.gate_id) + '">G' + esc(g.gate_id) + '</span>' +
            '<span class="hl">' + esc(g.headline || '—') + '</span>' +
            '<span class="n num">' + esc(g.n) + '</span>' +
            '</div>';
        }).join('');
      }
    }

    // ── Gate Activity — gate events only (probes de-merged to own section).
    // Aligned grid: TIME · G# · DECISION · SYMBOL, newest first. ──
    var feed = $('gatefeed');
    var ge = (s.recent_gate_events || []).slice();
    $('gate-cnt').textContent = ge.length || '';
    if (!ge.length) {
      feed.innerHTML = '<div class="empty">no gate events yet</div>';
    } else {
      ge.sort(function (a, b) {
        return (b.ts || b.ts_unix || 0) - (a.ts || a.ts_unix || 0);
      });
      var gaHdr = '<div class="thr ga-grid"><span>TIME</span><span>G#</span><span>DECIDED</span><span>SYM</span></div>';
      feed.innerHTML = gaHdr + ge.slice(0, 60).map(function (e) {
        var sym = e.symbol || e.ticker || '';
        var ts = e.ts || e.ts_unix;
        var tstr = ts ? hhmmss(ts) : '';
        var v = gateVerdict(e);
        var g = (e.gate_id != null) ? ('G' + e.gate_id) : 'G?';
        var strat = e.strategy_id || e.strategy || '';
        var why = e.reason || e.note || '';
        return '<div class="tr ga-grid" title="' + esc(g) + ' ' + esc(v.txt) +
            ' · ' + esc(strat) + ' ' + esc(sym) + ' · ' + esc(why) + ' · ' + esc(tstr) + '">' +
          '<span class="t num">' + esc(tstr) + '</span>' +
          '<span class="g" style="color:' + gateColor(e.gate_id) + '">' + esc(g) + '</span>' +
          '<span class="v ' + v.cls + '">' + esc(v.txt) + '</span>' +
          '<span class="sym" title="' + esc(sym) + '">' + esc(sym || '—') + '</span>' +
          '</div>';
      }).join('');
    }

    // ── Probe Activity — per-probe readings, newest first (aligned table).
    // TIME · PROBE · SYMBOL · LEAN/CONF. ──
    var pfeed = $('probefeed');
    var pr = probeRows(s).sort(function (a, b) { return (b.ts || 0) - (a.ts || 0); });
    if ($('probe-cnt')) $('probe-cnt').textContent = pr.length || '';
    if (pfeed) {
      if (!pr.length) {
        pfeed.innerHTML = '<div class="empty">no probe readings</div>';
      } else {
        var prHdr = '<div class="thr pr-grid"><span>TIME</span><span>PROBE</span><span>SYM</span><span class="ta-r">LEAN/CONF</span></div>';
        pfeed.innerHTML = prHdr + pr.slice(0, 60).map(function (p) {
          var tstr = p.ts ? hhmmss(p.ts) : '';
          var rd = (p.lean == null ? '—' : (p.lean >= 0 ? '+' : '') + p.lean.toFixed(2)) +
            (p.conf == null ? '' : ' / ' + p.conf.toFixed(2));
          return '<div class="tr pr-grid" title="' + esc(p.name) + ' ' + esc(p.sym) + ' ' + esc(p.kind) +
              ' · lean ' + (p.lean == null ? '—' : p.lean.toFixed(2)) + ' conf ' + (p.conf == null ? '—' : p.conf.toFixed(2)) +
              ' · ' + esc(p.action || '') + ' · ' + esc(tstr) + '">' +
            '<span class="t num">' + esc(tstr) + '</span>' +
            '<span class="pn" title="' + esc(p.name) + '">' + esc(p.name) + '</span>' +
            '<span class="sym" title="' + esc(p.sym) + '">' + esc(p.sym) + '</span>' +
            '<span class="rd num ' + pnlClass(p.lean) + '">' + rd + '</span>' +
            '</div>';
        }).join('');
      }
    }

    // Bridge optional probe data to the Neural Cloud globe (display-only): probes
    // pulse their ticker node + venue galaxy. Graceful no-op when absent.
    if (window.PolarisGlobe && window.PolarisGlobe.showProbes) {
      window.PolarisGlobe.showProbes(s.probe_events || s.probes || []);
    }
  }

  // Collapsible sections (every .sec.collapsible — i.e. all but Open Positions).
  // Restore the persisted collapse state on load, then delegate the h2 toggle.
  // Key = the section's data-ck; value '1' = collapsed. Default expanded.
  function wireCollapse() {
    var secs = document.querySelectorAll('.sec.collapsible');
    for (var i = 0; i < secs.length; i++) {
      var sec = secs[i];
      var key = sec.getAttribute('data-ck');
      var collapsed = false;
      try { collapsed = localStorage.getItem(key) === '1'; } catch (e) { /* private mode */ }
      if (collapsed) {
        sec.classList.add('collapsed');
        var h = sec.querySelector('h2');
        if (h) h.setAttribute('aria-expanded', 'false');
      }
    }
    function toggle(h2) {
      var sec = h2.closest('.sec.collapsible'); if (!sec) return;
      var isCol = sec.classList.toggle('collapsed');
      h2.setAttribute('aria-expanded', isCol ? 'false' : 'true');
      var key = sec.getAttribute('data-ck');
      try { if (key) localStorage.setItem(key, isCol ? '1' : '0'); } catch (e) { /* private mode */ }
    }
    function findHead(target) {
      var h2 = target.closest ? target.closest('h2') : null;
      if (h2 && h2.parentElement && h2.parentElement.classList.contains('collapsible')) return h2;
      return null;
    }
    document.addEventListener('click', function (e) {
      var h2 = findHead(e.target);
      if (h2) toggle(h2);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      var h2 = findHead(e.target);
      if (h2) { e.preventDefault(); toggle(h2); }
    });
  }

  function tick() {
    fetch('/api/snapshot', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(paint)
      .catch(function () {
        $('dot').classList.remove('live');
        $('status').textContent = 'offline';
      });
  }

  // ── Position GROUP toggle + Chart tap-to-pin (Jin 2026-07-16 P4b) ─────────
  // One delegated listener on #positions — re-renders immediately from
  // `_lastSnap` so a tap doesn't wait for the next 1s poll.
  function wirePositions() {
    var positions = $('positions');
    if (!positions) return;
    positions.addEventListener('click', function (e) {
      var g = e.target.closest ? e.target.closest('.tr.pos-grid.grp') : null;
      if (g) {
        var gk = g.getAttribute('data-g');
        if (expandedGrp.has(gk)) expandedGrp.delete(gk); else expandedGrp.add(gk);
        saveExpandedGrp();
        if (_lastSnap) renderPositions(_lastSnap);
        return;
      }
      var row = e.target.closest ? e.target.closest('.tr.pos-grid.pos') : null;
      if (!row) return;
      var k = row.getAttribute('data-key');
      if (!k) return;
      CHART_STATE.sel = k;
      if (_lastSnap) renderPositions(_lastSnap); // re-syncs .sel highlight + Chart panel, no wait
    });
  }

  // ── Per-strategy gauge marquee (Jin 2026-07-16 P4b) — same flow_stats poll
  // + flow_classes.js widget as /flow, so /m shows the identical class-gauge-
  // strip marquee instead of nothing. Graceful no-op if flow_classes.js
  // didn't load (window.PolarisFlowClasses absent) or the endpoint errors.
  function flowStatsTick() {
    if (!window.PolarisFlowClasses) return;
    fetch('/api/flow_stats?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) { window.PolarisFlowClasses.onStatsPoll(d || {}); })
      .catch(function () { /* display-only */ });
  }

  // ── Live price SSE push ───────────────────────────────────────────────────
  // Open-position price cells flash per-cell as marks STREAM in — not a poll.
  // /stream/prices pushes ONLY the cells that moved on the server; each message
  // updates just the CUR / P&L / % cells of the matching keyed row + a brief
  // up/down flash. The 1s snapshot poll still owns structure (rows added/removed).
  var _pxLast = {};   // key → { px, upnl } last numeric value for flash direction
  function _flashDir(prev, cur) {
    if (prev == null || cur == null || cur === prev) return '';
    return cur > prev ? 'up' : 'down';
  }
  function _flashCell(el, dir) {
    if (!el || !dir) return;
    el.classList.remove('px-flash-up', 'px-flash-down');
    void el.offsetWidth;   // restart the animation on a re-flash
    el.classList.add(dir === 'up' ? 'px-flash-up' : 'px-flash-down');
  }
  function _setCell(el, text, cls, dir) {
    if (!el) return;
    var changed = el.textContent !== text;
    if (changed) el.textContent = text;
    if (cls != null) el.className = cls;
    if (changed) _flashCell(el, dir);
  }
  function applyPriceCell(p) {
    var pos = $('positions'); if (!pos || !p || !p.key) return;
    var row = pos.querySelector('.row[data-key="' +
      (window.CSS && CSS.escape ? CSS.escape(p.key) : p.key) + '"]');
    if (!row) return;
    var prev = _pxLast[p.key] || {};
    var pxDir = _flashDir(prev.px, p.last_price);
    var upDir = _flashDir(prev.upnl, p.upnl_usd);
    _pxLast[p.key] = { px: p.last_price, upnl: p.upnl_usd };
    var pct = (p.upnl_pct || 0);
    _setCell(row.querySelector('.pxc'), fmtPx(p.last_price), 'pxc num', pxDir);
    _setCell(row.querySelector('.sp'), signed(p.upnl_usd), 'sp num ' + pnlClass(p.upnl_usd), upDir);
    _setCell(row.querySelector('.pct'), (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%', 'pct num ' + pnlClass(pct), '');
    // Jin 2026-06-24 SYNC: a moved cell (flash) → pulse the same symbol's globe
    // node on the same event. key = venue|symbol|strategy|side. Display-only.
    if ((pxDir || upDir) && window.PolarisGlobe && window.PolarisGlobe.pulseSymbol) {
      var parts = String(p.key).split('|');
      try { window.PolarisGlobe.pulseSymbol(parts[1], parts[0]); } catch (e) { /* display-only */ }
    }
  }
  var _priceES = null;
  function connectPriceStream() {
    if (_priceES || typeof EventSource === 'undefined') return;
    try {
      _priceES = new EventSource('/stream/prices');
      _priceES.onmessage = function (ev) {
        try {
          var j = JSON.parse(ev.data);
          if (j && j.prices) { for (var i = 0; i < j.prices.length; i++) applyPriceCell(j.prices[i]); }
        } catch (e) { /* ignore a malformed frame */ }
      };
      _priceES.onerror = function () { /* EventSource auto-reconnects */ };
    } catch (e) { /* SSE unavailable — cells fall back to the 1s snapshot poll */ }
  }

  // ── Tabs (Jin 2026-06-24) — Main / Logic / Build / Roadmap / AI ────────────
  // Main + Logic are fed by the 1s snapshot poll (paint). Build + Roadmap + AI
  // pull from dedicated fetch-once endpoints (like the desktop reference tabs),
  // loaded lazily the first time their tab is opened.
  var _loaded = {};
  function showTab(name) {
    var panes = document.querySelectorAll('.tabpane');
    for (var i = 0; i < panes.length; i++) panes[i].classList.toggle('on', panes[i].id === 'tab-' + name);
    var btns = document.querySelectorAll('#tabbar button');
    for (var j = 0; j < btns.length; j++) btns[j].classList.toggle('on', btns[j].getAttribute('data-tab') === name);
    try { localStorage.setItem('m.tab', name); } catch (e) { /* private mode */ }
    if (name === 'build') loadBuild();
    if (name === 'roadmap') loadRoadmap();
    if (name === 'ai') loadAi();
  }
  function wireTabs() {
    var bar = $('tabbar'); if (!bar) return;
    bar.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-tab]') : null;
      if (b) showTab(b.getAttribute('data-tab'));
    });
    var saved = 'main';
    try { saved = localStorage.getItem('m.tab') || 'main'; } catch (e) { /* private mode */ }
    showTab(saved);
  }
  // Build tab — recent commits + test count (GET /api/buildlog, fetch-once).
  function loadBuild() {
    if (_loaded.build) return; _loaded.build = true;
    var body = $('buildlist'); if (!body) return;
    body.innerHTML = '<div class="empty">loading…</div>';
    fetch('/api/buildlog', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) {
        var commits = (d && d.commits) || [];
        if ($('build-cnt')) $('build-cnt').textContent = d && d.test_count ? (d.test_count + ' tests') : (commits.length || '');
        if (!commits.length) { body.innerHTML = '<div class="empty">no build log</div>'; return; }
        body.innerHTML = commits.slice(0, 40).map(function (c) {
          var when = (c.date || '').slice(5, 10);
          return '<div class="li"><span class="when">' + esc(when) + '</span>' +
            '<span class="tg">' + esc((c.type || '').slice(0, 8)) + '</span>' +
            '<span class="what" title="' + esc(c.subject) + '">' + esc(c.subject) + '</span></div>';
        }).join('');
      }).catch(function () { body.innerHTML = '<div class="empty">/api/buildlog unavailable</div>'; _loaded.build = false; });
  }
  // Roadmap tab — phase items + next (GET /api/roadmap, fetch-once).
  function loadRoadmap() {
    if (_loaded.roadmap) return; _loaded.roadmap = true;
    var body = $('roadlist'); if (!body) return;
    body.innerHTML = '<div class="empty">loading…</div>';
    fetch('/api/roadmap', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) {
        var phases = (d && d.phases) || [];
        if ($('road-cnt')) $('road-cnt').textContent = phases.length || '';
        if (!phases.length) { body.innerHTML = '<div class="empty">no roadmap</div>'; return; }
        var html = '';
        phases.slice(0, 12).forEach(function (ph) {
          html += '<div class="ph-head" title="' + esc(ph.phase) + '">' + esc(ph.phase) + '</div>';
          (ph.items || []).slice(0, 20).forEach(function (it) {
            var txt = String(it.text || it || '');
            var tg = /\[DONE\]/i.test(txt) ? 'done' : /\[BUILD\]/i.test(txt) ? 'build' : '';
            var clean = txt.replace(/\*\*/g, '').replace(/\[(DONE|BUILD|DEBATE|DECISION)\]/ig, '').trim();
            html += '<div class="li">' + (tg ? '<span class="tg ' + tg + '">' + tg.toUpperCase() + '</span>' : '') +
              '<span class="what" title="' + esc(clean) + '">' + esc(clean) + '</span></div>';
          });
        });
        body.innerHTML = html;
      }).catch(function () { body.innerHTML = '<div class="empty">/api/roadmap unavailable</div>'; _loaded.roadmap = false; });
  }
  // AI tab — 3 abridged sections (GET /api/ai_activity, fetch-once): 1 BOT AI
  // (runtime OpenAI GPT: gate_events + #32 judge overlay + admission shadow),
  // 2 HARNESS AI (this Claude Code harness, dev-time — NOT the bot's LLM),
  // 3 COWORK INTEL (watchlist seed feed + expiry status, badge large on EXPIRED).
  function loadAi() {
    if (_loaded.ai) return; _loaded.ai = true;
    var body = $('ailist'); if (!body) return;
    body.innerHTML = '<div class="empty">loading…</div>';
    fetch('/api/ai_activity', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) {
        d = d || {};
        var bot = d.bot_ai || {};
        var harness = d.harness_ai || {};
        var cowork = d.cowork_intel || {};
        if ($('ai-cnt')) $('ai-cnt').textContent = '';
        var html = '';

        html += '<div class="ph-head">1 · BOT AI · runtime · OpenAI GPT</div>';
        var ge = bot.gate_events || {};
        html += '<div class="li"><span class="what">gate_events (' + (ge.window_h || 24) + 'h)</span><span class="when num">' + (ge.total || 0) + '</span></div>';
        var mu = ge.model_used || {};
        Object.keys(mu).forEach(function (k) {
          html += '<div class="li"><span class="what">model_used: ' + esc(k) + '</span><span class="when num">' + mu[k] + '</span></div>';
        });
        var j = bot.judge || {};
        html += '<div class="li"><span class="what">#32 judge calls (' + (j.window_h || 24) + 'h)</span><span class="when num">' + (j.calls || 0) + '</span></div>';
        if (j.calls) {
          html += '<div class="li"><span class="what">avg latency</span><span class="when num">' + (j.avg_latency_ms != null ? Math.round(j.avg_latency_ms) + 'ms' : '—') + '</span></div>';
          html += '<div class="li"><span class="what">salvage</span><span class="when num">' + (j.salvage_count || 0) + '</span></div>';
        }
        var adm = bot.entry_admission_shadow || {};
        html += '<div class="li"><span class="what">admission shadow (would-suppress)</span><span class="when num">' +
          (adm.would_suppress || 0) + ' / ' + (adm.total || 0) + '</span></div>';

        html += '<div class="ph-head">2 · HARNESS AI · dev · Claude</div>';
        html += '<div class="li"><span class="what" style="color:var(--p-cyn)">' +
          esc(harness.label || "Claude harness (dev) — not the bot's LLM") + '</span></div>';
        (harness.models || []).forEach(function (m) {
          html += '<div class="li"><span class="what">' + esc(m.model || '—') + '</span><span class="when num">' + (m.turns || 0) + ' turns</span></div>';
        });

        html += '<div class="ph-head">3 · COWORK INTEL · feed</div>';
        if (!cowork.available) {
          html += '<div class="li"><span class="what">no feed</span></div>';
        } else {
          var stKey = String(cowork.status || '').toLowerCase();
          var stCls = stKey === 'expired' ? 'danger' : (stKey === 'active' ? 'done' : '');
          html += '<div class="li">' + (stCls ? '<span class="tg ' + stCls + '">' + esc(cowork.status) + '</span>' : '<span class="tg">' + esc(cowork.status || '—') + '</span>') +
            '<span class="what">cohort fired: ' + (cowork.cohort_fired || 0) + '</span></div>';
          (cowork.candidates || []).slice(0, 8).forEach(function (c) {
            html += '<div class="li"><span class="what">' + esc(c.symbol || '—') + ' · ' + esc(c.thesis_tag || '') + '</span>' +
              '<span class="when num">' + (c.score != null ? c.score.toFixed(2) : '—') + '</span></div>';
          });
        }
        body.innerHTML = html;
      }).catch(function () { body.innerHTML = '<div class="empty">/api/ai_activity unavailable</div>'; _loaded.ai = false; });
  }

  wireCollapse();
  wireTabs();
  wirePositions();
  tick();
  setInterval(tick, POLL_MS);
  connectPriceStream();
  flowStatsTick();
  setInterval(flowStatsTick, POLL_MS);
})();
