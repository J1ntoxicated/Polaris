"""Polaris economics — venue cost models (pure, no I/O).

Centralizes the real vs demo fee schedules so accounting (dashboard) and any
future trading-logic share ONE source of truth instead of scattering bps
constants. See ``fees.py``.
"""
