"""Layer-6 alt-data EVIDENCE collectors (#6).

DEMO/PAPER only — virtual funds. This package supplies **regime/entry
EVIDENCE only** (news/macro/CoinGlass/FRED-style alt-data). It is a SIGNAL
source, NEVER a defensive throttle:

  - It MAY suggest a regime label that still passes the existing
    ``detect_regime_flip`` 2-consecutive-close confirm gate, and write raw
    evidence to ``regime_state.evidence_json`` (read-only context for G3/G7).
  - It MUST NOT skip/reject a signal, set any size/size_mult, write to
    ``learner_blocks`` / ``strategy_risk_state``, trigger an early exit,
    block entry, or add any P&L/blackout halt.

A failing/keyless collector returns ``{}`` and contributes NO evidence — the
price-only ``compute_real_regime`` result stands (correct fallback, NOT a
defensive throttle). ``flow_not_block`` + aggressive bias preserved.

Lives under ``core/altdata/`` (NOT ``core/data/``) so the ``data/`` gitignore
pattern does not hide it.
"""

from polaris.core.altdata._base import AltDataCollector

__all__ = ["AltDataCollector"]
