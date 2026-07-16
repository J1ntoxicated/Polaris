"""Delegation gate (P2b) — SHADOW-only strategy<->ticker fit-score.

DEMO/PAPER only. Design SSOT: vault/50_research/delegation-gate-blueprint.md.
v1 is SHADOW ONLY: no module in this package ever gates, sizes, or routes a
live signal (flow_not_block, 9-stack sizing chain untouched) — it computes a
deterministic fit-score ranking and logs it alongside the ACTUAL (dumb
asset_class-routed) dispatch for later /debate-judged promotion.
"""

from __future__ import annotations
