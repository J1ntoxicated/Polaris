/* board_reference.js — Polaris reference-tab renderers (DEMO/PAPER display-only).
 *
 * The three reference tabs (개발 / 가야할길 / 배운것) are STATIC: their data comes
 * from dedicated endpoints (/api/buildlog · /api/roadmap · /api/lessons), NOT the
 * 1s /api/snapshot poll. So instead of binding to the snapshot frame, each
 * renderer fetches its endpoint ONCE on first activation, caches the payload, and
 * renders from cache on every later call (the snapshot poll keeps re-invoking the
 * active tab's renderer — for these tabs that's a cheap cache read, no refetch).
 *
 * Loads AFTER board_tabs_ext.js: it re-registers build/path/learned via
 * window.PolarisBoardTabs.register, OVERRIDING ext.js's P3 placeholders. Pure
 * DOM render + fetch. No mock data — every field is a live endpoint value. No
 * sizing/order/trading logic. Uses ONLY the existing #board palette tokens.
 *
 * Bloomberg-dense detail LISTS (no cards/ladders/kanban): Build = commit
 * timeline + test/heat/digest; Roadmap = phase groups (item rows + tags) +
 * plans + next strikes; Lessons = title + FULL takeaway rows (no truncation) +
 * anti-pattern list + forensic + debate. English chrome; vault titles verbatim.
 */
(function () {
  'use strict';

  const B = window.PolarisBoard;
  const T = window.PolarisBoardTabs;
  if (!B || !T) return;
  const $ = B.$;
  const esc = B.esc;
  const pn = B.pn;

  // ── per-endpoint fetch-once cache ──────────────────────────────────────────
  // state: 'idle' (not yet fetched) | 'loading' | 'ready' | 'error'. The render
  // fns are idempotent — first call kicks the fetch + paints a loading note;
  // when the fetch resolves it caches the data and re-renders the (still-active)
  // pane. Later poll-driven calls just re-render from cache.
  const CACHE = {};   // endpoint → { state, data, err }
  function ensure(endpoint, onReady) {
    let c = CACHE[endpoint];
    if (!c) { c = CACHE[endpoint] = { state: 'idle', data: null, err: null }; }
    if (c.state === 'ready') { onReady(c.data); return; }
    if (c.state === 'loading') return;       // fetch in flight — wait
    c.state = 'loading';
    fetch(endpoint + '?t=' + Date.now(), { cache: 'no-store' })
      .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(d => { c.state = 'ready'; c.data = d; onReady(d); })
      .catch(e => { c.state = 'error'; c.err = String(e); onReady(null); });
  }

  // ── small shared bits ──────────────────────────────────────────────────────
  function loadingHtml() { return '<div class="empty">loading…</div>'; }
  function errorHtml(ep) { return `<div class="empty">fetch failed · ${esc(ep)}</div>`; }
  function noneYet() { return '<span class="ref-none">none yet</span>'; }
  // Persisted collapse state, DEFAULT COLLAPSED (the debate section starts
  // folded; only an explicit '0' from a prior un-fold keeps it open). Mirrors
  // board_tabs.wireCollapsibles' localStorage convention ('1' collapsed / '0'
  // expanded) so the shared delegated toggle round-trips correctly.
  function collapseStateDefaultClosed(key) {
    let collapsed = true;
    try { collapsed = localStorage.getItem(key) !== '0'; } catch (e) { /* private mode */ }
    return { cls: collapsed ? ' collapsed' : '', aria: collapsed ? 'false' : 'true' };
  }
  function shortDate(iso) {
    if (!iso) return '';
    const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? (m[2] + '-' + m[3]) : String(iso).slice(0, 10);
  }
  // commit type → existing palette token (feat green / fix yellow / docs grey;
  // everything else dim). No new colours.
  function typeClass(t) {
    const k = String(t || '').toLowerCase();
    if (k === 'feat') return 'rc-feat';
    if (k === 'fix') return 'rc-fix';
    if (k === 'docs') return 'rc-docs';
    return 'rc-other';
  }
  // Strip the "type(scope):" prefix the timeline chip already encodes.
  function subjBody(subject, type) {
    let s = String(subject || '');
    const re = new RegExp('^' + (type || '') + '(\\([^)]*\\))?:\\s*', 'i');
    return s.replace(re, '');
  }
  // Roadmap/lesson status word → existing-palette tag class. Unknown → neutral.
  function tagClass(t) {
    const k = String(t || '').toUpperCase();
    if (k === 'DONE') return 't-done';
    if (k === 'BUILD') return 't-build';
    if (k === 'DEBATE') return 't-debate';
    if (k === 'DECISION') return 't-decision';
    return '';
  }
  function tagHtml(t) {
    if (!t) return '';
    return `<span class="ref-tg ${tagClass(t)}">${esc(t)}</span>`;
  }
  // inline **bold** markdown emphasis on an escaped string
  function mdBold(s) { return esc(s).replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>'); }

  // ── style (reference panes only) — existing #board tokens, no new colours ──
  const REF_CSS = `
  /* shared reference scaffolding */
  #board .ref { display: flex; flex-direction: column; gap: 9px; padding: 8px 10px; }
  #board .ref-sec { display: flex; flex-direction: column; gap: 3px; }
  #board .ref-sec > .ref-h {
    font-size: 9px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--p-dim); border-bottom: 1px dotted rgba(95,135,175,0.14); padding-bottom: 2px;
  }
  #board .ref-sec > .ref-h .ref-hc { color: var(--p-cyn); margin-left: 6px; letter-spacing: 0; }
  #board .ref-none { color: var(--p-dim); font-size: 10px; font-style: italic; }

  /* commit timeline (개발) */
  #board .rc { display: grid; grid-template-columns: 46px 1fr 44px; align-items: baseline; gap: 8px;
    padding: 1px 0; font-size: 11px; line-height: 1.45; }
  #board .rc .rc-type {
    font-size: 8px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    text-align: center; padding: 1px 0; border: 1px solid var(--ghost);
  }
  #board .rc .rc-type.rc-feat { color: var(--p-grn); border-color: rgba(135,215,135,0.5); }
  #board .rc .rc-type.rc-fix  { color: var(--p-ylw); border-color: rgba(215,215,135,0.5); }
  #board .rc .rc-type.rc-docs { color: var(--p-gry); border-color: var(--ghost); }
  #board .rc .rc-type.rc-other{ color: var(--p-dim); border-color: var(--ghost); }
  #board .rc .rc-subj { color: var(--p-wht); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .rc .rc-date { color: var(--p-dim); font-size: 9px; text-align: right; font-variant-numeric: tabular-nums; }

  /* wave digest (개발) — log_tail lines */
  #board .ref-log { font-size: 10px; line-height: 1.5; }
  #board .ref-log .lg { color: var(--p-gry); padding: 1px 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .ref-log .lg b { color: var(--p-wht); font-weight: 700; }

  /* activity heat-strip (개발) — GitHub-calendar-ish, intensity via opacity */
  #board .ref-heat { display: flex; gap: 2px; flex-wrap: wrap; align-items: flex-end; }
  #board .ref-heat .hc { width: 11px; height: 11px; background: var(--p-grn); }

  /* badges (test-health) */
  #board .ref-badge {
    display: inline-flex; align-items: baseline; gap: 5px; font-size: 10px;
    border: 1px solid rgba(135,215,135,0.45); padding: 2px 8px; color: var(--p-grn);
  }
  #board .ref-badge .bv { font-weight: 700; font-variant-numeric: tabular-nums; }
  #board .ref-badge.warn { border-color: rgba(215,215,135,0.45); color: var(--p-ylw); }

  /* phase rows (가야할길) — Bloomberg dense list, one phase = a group header +
     indented item rows. Progress shown as inline N/M, not a rail. */
  #board .ref-phase { display: flex; flex-direction: column; gap: 1px; margin-bottom: 5px; }
  #board .ref-phase .ph-h { display: grid; grid-template-columns: 1fr 52px; align-items: baseline; gap: 8px;
    color: var(--p-wht); font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
    border-bottom: 1px dotted rgba(95,135,175,0.14); padding-bottom: 1px; margin-bottom: 1px; }
  #board .ref-phase .ph-h .ph-pct { text-align: right; font-size: 9px; color: var(--p-gry);
    font-weight: 400; font-variant-numeric: tabular-nums; }
  #board .ref-phase .ph-i { display: grid; grid-template-columns: 1fr auto; align-items: baseline; gap: 8px;
    font-size: 10px; line-height: 1.5; padding: 0 0 0 12px; position: relative; }
  #board .ref-phase .ph-i::before { content: '›'; position: absolute; left: 2px; color: var(--p-dim); }
  #board .ref-phase .ph-i .ph-tx { color: var(--p-gry); min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .ref-phase .ph-i.done .ph-tx { color: var(--p-dim); }
  #board .ref-phase .ph-i .ph-tg { white-space: nowrap; display: inline-flex; gap: 4px; }

  /* inline status tag (shared: phase items, plans, lessons) — outline chip */
  #board .ref-tg { font-size: 8px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    border: 1px solid var(--ghost); color: var(--p-dim); padding: 0 4px; white-space: nowrap; }
  #board .ref-tg.t-done   { color: var(--p-grn); border-color: rgba(135,215,135,0.5); }
  #board .ref-tg.t-build  { color: var(--p-cyn); border-color: rgba(135,195,215,0.5); }
  #board .ref-tg.t-debate { color: var(--p-mag); border-color: rgba(195,135,215,0.5); }
  #board .ref-tg.t-decision { color: var(--p-ylw); border-color: rgba(215,215,135,0.5); }

  /* plan rows + next strikes + blockers (가야할길) — flat detail list */
  #board .ref-list { display: flex; flex-direction: column; gap: 1px; }
  #board .ref-list .li { display: grid; grid-template-columns: 1fr auto; align-items: baseline; gap: 8px;
    color: var(--p-gry); font-size: 10px; line-height: 1.5; padding-left: 12px; position: relative; }
  #board .ref-list .li::before { content: '›'; position: absolute; left: 2px; color: var(--p-dim); }
  #board .ref-list .li .li-tx { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .ref-list .li b { color: var(--p-wht); font-weight: 700; }
  #board .ref-list.blockers .li::before { content: '✕'; color: var(--p-red); }
  #board .ref-list.blockers .li, #board .ref-list.blockers .li .li-tx { color: var(--p-red); }

  /* lessons / forensic / debate (배운것) — DETAIL list, FULL takeaway (no clamp).
     One row = title (bold) + status/kind tag, then the full takeaway under it. */
  #board .ref-det { display: flex; flex-direction: column; gap: 5px; }
  #board .ref-det .dl { padding: 2px 0 4px 11px; position: relative;
    border-bottom: 1px dotted rgba(95,135,175,0.10); }
  #board .ref-det .dl::before { content: ''; position: absolute; left: 0; top: 5px; bottom: 5px;
    width: 2px; background: var(--p-cyn); }
  #board .ref-det .dl.k-forensic::before { background: var(--p-red); }
  #board .ref-det .dl .dl-top { display: flex; align-items: baseline; gap: 6px; }
  #board .ref-det .dl .dl-title { color: var(--p-wht); font-size: 10px; font-weight: 700; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  #board .ref-det .dl .dl-take { color: var(--p-gry); font-size: 10px; line-height: 1.45; margin-top: 2px;
    white-space: normal; word-break: break-word; }

  /* anti-pattern list (배운것) — red one-line rows, dense */
  #board .ref-anti { display: flex; flex-direction: column; gap: 1px; }
  #board .ref-anti .an { color: var(--p-red); font-size: 10px; line-height: 1.5;
    padding-left: 12px; position: relative; }
  #board .ref-anti .an::before { content: '✕'; position: absolute; left: 2px; color: var(--p-red); opacity: 0.7; }
  `;
  (function injectRefStyle() {
    const s = document.createElement('style');
    s.id = 'board-reference-style';
    s.textContent = REF_CSS;
    document.head.appendChild(s);
  })();

  function setH(el, label, count) {
    return `<div class="ref-h">${esc(label)}${count != null ? `<span class="ref-hc">${count}</span>` : ''}</div>`;
  }

  // ── 개발 (build) — GET /api/buildlog ───────────────────────────────────────
  function renderBuild() {
    const body = $('build-body'); if (!body) return;
    ensure('/api/buildlog', (d) => {
      if (!$('build-body')) return;        // pane may have been re-skeletoned
      if (d == null) { $('build-body').innerHTML = errorHtml('/api/buildlog'); return; }
      const commits = d.commits || [];
      const log = d.log_tail || [];
      const heat = d.heat || [];
      const tc = d.test_count;

      // test-health badge
      const badge = (tc != null)
        ? `<div class="ref-badge"><span>tests</span><span class="bv">${tc}</span><span>green</span></div>`
        : noneYet();

      // activity heat-strip — opacity ∝ count (no new colour; green w/ alpha)
      const maxC = heat.reduce((m, h) => Math.max(m, h.count || 0), 0) || 1;
      const heatHtml = heat.length
        ? `<div class="ref-heat">` + heat.map(h => {
            const op = 0.15 + 0.85 * Math.min(1, (h.count || 0) / maxC);
            return `<span class="hc" style="opacity:${op.toFixed(2)}" title="${esc(h.day)} · ${h.count || 0} commits"></span>`;
          }).join('') + `</div>`
        : noneYet();

      // commit timeline
      const cHtml = commits.length
        ? commits.map(c => {
            const t = c.type || '';
            return `<div class="rc" title="${esc(c.hash)} · ${esc(c.subject)}">
              <span class="rc-type ${typeClass(t)}">${esc(t || '·')}</span>
              <span class="rc-subj">${esc(subjBody(c.subject, t))}</span>
              <span class="rc-date">${esc(shortDate(c.date))}</span>
            </div>`;
          }).join('')
        : noneYet();

      // wave digest (log_tail) — bold the leading [tag]
      const lHtml = log.length
        ? `<div class="ref-log">` + log.slice().reverse().map(line => {
            const s = esc(line).replace(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})/, '<b>$1</b>');
            return `<div class="lg" title="${esc(line)}">${s}</div>`;
          }).join('') + `</div>`
        : noneYet();

      $('build-body').innerHTML =
        `<div class="ref">
          <div class="ref-sec">${setH(0, 'test-health', null)}${badge}</div>
          <div class="ref-sec">${setH(0, 'commit timeline', commits.length)}${cHtml}</div>
          <div class="ref-sec">${setH(0, 'activity heat (commits/day)', null)}${heatHtml}</div>
          <div class="ref-sec">${setH(0, 'wave digest', log.length)}${lHtml}</div>
        </div>`;
    });
    // first paint while the fetch is in flight
    if (CACHE['/api/buildlog'] && CACHE['/api/buildlog'].state === 'loading' && !body.children.length) {
      body.innerHTML = loadingHtml();
    }
  }

  // ── 가야할길 (path) — GET /api/roadmap ─────────────────────────────────────
  function renderPath() {
    const body = $('path-body'); if (!body) return;
    ensure('/api/roadmap', (d) => {
      if (!$('path-body')) return;
      if (d == null) { $('path-body').innerHTML = errorHtml('/api/roadmap'); return; }
      const phases = d.phases || [];
      const plans = d.plans || [];
      const next = d.next || [];
      const blockers = d.blockers || [];

      // phases — each phase = a group header (name + N/M done) followed by its
      // item rows (text + DONE/BUILD/DEBATE/DECISION tags). Dense detail list.
      const phasesHtml = phases.length
        ? phases.map(p => {
            const items = p.items || [];
            const done = items.filter(it => (it.tags || []).indexOf('DONE') >= 0).length;
            const rows = items.map(it => {
              const tags = it.tags || [];
              const isDone = tags.indexOf('DONE') >= 0;
              const tg = tags.map(tagHtml).join('');
              return `<div class="ph-i${isDone ? ' done' : ''}" title="${esc(it.text)}">
                <span class="ph-tx">${esc(it.text)}</span>
                <span class="ph-tg">${tg}</span>
              </div>`;
            }).join('');
            return `<div class="ref-phase">
              <div class="ph-h"><span>${esc(p.phase)}</span><span class="ph-pct">${done}/${items.length}</span></div>
              ${rows}
            </div>`;
          }).join('')
        : noneYet();

      // plans — flat detail list: plan name (bold) + status tag.
      const plansHtml = plans.length
        ? `<div class="ref-list">` + plans.map(p =>
            `<div class="li" title="${esc(p.name)}"><span class="li-tx"><b>${esc(p.name)}</b></span>${tagHtml(p.status)}</div>`
          ).join('') + `</div>`
        : noneYet();

      // next strikes — bold inline **…** markdown emphasis
      const nextHtml = next.length
        ? `<div class="ref-list">` + next.map(n => `<div class="li"><span class="li-tx">${mdBold(n)}</span></div>`).join('') + `</div>`
        : noneYet();

      // blockers — only when present (endpoint may omit)
      const blockHtml = blockers.length
        ? `<div class="ref-sec">${setH(0, 'blockers', blockers.length)}`
          + `<div class="ref-list blockers">` + blockers.map(b => `<div class="li"><span class="li-tx">${mdBold(b)}</span></div>`).join('') + `</div></div>`
        : '';

      $('path-body').innerHTML =
        `<div class="ref">
          <div class="ref-sec">${setH(0, 'phases P0→P6', phases.length)}${phasesHtml}</div>
          <div class="ref-sec">${setH(0, 'plans', plans.length)}${plansHtml}</div>
          <div class="ref-sec">${setH(0, 'next strikes', next.length)}${nextHtml}</div>
          ${blockHtml}
        </div>`;
    });
    if (CACHE['/api/roadmap'] && CACHE['/api/roadmap'].state === 'loading' && !body.children.length) {
      body.innerHTML = loadingHtml();
    }
  }

  // ── 배운것 (learned) — GET /api/lessons ────────────────────────────────────
  function renderLearned() {
    const body = $('learned-body'); if (!body) return;
    ensure('/api/lessons', (d) => {
      if (!$('learned-body')) return;
      if (d == null) { $('learned-body').innerHTML = errorHtml('/api/lessons'); return; }
      const lessons = d.lessons || [];
      const forensic = d.forensic || [];
      const anti = d.anti_patterns || [];
      const debate = d.debate || d.verdicts || [];   // optional — render if present

      // detail row: title (bold) + status/kind tag, then the FULL takeaway (no
      // truncation, wraps). kindCls drives the left accent (cyan / red).
      const row = (x, kindCls) => {
        const tag = x.status || x.kind || '';
        return `<div class="dl ${kindCls}">
          <div class="dl-top">
            <span class="dl-title" title="${esc(x.name || x.title)}">${esc(x.title || x.name)}</span>
            ${tag ? `<span class="ref-tg">${esc(tag)}</span>` : ''}
          </div>
          ${x.takeaway ? `<div class="dl-take">${esc(x.takeaway)}</div>` : ''}
        </div>`;
      };
      const detList = (rows) => `<div class="ref-det">${rows}</div>`;

      const lessonsHtml = lessons.length
        ? detList(lessons.map(l => row(l, 'k-lesson')).join(''))
        : noneYet();

      const antiHtml = anti.length
        ? `<div class="ref-anti">` + anti.map(a => `<div class="an" title="${esc(a)}">${esc(a)}</div>`).join('') + `</div>`
        : noneYet();

      const forHtml = forensic.length
        ? detList(forensic.map(f => row(f, 'k-forensic')).join(''))
        : noneYet();

      const debateHtml = debate.length
        ? `<div class="ref-sec">${setH(0, 'debate verdicts', debate.length)}`
          + detList(debate.map(v => row(v, 'k-lesson')).join('')) + `</div>`
        : '';

      $('learned-body').innerHTML =
        `<div class="ref">
          <div class="ref-sec">${setH(0, 'lessons', lessons.length)}${lessonsHtml}</div>
          <div class="ref-sec">${setH(0, 'anti-patterns', anti.length)}${antiHtml}</div>
          <div class="ref-sec">${setH(0, 'root-cause (forensic)', forensic.length)}${forHtml}</div>
          ${debateHtml}
        </div>`;
    });
    if (CACHE['/api/lessons'] && CACHE['/api/lessons'].state === 'loading' && !body.children.length) {
      body.innerHTML = loadingHtml();
    }
  }

  // ── AI — GET /api/ai_activity ──────────────────────────────────────────────
  // The truth-first AI tab. Header line states the in-loop trading AI call count
  // (a hard 0 — W3 AI-free core: tick entry / G3 / G4 / G7 are deterministic).
  // Below it the only two places AI IS used, neither in the trade loop: dev
  // debates (GPT + Gemini cross-validation) and the probe escalation seam
  // (observe-only — flagged would-escalate cases; the advisor is not built so
  // there is no GPT answer yet). Two dense aligned tables, English, no cards.
  function aiHeaderHtml(n) {
    const calls = (typeof n === 'number') ? n : 0;
    return `<div class="ai-hdr">
      <span class="ai-hdr-k">In-loop trading AI</span>
      <span class="ai-hdr-v ${calls === 0 ? 'ai-zero' : ''}">${calls} calls</span>
      <span class="ai-hdr-note">AI-free core (W3 cutover · tick entry / G3 / G4 / G7 deterministic). AI is used only for: dev debates (GPT/Gemini) + probe escalation (observe-only).</span>
    </div>`;
  }
  function debatesTable(rows) {
    if (!rows || !rows.length) {
      return `<div class="ref-sec">${setH(0, 'debates (GPT / Gemini)', 0)}<span class="ref-none">none yet</span></div>`;
    }
    const trs = rows.map(d => {
      const gpt = d.gpt ? mdBold(d.gpt) : noneYet();
      const gem = d.gemini ? mdBold(d.gemini) : noneYet();
      const dec = d.decision ? mdBold(d.decision) : noneYet();
      const q = d.question ? mdBold(d.question) : noneYet();
      return `<tr>
        <td class="l ai-date">${esc(shortDate(d.date))}</td>
        <td class="l ai-topic" title="${esc(d.topic || '')}">${esc(d.topic || '—')}</td>
        <td class="l ai-q" title="${esc(d.question || '')}">${q}</td>
        <td class="l ai-gpt" title="${esc(d.gpt || '')}">${gpt}</td>
        <td class="l ai-gem" title="${esc(d.gemini || '')}">${gem}</td>
        <td class="l ai-dec" title="${esc(d.decision || '')}">${dec}</td>
      </tr>`;
    }).join('');
    return `<div class="ref-sec">${setH(0, 'debates · GPT / Gemini cross-validation', rows.length)}
      <table class="ai-tbl"><colgroup>
        <col style="width:6%"><col style="width:18%"><col style="width:21%">
        <col style="width:19%"><col style="width:18%"><col style="width:18%">
       </colgroup><thead><tr>
        <th class="l">DATE</th><th class="l">TOPIC</th><th class="l">QUESTION</th>
        <th class="l">GPT</th><th class="l">GEMINI</th><th class="l">DECISION</th>
      </tr></thead><tbody>${trs}</tbody></table></div>`;
  }
  function escalationsTable(rows) {
    if (!rows || !rows.length) {
      return `<div class="ref-sec">${setH(0, 'probe escalation · observe-only', 0)}<span class="ref-none">none flagged — no ambiguous (would-escalate) probe decisions yet</span></div>`;
    }
    const trs = rows.map(e => {
      const t = e.ts ? new Date(e.ts * 1000).toISOString().slice(11, 19) : '—';
      return `<tr>
        <td class="l ai-date">${esc(t)}</td>
        <td class="l">${esc(e.gate || '—')}</td>
        <td class="l ai-q" title="${esc(e.case || '')}${e.position_id ? ' · pos ' + esc(e.position_id) : ''}">${esc(e.case || '—')}</td>
        <td class="l ai-dec" title="${esc(e.reason || '')}">${esc(e.status || '—')}</td>
      </tr>`;
    }).join('');
    return `<div class="ref-sec">${setH(0, 'probe escalation · observe-only (would-escalate · no advisor yet)', rows.length)}
      <table class="ai-tbl"><colgroup>
        <col style="width:8%"><col style="width:16%"><col style="width:48%"><col style="width:28%">
       </colgroup><thead><tr>
        <th class="l">TIME</th><th class="l">GATE</th><th class="l">CASE</th><th class="l">STATUS</th>
      </tr></thead><tbody>${trs}</tbody></table></div>`;
  }
  function renderAi() {
    const body = $('ai-activity-body'); if (!body) return;
    ensure('/api/ai_activity', (d) => {
      if (!$('ai-activity-body')) return;       // pane re-skeletoned
      if (d == null) { $('ai-activity-body').innerHTML = errorHtml('/api/ai_activity'); return; }
      // Debate section folds away (default collapsed) via the shared #board
      // collapsible machinery (.collapsible/.collapse-toggle/.chev/.collapsed —
      // the delegated toggle is wired by board_tabs.wireCollapsibles). Probe
      // calls stay expanded inside a scroll box so a long stream never pushes
      // the pane. Display-only: the inner tables + data are unchanged.
      const dc = collapseStateDefaultClosed('pb.collapse.ai-debates');
      $('ai-activity-body').innerHTML =
        `<div class="ref">
          ${aiHeaderHtml(d.in_loop_ai_calls)}
          <div class="ai-fold collapsible${dc.cls}" data-collapse-key="pb.collapse.ai-debates">
            <div class="ai-fold-h collapse-toggle" role="button" tabindex="0" aria-expanded="${dc.aria}">
              <span class="chev" aria-hidden="true"></span>
              <span class="ai-fold-t">debates · GPT / Gemini cross-validation</span>
              <span class="ai-fold-n">${(d.debates || []).length}</span>
            </div>
            <div class="ai-fold-b">${debatesTable(d.debates)}</div>
          </div>
          <div class="ai-scroll">${escalationsTable(d.escalations)}</div>
        </div>`;
    });
    if (CACHE['/api/ai_activity'] && CACHE['/api/ai_activity'].state === 'loading' && !body.children.length) {
      body.innerHTML = loadingHtml();
    }
  }

  // AI-tab styles (existing #board palette tokens only — no new colours).
  (function injectAiStyle() {
    if (document.getElementById('board-ai-style')) return;
    const s = document.createElement('style');
    s.id = 'board-ai-style';
    s.textContent = `
    #board .ai-hdr {
      display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 12px;
      border: 1px solid rgba(95,135,175,0.22); border-left: 4px solid var(--p-grn);
      background: rgba(15,19,26,0.55); padding: 5px 10px; font-size: 11px;
    }
    #board .ai-hdr .ai-hdr-k { font-size: 9px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--p-dim); }
    #board .ai-hdr .ai-hdr-v { font-weight: 700; font-variant-numeric: tabular-nums; color: var(--p-wht); }
    #board .ai-hdr .ai-hdr-v.ai-zero { color: var(--p-grn); }
    #board .ai-hdr .ai-hdr-note { color: var(--p-gry); font-size: 10px; }
    #board table.ai-tbl td { vertical-align: top; white-space: normal; line-height: 1.4; }
    #board table.ai-tbl td.ai-date { color: var(--p-dim); font-size: 9px; white-space: nowrap; font-variant-numeric: tabular-nums; }
    #board table.ai-tbl td.ai-topic { color: var(--p-wht); font-weight: 700; }
    #board table.ai-tbl td.ai-q { color: var(--p-gry); }
    #board table.ai-tbl td.ai-gpt { color: var(--p-cyn); }
    #board table.ai-tbl td.ai-gem { color: var(--p-mag); }
    #board table.ai-tbl td.ai-dec { color: var(--p-grn); }
    /* debate fold — non-panel collapsible (default closed); chevron rotates,
       body hides on .collapsed. Reuses the shared #board toggle delegation. */
    #board .ai-fold { display: flex; flex-direction: column; gap: 3px; }
    #board .ai-fold .ai-fold-h { display: flex; align-items: baseline; gap: 6px; cursor: pointer; user-select: none;
      font-size: 9px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; color: var(--p-dim);
      border-bottom: 1px dotted rgba(95,135,175,0.14); padding-bottom: 2px; }
    #board .ai-fold .ai-fold-h:hover .ai-fold-t { color: var(--p-cyn); }
    #board .ai-fold .ai-fold-h:focus-visible { outline: 1px solid var(--p-cyn); outline-offset: 2px; }
    #board .ai-fold .ai-fold-h .ai-fold-n { color: var(--p-cyn); margin-left: 6px; letter-spacing: 0; font-variant-numeric: tabular-nums; }
    #board .ai-fold .ai-fold-h .chev {
      flex: 0 0 auto; width: 0; height: 0; align-self: center;
      border-left: 4px solid var(--p-cyn); border-top: 4px solid transparent; border-bottom: 4px solid transparent;
      transform: rotate(90deg); transition: transform 0.12s ease; opacity: 0.85; }
    #board .ai-fold.collapsed .ai-fold-h .chev { transform: rotate(0deg); }
    #board .ai-fold.collapsed .ai-fold-b { display: none; }
    /* the fold header IS the debate section title → hide the inner duplicate. */
    #board .ai-fold .ai-fold-b > .ref-sec > .ref-h { display: none; }
    /* probe-call section — stays expanded, scrolls if the stream is long. */
    #board .ai-scroll { max-height: 360px; overflow-y: auto; }
    `;
    document.head.appendChild(s);
  })();

  // ── override ext.js's P3 placeholder registrations ─────────────────────────
  T.register('build', renderBuild);
  T.register('path', renderPath);
  T.register('learned', renderLearned);
  T.register('ai', renderAi);
})();
