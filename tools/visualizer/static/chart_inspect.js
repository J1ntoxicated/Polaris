/* Polaris shared inspect-chart builder (Jin 2026-07-16 P4b mobile-drift fix).
 * Extracted verbatim from wall_side.js (P4a) so /flow (desktop) and /m
 * (mobile) render the IDENTICAL glance-only inline chart instead of two
 * copies drifting apart. Pure function: position row in, SVG string out —
 * no DOM reads, no globals besides window.PolarisChartInspect.
 * Display-only. Deep multi-indicator charting lives on the /board ticker
 * chart; this is a glance-only reimplementation, not a rebuild of it.
 */
(function () {
  'use strict';
  // Lightweight inline inspect chart — spark price line + entry(dotted steel)/
  // stop(dashed red) levels + MFE/MAE band (R-multiples converted to price via
  // R = |entry - stop|, the same risk unit the position was sized against) +
  // current-price marker.
  function buildChartSvg(p) {
    var arr = Array.isArray(p.spark) && p.spark.length >= 2 ? p.spark.slice() : null;
    var entry = p.entry_price || null;
    var lastPx = p.last_price || (arr ? arr[arr.length - 1] : entry);
    if (!arr) arr = [entry, lastPx].filter(function (v) { return v != null; });
    if (arr.length < 2) return '<div class="side-chart-empty">no price history yet</div>';
    var stop = p.stop_price ? p.stop_price : null;
    var dir = String(p.side || '').toLowerCase() === 'short' ? -1 : 1;
    var R = (entry != null && stop != null) ? Math.abs(entry - stop) : null;
    var mfe = (R != null && p.mfe_atr_r != null) ? entry + dir * p.mfe_atr_r * R : null;
    var mae = (R != null && p.mae_atr_r != null) ? entry + dir * p.mae_atr_r * R : null;

    var vals = arr.slice();
    if (entry != null) vals.push(entry);
    if (stop != null) vals.push(stop);
    if (mfe != null) vals.push(mfe);
    if (mae != null) vals.push(mae);
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var padSpan = (hi - lo) * 0.08 || Math.abs(hi) * 0.01 || 1;
    lo -= padSpan; hi += padSpan;
    var span = (hi - lo) || 1;

    var W = 400, H = 130, PADX = 6, PADY = 8, n = arr.length;
    var iw = W - PADX * 2, ih = H - PADY * 2;
    function xOf(i) { return PADX + (n === 1 ? 0 : (i / (n - 1)) * iw); }
    function yOf(v) { return PADY + ih - ((v - lo) / span) * ih; }
    function hline(y, col, dash, w) {
      var yy = y.toFixed(1);
      return '<line x1="' + PADX + '" y1="' + yy + '" x2="' + (W - PADX) + '" y2="' + yy + '" stroke="' + col + '" stroke-width="' + w + '" stroke-dasharray="' + dash + '" opacity="0.85"/>';
    }

    var pts = arr.map(function (v, i) { return xOf(i).toFixed(1) + ',' + yOf(v).toFixed(1); }).join(' ');
    var u = p.upnl_usd != null ? p.upnl_usd : p.pnl_usd;
    var lineCol = u > 0 ? '#7dffa8' : u < 0 ? '#ff7d8a' : '#9fc0ff';

    var svg = '';
    if (mfe != null && mae != null) {
      var yTop = Math.min(yOf(mfe), yOf(mae)), yBot = Math.max(yOf(mfe), yOf(mae));
      svg += '<rect x="' + PADX + '" y="' + yTop.toFixed(1) + '" width="' + iw + '" height="' + (yBot - yTop).toFixed(1) + '" fill="rgba(159,192,255,0.07)"/>';
    }
    if (mfe != null) svg += hline(yOf(mfe), '#7dffa8', '2 2', 0.8);
    if (mae != null) svg += hline(yOf(mae), '#ff7d8a', '2 2', 0.8);
    if (entry != null) svg += hline(yOf(entry), '#8a94b0', '3 2', 1);
    if (stop != null) svg += hline(yOf(stop), '#ff4d5e', '4 2', 1);
    svg += '<polyline points="' + pts + '" fill="none" stroke="' + lineCol + '" stroke-width="1.25" stroke-linejoin="round" stroke-linecap="round"/>';
    svg += '<circle cx="' + xOf(n - 1).toFixed(1) + '" cy="' + yOf(arr[n - 1]).toFixed(1) + '" r="2.5" fill="' + lineCol + '"/>';
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" aria-hidden="true">' + svg + '</svg>';
  }

  window.PolarisChartInspect = { buildChartSvg: buildChartSvg };
})();
