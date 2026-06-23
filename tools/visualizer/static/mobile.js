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

  function paint(s) {
    // Header status — alive if we have a snapshot.
    $('dot').classList.add('live');
    $('status').textContent = 'live';

    // Compact status strip: equity + today P&L (small, not a hero block).
    // Show BOTH: demo (70bps fee-burdened) · real-fee-net (go-live truth at 10bps).
    var eq = $('equity');
    eq.textContent = usd(s.equity_now) + ' demo';
    eq.title = 'OKX demo charges 70bps (7x real); real-fee-net = equity at live 10bps fees';
    var eqSub = $('equity-sub');
    if (eqSub) eqSub.textContent = usd(s.equity_now_real_fee_net) + ' real-fee-net';
    var pnl = $('pnl');
    pnl.textContent = signed(s.daily_pnl_usd);
    pnl.className = 'v num ' + pnlClass(s.daily_pnl_usd);
    var pnlPct = $('pnl-pct');
    if (pnlPct) {
      // today's return on day-start equity (equity_now − today's P&L).
      var dayStart = (s.equity_now || 0) - (s.daily_pnl_usd || 0);
      var dp = dayStart > 0 ? (s.daily_pnl_usd / dayStart) * 100 : 0;
      pnlPct.textContent = (dp >= 0 ? '+' : '') + dp.toFixed(2) + '%';
      pnlPct.className = 'sub num ' + pnlClass(dp);
    }

    // ── Open Positions — aligned table w/ header. chip · symbol · side ·
    // current(live) · profit · % · exp. ENTRY dropped on phone (overflow fix). ──
    var positions = $('positions');
    var ps = s.positions || [];
    $('pos-cnt').textContent = ps.length || '';
    if (!ps.length) {
      positions.innerHTML = '<div class="empty">No open positions</div>';
    } else {
      var posHdr = '<div class="thr pos-grid">' +
        '<span></span><span>SYM</span><span class="ta-c">S</span>' +
        '<span class="ta-r">CUR</span><span class="ta-r">P&amp;L</span>' +
        '<span class="ta-r">%</span><span class="ta-r">EXP</span></div>';
      positions.innerHTML = posHdr + ps.slice(0, 30).map(function (p) {
        var sideCls = (String(p.side || '').toLowerCase().indexOf('s') === 0 ||
          String(p.side || '').toLowerCase() === 'short') ? 'side-short' : 'side-long';
        var vi = venueInfo(p.venue);
        var pct = (p.upnl_pct || 0);
        var sideTag = (String(p.side || '').toLowerCase().charAt(0) === 's') ? 'S' : 'L';
        // data-key matches the server /stream/prices key (venue|symbol|strategy|side)
        // so streamed cell pushes flash the live CUR / P&L / % cells of THIS row.
        var key = [p.venue, p.symbol, p.strategy_id, p.side].join('|');
        return '<div class="tr row pos-grid" data-key="' + esc(key) + '">' +
          '<span class="vchip v-' + vi.key + '" title="' + esc(p.venue) + '">' + esc(vi.tag) + '</span>' +
          '<span class="a" title="' + esc(p.symbol) + '">' + esc(p.symbol) + '</span>' +
          '<span class="b ' + sideCls + '" title="' + esc(p.side) + '">' + sideTag + '</span>' +
          '<span class="pxc num" title="current (live)">' + fmtPx(p.last_price) + '</span>' +
          '<span class="sp num ' + pnlClass(p.upnl_usd) + '">' + signed(p.upnl_usd) + '</span>' +
          '<span class="pct num ' + pnlClass(pct) + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%</span>' +
          '<span class="exp num" title="exposure (size USD)">' + kusd(p.size_usd) + '</span>' +
          '</div>';
      }).join('');
    }

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
            '<span class="g">G' + esc(g.gate_id) + '</span>' +
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
          '<span class="g">' + esc(g) + '</span>' +
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

  wireCollapse();
  tick();
  setInterval(tick, POLL_MS);
  connectPriceStream();
})();
