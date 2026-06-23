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

  // Same verdict mapping as desktop board_tabs_ext.gateVerdict: PASS green,
  // KILL red, everything else (HOLD / ADJUST_EXIT / monitor decisions) = 'other'.
  function gateVerdict(e) {
    var raw = String(e.decision || e.verdict || e.action || '').toUpperCase();
    if (/PASS|ADMIT|OK|ENTER|VALID|ALLOW/.test(raw)) return { txt: 'PASS', cls: 'pass' };
    if (/KILL|REJECT|BLOCK|DENY|FAIL|VETO|SUPPRESS/.test(raw)) return { txt: 'KILL', cls: 'kill' };
    return { txt: raw || '—', cls: 'other' };
  }

  // Normalize an OPTIONAL probe stream into the gate-activity feed. Probe data
  // is being designed in parallel — read s.probe_events[] first, else probe-
  // tagged items already inside recent_gate_events (gate_id/label marks them).
  // Shape kept GENERIC: {name|probe, ticker|symbol, reading|value|state,
  // decision?, reason?, ts, venue?}. Returns [] when no probe data → feed
  // looks unchanged (graceful). Each becomes a feed-row event tagged isProbe.
  function probeFeedEvents(s) {
    var out = [];
    var pe = s.probe_events || s.probes || [];
    for (var i = 0; i < pe.length; i++) {
      var pr = pe[i] || {};
      var rd = (pr.reading != null) ? pr.reading
        : (pr.value != null) ? pr.value
        : (pr.state != null) ? pr.state : '';
      out.push({
        isProbe: true,
        name: pr.name || pr.probe || pr.id || 'probe',
        symbol: pr.ticker || pr.symbol || '',
        decision: pr.decision || '',
        reason: pr.reason || (rd !== '' ? String(rd) : ''),
        venue: pr.venue || '',
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

    // ── Open Positions — the primary section. Dense one-line rows. ──
    var positions = $('positions');
    var ps = s.positions || [];
    $('pos-cnt').textContent = ps.length || '';
    if (!ps.length) {
      positions.innerHTML = '<div class="empty">No open positions</div>';
    } else {
      positions.innerHTML = ps.slice(0, 30).map(function (p) {
        var sideCls = (String(p.side || '').toLowerCase().indexOf('s') === 0 ||
          String(p.side || '').toLowerCase() === 'short') ? 'side-short' : 'side-long';
        // Exchange colour label as the FIRST element — matches the desktop
        // per-position venue cue (row-a/b/c left-border + VEN cell). Small
        // uppercase chip tinted via --stream-a/b/c (v-okx/v-capital/v-alpaca).
        var vi = venueInfo(p.venue);
        // Aligned grid: chip · symbol · side(L/S) · ENTRY · CURRENT(live) ·
        // PROFIT · % · EXPOSURE. Columns line up across rows (main-dashboard style).
        var pct = (p.upnl_pct || 0);
        var sideTag = (String(p.side || '').toLowerCase().charAt(0) === 's') ? 'S' : 'L';
        return '<div class="row">' +
          '<span class="vchip v-' + vi.key + '" title="' + esc(p.venue) + '">' + esc(vi.tag) + '</span>' +
          '<span class="a" title="' + esc(p.symbol) + '">' + esc(p.symbol) + '</span>' +
          '<span class="b ' + sideCls + '" title="' + esc(p.side) + '">' + sideTag + '</span>' +
          '<span class="pxe num" title="entry">' + fmtPx(p.entry_price) + '</span>' +
          '<span class="pxc num" title="current (live)">' + fmtPx(p.last_price) + '</span>' +
          '<span class="sp num ' + pnlClass(p.upnl_usd) + '">' + signed(p.upnl_usd) + '</span>' +
          '<span class="pct num ' + pnlClass(pct) + '">' + (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%</span>' +
          '<span class="exp num" title="exposure (size USD)">' + kusd(p.size_usd) + '</span>' +
          '</div>';
      }).join('');
    }

    // ── Gate Activity — live gate feed, mirrors the desktop board_tabs_ext g2.
    // Gate decisions AND probe events flow through the SAME time-sorted stream,
    // newest first:  G#/PROBE · decision · strategy symbol · reason · hh:mm:ss
    var feed = $('gatefeed');
    var ge = (s.recent_gate_events || []).slice().concat(probeFeedEvents(s));
    $('gate-cnt').textContent = ge.length || '';
    if (!ge.length) {
      feed.innerHTML = '<div class="empty">no gate events yet</div>';
    } else {
      ge.sort(function (a, b) {
        return (b.ts || b.ts_unix || 0) - (a.ts || a.ts_unix || 0);
      });
      feed.innerHTML = ge.slice(0, 60).map(function (e) {
        var sym = e.symbol || e.ticker || '';
        var why = e.reason || e.note || '';
        var ts = e.ts || e.ts_unix;
        var tstr = ts ? hhmmss(ts) : '';
        if (e.isProbe) {
          // Probe event row — tagged PROBE in the gate slot, probe name as label.
          var pv = gateVerdict(e);   // honours decision if probe carries one
          var pverd = e.decision ? '<span class="v ' + pv.cls + '">' + esc(pv.txt) + '</span>' : '<span class="v probe">PROBE</span>';
          return '<div class="gf-row" title="PROBE ' + esc(e.name) + ' · ' + esc(sym) + ' · ' + esc(why) + ' · ' + esc(tstr) + '">' +
            '<span class="g gprobe">⊙</span>' +
            pverd +
            '<span class="what"><span class="glbl">' + esc(e.name) + '</span> ' +
              '<span class="sym">' + esc(sym) + '</span> ' +
              '<span class="why">' + esc(why) + '</span>' +
            '</span>' +
            '<span class="t">' + esc(tstr) + '</span>' +
            '</div>';
        }
        var v = gateVerdict(e);
        var g = (e.gate_id != null) ? ('G' + e.gate_id) : 'G?';
        var label = e.label || e.gate_label || '';   // gate name (e.g. 'Monitor')
        var strat = e.strategy_id || e.strategy || '';
        // Fuller detail: gate label + venue chip + strategy + symbol + reason.
        var lbl = label ? '<span class="glbl">' + esc(label) + '</span> ' : '';
        return '<div class="gf-row" title="' + esc(g) + ' ' + esc(label) + ' ' + esc(v.txt) +
            ' · ' + esc(strat) + ' ' + esc(sym) + ' · ' + esc(why) + ' · ' + esc(tstr) + '">' +
          '<span class="g">' + esc(g) + '</span>' +
          '<span class="v ' + v.cls + '">' + esc(v.txt) + '</span>' +
          '<span class="what">' + lbl +
            '<span class="strat">' + esc(strat) + '</span> ' +
            '<span class="sym">' + esc(sym) + '</span> ' +
            '<span class="why">' + esc(why) + '</span>' +
          '</span>' +
          '<span class="t">' + esc(tstr) + '</span>' +
          '</div>';
      }).join('');
    }

    // Bridge optional probe data to the Neural Cloud globe (display-only): probes
    // pulse their ticker node + venue galaxy. Graceful no-op when absent.
    if (window.PolarisGlobe && window.PolarisGlobe.showProbes) {
      window.PolarisGlobe.showProbes(s.probe_events || s.probes || []);
    }
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

  tick();
  setInterval(tick, POLL_MS);
})();
