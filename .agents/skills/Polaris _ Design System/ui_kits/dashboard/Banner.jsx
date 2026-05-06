/* global React, C, Glyph, Cell, Badge, Spark, useTick */

function Banner() {
  const t = useTick(1000);
  const tickN = 12847 + t;
  const now = new Date();
  const ts = now.toISOString().slice(0, 19).replace('T', ' ');

  return (
    <div style={{ padding: '6px 10px', lineHeight: 1.55, fontSize: 12, whiteSpace: 'nowrap' }}>
      <div>
        <Glyph ch="★" color={C.blue} />{' '}
        <span style={{ color: C.wht, fontWeight: 700, letterSpacing: '0.06em' }}>POLARIS</span>{' '}
        <Glyph ch="✦" color={C.blue} />{' '}
        <Badge icon="◉" text="LIT" level="live" />{' '}
        <span style={{ color: C.dim }}>[</span><span style={{ color: C.red, fontWeight: 700 }}>RISK_OFF</span><span style={{ color: C.dim }}>]</span>{' '}
        <span style={{ color: C.red, fontWeight: 700 }}>FULL ATTACK</span>
        {'  '}
        <span style={{ color: C.grn, fontWeight: 700 }}>★★★</span>{' '}
        <span style={{ color: C.blue, fontWeight: 700 }}>NSI</span>{' '}
        <span style={{ color: C.cyn }}>████████</span>{' '}
        <span style={{ color: C.grn, fontWeight: 700 }}>78</span>
        {'  '}
        <Cell label="Tick:" value={tickN.toLocaleString()} valueColor={C.cyn} />
        {'  '}
        <span style={{ color: C.gry }}>{ts}</span>
      </div>

      <div>
        <Cell label="OKX" value="$52,103" valueColor={C.wht} labelColor={C.cyn} />
        {'  '}
        <Cell label="CAP" value="$31,994" valueColor={C.wht} labelColor={C.blu} />
        {'  '}
        <Cell label="ALP" value="$16,205" valueColor={C.wht} labelColor={C.ylw} />
        {'   '}
        <Cell label="Pos:" value="7" valueColor={C.cyn} />
        {'  '}
        <Cell label="PnL:" value="+$24.3" valueColor={C.grn} />
        {'  '}
        <Cell label="WR:" value="54%" valueColor={C.grn} />
        {'  '}
        <Cell label="F&G:" value="19" valueColor={C.red} />
        {'  '}
        <Cell label="Up:" value="1h30m" valueColor={C.cyn} />
      </div>

      <div>
        <span style={{ color: C.ghost }}>{'═'.repeat(8)}</span>{' '}
        <span style={{ color: C.gry }}>24h: 242 trades  131W  Net: -$120.3  WR: 54%</span>{' '}
        <span style={{ color: C.ghost }}>{'═'.repeat(60)}</span>
      </div>
    </div>
  );
}

window.Banner = Banner;
