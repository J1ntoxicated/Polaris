/* global React */
const { useState, useEffect, useMemo } = React;

// Polaris palette — keep in sync with /colors_and_type.css
const C = {
  bg:    '#0a0d12',
  bgAlt: '#0f131a',
  bgR:   '#5f0000',
  bgY:   '#5f5f00',
  blue:  '#5f87af',
  navy:  '#005f87',
  grn:   '#87d787',
  red:   '#d78787',
  cyn:   '#87d7ff',
  ylw:   '#d7d787',
  mag:   '#d7afff',
  blu:   '#87afd7',
  org:   '#ffaf87',
  wht:   '#dadada',
  gry:   '#a8a8a8',
  dim:   '#6c6c6c',
  ghost: '#626262',
};

// Repeat a single char without prettier mangling
const rep = (ch, n) => Array(Math.max(0, n) + 1).join(ch);

const Hline = ({ label, color = C.blue, labelColor = C.gry, width = 60 }) => {
  const sep = label ? ` ${label} ` : '';
  const side = Math.max(3, Math.floor((width - sep.length) / 2));
  const rest = Math.max(0, width - side - sep.length);
  return (
    <span>
      <span style={{ color }}>{rep('─', side)}</span>
      {label ? <span style={{ color: labelColor }}>{sep}</span> : null}
      <span style={{ color }}>{rep('─', rest)}</span>
    </span>
  );
};

const Cell = ({ label, value, valueColor = C.wht, labelColor = C.gry, bold = true }) => (
  <span>
    <span style={{ color: labelColor }}>{label}</span>{' '}
    <span style={{ color: valueColor, fontWeight: bold ? 700 : 400 }}>{value}</span>
  </span>
);

const Glyph = ({ ch, color = C.blue }) => <span style={{ color }}>{ch}</span>;

const Bar = ({ pct, width = 8, kind = 'gradient' }) => {
  const filled = Math.max(0, Math.min(width, Math.round((pct / 100) * width)));
  const empty = width - filled;
  if (kind === 'gradient') {
    const t1 = Math.min(filled, Math.floor(width / 3));
    const t2 = Math.min(Math.max(0, filled - t1), Math.floor(width / 3));
    const t3 = Math.max(0, filled - t1 - t2);
    return (
      <span>
        <span style={{ color: C.red }}>{rep('█', t1)}</span>
        <span style={{ color: C.ylw }}>{rep('█', t2)}</span>
        <span style={{ color: C.grn }}>{rep('█', t3)}</span>
        <span style={{ color: C.dim }}>{rep('░', empty)}</span>
      </span>
    );
  }
  // simple
  const col = pct >= 60 ? C.grn : pct >= 35 ? C.ylw : C.red;
  return (
    <span>
      <span style={{ color: col }}>{rep('█', filled)}</span>
      <span style={{ color: C.dim }}>{rep('░', empty)}</span>
    </span>
  );
};

const ThreatBar = ({ pnl, ageS }) => {
  let n = 0;
  if (pnl < 0) n++; if (pnl < -0.5) n++; if (pnl < -1) n++;
  if (ageS > 1800) n++; if (ageS > 3600) n++;
  n = Math.min(5, n);
  const col = n >= 4 ? C.red : n >= 2 ? C.ylw : C.grn;
  return <span style={{ color: col }}>{rep('▓', n)}{rep('░', 5 - n)}</span>;
};

const Spark = ({ data, color = C.cyn, width = 13 }) => {
  const ramp = ' ▁▂▃▄▅▆▇█';
  const d = data.slice(-width);
  const lo = Math.min(...d), hi = Math.max(...d);
  const r = hi - lo || 1;
  return (
    <span style={{ color }}>
      {d.map((v, i) => ramp[Math.min(8, Math.max(1, Math.round(((v - lo) / r) * 7) + 1))]).join('')}
    </span>
  );
};

const Badge = ({ icon, text, level = 'live' }) => {
  const map = {
    live:   { bg: '#2d6a2d', fg: '#fff' },
    ok:     { bg: '#2d6a2d', fg: '#fff' },
    warn:   { bg: '#6a6a2d', fg: '#fff' },
    danger: { bg: '#6a2d2d', fg: '#fff' },
    info:   { bg: '#2d6a6a', fg: '#fff' },
    off:    { bg: '#1a1f2a', fg: C.dim },
  };
  const s = map[level] || map.info;
  return (
    <span style={{ background: s.bg, color: s.fg, fontWeight: 700, padding: '0 6px' }}>
      {icon} {text}
    </span>
  );
};

const PnL = ({ v, suffix = '' }) => {
  const col = v > 0 ? C.grn : v < 0 ? C.red : C.dim;
  const sign = v > 0 ? '+' : '';
  return <span style={{ color: col, fontWeight: 700 }}>{sign}{v.toFixed(suffix === '%' ? 2 : 1)}{suffix}</span>;
};

const useTick = (ms = 1000) => {
  const [t, setT] = useState(0);
  useEffect(() => { const id = setInterval(() => setT(x => x + 1), ms); return () => clearInterval(id); }, [ms]);
  return t;
};

Object.assign(window, { C, rep, Hline, Cell, Glyph, Bar, ThreatBar, Spark, Badge, PnL, useTick });
