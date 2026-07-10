/* Polaris FLOW — console v2 label-lane engine (Jin 2026-07-11 "제대로 하자",
 * build spec wall_console_blueprint.md §2). Measurement-based DETERMINISTIC
 * lane placement so two nearby labels/chips never render on top of each
 * other — replaces the old id-hash "coin flip" side/drop alternation, which
 * picked an offset purely from the node's own id and had no idea whether a
 * NEIGHBOUR already claimed the same slot (the reported OKX strategy-block
 * vs g6/monitor·g8/reflector label collision at y 0.50-0.57H).
 *
 * Rebuild-time only (boot + the 1s static-layer bake) — O(n log n) sort +
 * a bounded per-lane interval scan, never touched per-frame. Two entry
 * points:
 *   place(items, opts)    — leader-label callouts (rail/regime/probe labels):
 *                            greedy row-lane placement, falls back to a
 *                            dimmed stamp at the first lane rather than a
 *                            hidden/overlapping one (계약 §1 뭉침 금지 —
 *                            dim, never hide, never double-stamp).
 *   placeRow(items, opts) — 1-D horizontal chip runs (watch "[SYM]"
 *                            brackets): left-to-right minimum-pitch sweep +
 *                            a right-to-left relax pass back toward each
 *                            chip's own x, demoting anything that still
 *                            can't fit into a second row.
 *
 * Display-only — this file draws nothing itself; it only computes offsets
 * for wall_spine_field.js / wall_spine_deco.js / wall_console_readouts.js to
 * draw at.
 */
(function () {
  // Shared offscreen measuring context — MUST use the same font the caller
  // will actually draw with (the one real footgun here per the build spec:
  // measuring with one font and drawing with another silently drifts the
  // lane math out from under the real glyph widths).
  let mctx = null;
  function measurer(font) {
    if (!mctx) mctx = document.createElement('canvas').getContext('2d');
    mctx.font = font;
    return mctx;
  }

  // Leader-label rows: 3 vertical offsets x 2 sides = 6 lanes, matching the
  // existing drawLeaderLabel(ctx,x,y,text,color,alpha,side,drop) grammar
  // (wall_spine_field.js) unchanged — this engine only picks WHICH of its
  // (side,drop) slots a given label lands in.
  const LABEL_LANES = [
    [1, 0], [-1, 0], [1, 9], [-1, 9], [1, 18], [-1, 18],
  ];

  /**
   * place(items) — items: [{id, x, y, text, font}]. Returns
   * Map(id -> {side, drop, dimmed}).
   */
  function place(items, opts) {
    const font = (opts && opts.font) || '600 8px JetBrains Mono, monospace';
    const ctx = measurer(font);
    const out = new Map();
    const rows = LABEL_LANES.map(() => []); // per-lane occupied x-intervals
    const sorted = (items || []).slice().sort((a, b) => a.x - b.x);
    sorted.forEach((it) => {
      const w = ctx.measureText(it.text || '').width + 8;
      const x0 = it.x - w / 2, x1 = it.x + w / 2;
      let placed = false;
      for (let li = 0; li < LABEL_LANES.length && !placed; li++) {
        const row = rows[li];
        const clash = row.some((iv) => x0 < iv[1] && x1 > iv[0]);
        if (clash) continue;
        row.push([x0, x1]);
        out.set(it.id, { side: LABEL_LANES[li][0], drop: LABEL_LANES[li][1], dimmed: false });
        placed = true;
      }
      if (!placed) {
        // Fallback chain: never hide, never re-stamp at full alpha over an
        // occupied slot — dim it in lane 0 (계약 §1).
        out.set(it.id, { side: LABEL_LANES[0][0], drop: LABEL_LANES[0][1], dimmed: true });
      }
    });
    return out;
  }

  /**
   * placeRow(items, opts) — items: [{id, x}]. opts: {minPitch, xMin, xMax,
   * rowGap}. Returns Map(id -> {x, row, dy}).
   */
  function placeRow(items, opts) {
    const minPitch = (opts && opts.minPitch) || 20;
    const xMin = (opts && opts.xMin) != null ? opts.xMin : 0;
    const xMax = (opts && opts.xMax) != null ? opts.xMax : 2000;
    const rowGap = (opts && opts.rowGap) || 14;
    const sorted = (items || []).slice().sort((a, b) => a.x - b.x);
    // left -> right minimum-pitch sweep
    let lastX = -Infinity;
    const fwd = sorted.map((it) => {
      const x = Math.max(it.x, lastX + minPitch);
      lastX = x;
      return { id: it.id, x, orig: it.x };
    });
    // right -> left relax back toward each chip's own x (keeps the sweep
    // from dragging the whole row rightward past xMax when the left end is
    // dense — same two-pass grammar as the candidate min-separation relax
    // in wall_spine_field.js).
    let lastXR = Infinity;
    for (let i = fwd.length - 1; i >= 0; i--) {
      const p = fwd[i];
      if (p.x > lastXR - minPitch) p.x = lastXR - minPitch;
      lastXR = p.x;
    }
    const out = new Map();
    fwd.forEach((p) => {
      const overflow = p.x < xMin || p.x > xMax;
      const cx = Math.max(xMin, Math.min(xMax, p.x));
      out.set(p.id, { x: cx, row: overflow ? 1 : 0, dy: overflow ? rowGap : 0 });
    });
    return out;
  }

  window.PolarisConsoleLanes = { place, placeRow };
})();
