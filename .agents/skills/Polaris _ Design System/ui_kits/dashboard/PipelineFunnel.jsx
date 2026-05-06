/* global React, C, Hline, Bar */

function PipelineFunnel() {
  const stages = [
    { name: 'scan',       n: 1247392, col: C.wht, rest: 100 },
    { name: 'candidate',  n: 82,      col: C.cyn, rest: 64 },
    { name: 'pass S+H',   n: 14,      col: C.cyn, rest: 32 },
    { name: 'AI advise',  n: 11,      col: C.mag, rest: 22 },
    { name: 'exec',       n: 7,       col: C.grn, rest: 14 },
  ];
  return (
    <div style={{ fontSize: 11, lineHeight: 1.55, padding: '0 10px' }}>
      <Hline label="PIPELINE & FUNNEL" width={120} />
      {stages.map((s, i) => (
        <div key={s.name} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ width: '14ch', color: C.gry }}>{s.name}</span>
          <span style={{ width: '12ch', color: s.col, fontWeight: 700, textAlign: 'right' }}>{s.n.toLocaleString()}</span>
          <Bar pct={s.rest} width={20} kind="simple" />
          <span style={{ color: C.dim, fontSize: 10 }}>{s.rest}%</span>
        </div>
      ))}
      <div style={{ color: C.dim, paddingTop: 4 }}>
        <span style={{ color: C.gry }}>drop  </span>
        <span style={{ color: C.red }}>S-gate 1245</span>{'   '}
        <span style={{ color: C.red }}>H-gate 412</span>{'   '}
        <span style={{ color: C.ylw }}>AI 27</span>{'   '}
        <span style={{ color: C.ylw }}>stale 8</span>
      </div>
    </div>
  );
}

window.PipelineFunnel = PipelineFunnel;
