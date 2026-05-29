/* Polaris dashboard overlay — renders the live terminal dashboard (dashboard_v2)
 * inside the browser, beside the sphere. Fetches /api/dashboard (ANSI rows from
 * render_dashboard_v2) and converts SGR escapes → styled HTML. Display-only. */
(function () {
  "use strict";

  // xterm-256 → rgb palette (16 base + 6x6x6 cube + 24 grayscale).
  var XTERM = (function () {
    var c = [];
    var base = [
      [0, 0, 0], [205, 0, 0], [0, 205, 0], [205, 205, 0], [0, 0, 238],
      [205, 0, 205], [0, 205, 205], [229, 229, 229], [127, 127, 127],
      [255, 0, 0], [0, 255, 0], [255, 255, 0], [92, 92, 255], [255, 0, 255],
      [0, 255, 255], [255, 255, 255]
    ];
    for (var i = 0; i < 16; i++) c[i] = base[i];
    for (i = 16; i < 232; i++) {
      var n = i - 16;
      var r = Math.floor(n / 36) % 6, g = Math.floor(n / 6) % 6, b = n % 6;
      var f = function (v) { return v === 0 ? 0 : 55 + 40 * v; };
      c[i] = [f(r), f(g), f(b)];
    }
    for (i = 232; i < 256; i++) { var v = 8 + 10 * (i - 232); c[i] = [v, v, v]; }
    return c;
  })();

  function esc(t) {
    return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Convert one ANSI line to HTML, wrapping coloured runs in <span>.
  function ansiToHtml(line) {
    var cur = { fg: null, bold: false, dim: false };
    var html = "";
    var re = /\x1b\[([0-9;]*)m/g;
    var last = 0, m;
    function spanOpen() {
      var s = "";
      if (cur.fg) s += "color:rgb(" + cur.fg.join(",") + ");";
      if (cur.bold) s += "font-weight:700;";
      if (cur.dim) s += "opacity:.55;";
      return s ? '<span style="' + s + '">' : "<span>";
    }
    function flush(text) {
      if (!text) return;
      html += spanOpen() + esc(text) + "</span>";
    }
    while ((m = re.exec(line)) !== null) {
      flush(line.slice(last, m.index));
      last = re.lastIndex;
      var codes = m[1].split(";").filter(function (x) { return x !== ""; })
        .map(Number);
      if (codes.length === 0) codes = [0];
      for (var k = 0; k < codes.length; k++) {
        var code = codes[k];
        if (code === 0) cur = { fg: null, bold: false, dim: false };
        else if (code === 1) cur.bold = true;
        else if (code === 2) cur.dim = true;
        else if (code === 22) { cur.bold = false; cur.dim = false; }
        else if (code === 39) cur.fg = null;
        else if (code === 38 && codes[k + 1] === 5) {
          cur.fg = XTERM[codes[k + 2]] || null; k += 2;
        }
      }
    }
    flush(line.slice(last));
    return html;
  }

  var panel, pre, statusEl, visible = true, timer = null;

  function render(rows) {
    pre.innerHTML = rows.map(ansiToHtml).join("\n");
  }

  async function tick() {
    try {
      var r = await fetch("/api/dashboard?t=" + Date.now());
      if (!r.ok) throw new Error("HTTP " + r.status);
      var d = await r.json();
      render(d.rows || []);
      statusEl.textContent = "live · " + new Date().toLocaleTimeString();
      statusEl.style.color = "#7fd18b";
    } catch (e) {
      statusEl.textContent = "reconnecting… (" + e.message + ")";
      statusEl.style.color = "#d6a14f";
    }
  }

  function start() {
    tick();
    timer = setInterval(tick, 3000);
  }

  function toggle() {
    visible = !visible;
    panel.style.transform = visible ? "translateX(0)" : "translateX(102%)";
  }

  function injectStyle() {
    var css =
      "#dash-overlay{position:fixed;top:0;right:0;height:100vh;width:62ch;" +
      "max-width:48vw;background:rgba(8,10,16,.90);backdrop-filter:blur(3px);" +
      "border-left:1px solid #2a3142;box-shadow:-8px 0 24px rgba(0,0,0,.5);" +
      "z-index:9999;display:flex;flex-direction:column;transition:transform .25s ease;" +
      "font-family:'SF Mono',Menlo,Consolas,monospace;}" +
      "#dash-overlay-hdr{display:flex;align-items:center;gap:10px;padding:6px 10px;" +
      "border-bottom:1px solid #2a3142;font-size:11px;letter-spacing:.08em;color:#8aa0c0;}" +
      "#dash-overlay-hdr .dash-title{font-weight:700;color:#9fd0ff;}" +
      "#dash-overlay-status{margin-left:auto;font-size:10px;}" +
      "#dash-overlay-hdr .dash-x{cursor:pointer;color:#7a8aa3;font-size:16px;line-height:1;}" +
      "#dash-overlay-hdr .dash-x:hover{color:#fff;}" +
      "#dash-overlay-pre{margin:0;padding:8px 10px;overflow:auto;flex:1;" +
      "font-size:11px;line-height:1.28;white-space:pre;color:#c8d2e0;}" +
      "#dash-overlay-tab{position:fixed;top:10px;right:10px;z-index:9998;" +
      "background:rgba(20,26,38,.85);color:#9fd0ff;border:1px solid #2a3142;" +
      "border-radius:6px;padding:5px 10px;font:600 11px/1 'SF Mono',Menlo,monospace;" +
      "letter-spacing:.06em;cursor:pointer;}" +
      "#dash-overlay-tab:hover{background:rgba(30,40,60,.95);}";
    var s = document.createElement("style");
    s.textContent = css;
    document.head.appendChild(s);
  }

  function build() {
    injectStyle();
    panel = document.createElement("div");
    panel.id = "dash-overlay";
    var header = document.createElement("div");
    header.id = "dash-overlay-hdr";
    header.innerHTML =
      '<span class="dash-title">TERMINAL DASHBOARD</span>' +
      '<span id="dash-overlay-status"></span>' +
      '<span class="dash-x" title="hide (D)">×</span>';
    pre = document.createElement("pre");
    pre.id = "dash-overlay-pre";
    panel.appendChild(header);
    panel.appendChild(pre);
    document.body.appendChild(panel);
    statusEl = header.querySelector("#dash-overlay-status");
    header.querySelector(".dash-x").addEventListener("click", toggle);

    var tab = document.createElement("button");
    tab.id = "dash-overlay-tab";
    tab.textContent = "▣ DASH";
    tab.title = "toggle terminal dashboard (D)";
    tab.addEventListener("click", toggle);
    document.body.appendChild(tab);

    window.addEventListener("keydown", function (e) {
      if ((e.key === "d" || e.key === "D") && !e.metaKey && !e.ctrlKey) toggle();
    });
    start();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
