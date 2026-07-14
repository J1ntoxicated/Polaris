/* Polaris FLOW — pts-classes river annotations (Jin 2026-07-10,
 * feat/visual-wall-director): the score_F window-fill gauge strip, EARN/
 * BENCH transition badge pop, and "NEW CANDIDATE" universe-admission flash
 * — split out of flow.js to keep that file under the project's 500-LOC cap.
 *
 * Self-contained: reads the SAME /api/flow_stats payload flow.js already
 * polls (its `classes` / `survivor_admissions_recent` fields), fed in via
 * window.PolarisFlowClasses.onStatsPoll(d) — flow.js calls this once per
 * poll cycle rather than this file opening a second fetch. flow.js also
 * calls window.PolarisFlowClasses.setColX(colX) whenever its own layout()
 * recomputes the river's column x-coordinates, so the "NEW CANDIDATE" flash
 * can park itself under the G1 column.
 *
 * Display-only — nothing here issues, sizes, gates or throttles a trade.
 */
(function () {
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const classKey = (venue, sid) => venue + '|' + sid;
  let classByKey = {};      // own copy, purely for EARN/BENCH transition diffing
  let colX = [];
  let _classesSeeded = false;   // first poll seeds classByKey without popping badges
  let _lastAdmissionTs = 0;     // watermark seeded on first poll (no flash flood on load)
  let _candidateFlashTimer = null;
  let _cgSig = null;            // marquee roster signature — rebuild track only on change

  function renderClassGauges(d) {
    const el = document.getElementById('class-gauge-strip');
    if (!el) return;
    const classes = d.classes || [];
    if (!classes.length) { el.innerHTML = ''; _cgSig = null; return; }
    // Jin 2026-07-15 "순차적으로 계속 흐르듯이": the marquee track is rebuilt
    // ONLY when the roster/class set changes — NOT on every stats poll.
    // Rebuilding innerHTML each poll restarted the CSS animation, so it jerked
    // back to the start instead of looping seamlessly. Between rebuilds the
    // duplicated run + translateX(-50%) loops continuously; gauge fills refresh
    // in place so the animation is never interrupted.
    const sig = classes.map((c) => c.strategy_id + ':' + (c.strategy_class || '')).join('|');
    if (sig !== _cgSig) {
      const chips = classes.map((c) => {
        const pct = c.window_w ? Math.round((100 * c.filled) / c.window_w) : 0;
        const cls = String(c.strategy_class || '').toLowerCase();
        return `<span class="chip ${esc(cls)}"><span class="sid">${esc(c.strategy_id)}</span>`
          + `<span class="gauge"><span class="fill" style="width:${pct}%"></span></span>`
          + `<span class="nm">${c.filled}/${c.window_w}</span></span>`;
      }).join('');
      const dur = Math.max(18, classes.length * 2.4);
      el.innerHTML = `<span class="cg-track" style="animation-duration:${dur}s">${chips}${chips}</span>`;
      _cgSig = sig;
      return;
    }
    // roster unchanged → refresh gauge fills + N/M in BOTH duplicated copies
    // (index i and i+per) without rebuilding the track.
    const fills = el.querySelectorAll('.chip .fill');
    const nms = el.querySelectorAll('.chip .nm');
    const per = classes.length;
    classes.forEach((c, i) => {
      const pct = c.window_w ? Math.round((100 * c.filled) / c.window_w) : 0;
      const txt = c.filled + '/' + c.window_w;
      [i, i + per].forEach((k) => {
        if (fills[k]) fills[k].style.width = pct + '%';
        if (nms[k]) nms[k].textContent = txt;
      });
    });
  }

  function spawnClassBadge(kind, c) {
    const el = document.getElementById('class-badges');
    if (!el) return;
    const arrow = kind === 'earn' ? '▲' : '▼';
    const label = kind === 'earn' ? 'EARN' : 'BENCH';
    const badge = document.createElement('div');
    badge.className = 'badge ' + kind;
    badge.textContent = `${label}${arrow} ${c.strategy_id}`;
    badge.addEventListener('animationend', () => badge.remove());
    el.appendChild(badge);
  }

  function positionCandidateFlash() {
    const el = document.getElementById('candidate-flash');
    if (!el || !colX.length) return;
    el.style.left = colX[0] + 'px';
    el.style.transform = 'translateX(-50%)';
  }

  function spawnCandidateFlash(a) {
    const el = document.getElementById('candidate-flash');
    if (!el) return;
    positionCandidateFlash();
    el.textContent = 'NEW CANDIDATE · ' + a.symbol;
    el.classList.add('show');
    if (_candidateFlashTimer) clearTimeout(_candidateFlashTimer);
    _candidateFlashTimer = setTimeout(() => el.classList.remove('show'), 2500);
  }

  function onStatsPoll(d) {
    const prevClassByKey = classByKey;
    classByKey = {};
    for (const c of d.classes || []) classByKey[classKey(c.venue, c.strategy_id)] = c;
    renderClassGauges(d);
    // EARN/BENCH transition badge pop — diff against the previous poll's
    // class (skip the very first poll so pre-existing classes don't flood
    // the page with badges on load).
    if (_classesSeeded) {
      for (const c of d.classes || []) {
        const prev = prevClassByKey[classKey(c.venue, c.strategy_id)];
        if (prev && prev.strategy_class !== c.strategy_class) {
          if (c.strategy_class === 'EARN') spawnClassBadge('earn', c);
          else if (c.strategy_class === 'BENCH') spawnClassBadge('bench', c);
        }
      }
    }
    _classesSeeded = true;
    // "NEW CANDIDATE" flash — any survivor_admissions row newer than the
    // last-seen watermark (seeded on the first poll, same no-flood pattern).
    // survivor_admissions doesn't exist yet server-side (fail-safe empty
    // list) — this simply never fires until it lands.
    const admissions = d.survivor_admissions_recent || [];
    if (_lastAdmissionTs > 0) {
      for (const a of admissions) {
        if (a.ts > _lastAdmissionTs) spawnCandidateFlash(a);
      }
    }
    if (admissions.length) _lastAdmissionTs = Math.max(_lastAdmissionTs, ...admissions.map((a) => a.ts));
  }

  window.PolarisFlowClasses = {
    onStatsPoll,
    setColX: (x) => { colX = x; positionCandidateFlash(); },
  };
})();
