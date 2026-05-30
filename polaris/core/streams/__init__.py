"""Stream layer — routing/isolation dimension (NOT a sizing damper).

A *stream* binds a venue + product_class to a Track, sizing profile, strategy
roster, and adapter dispatch. It is the SSOT that replaces the venue-binary
``if venue == "okx" ... else ...`` branches scattered across the codebase.

This package carries NO multiplier: stream supplies leverage (notional source),
caps (headroom_min inputs), entry gates, and adapter dispatch only — the T4
multiplicative chain is untouched (9-stack collapse stays blocked).
"""

from polaris.core.streams.config import (
    GUARD_TOKEN_BY_PRODUCT_CLASS,
    STREAMS,
    VENUE_TO_STREAM,
    SizingProfile,
    StreamConfig,
    StreamId,
    StreamProfile,
    Track,
    asset_class_allowed_for_venue,
    derive_leverage,
    fallback_leverage_for_asset_class,
    guard_token_for_product_class,
    resolve_stream,
    resolve_stream_profile,
)

__all__ = [
    "GUARD_TOKEN_BY_PRODUCT_CLASS",
    "STREAMS",
    "VENUE_TO_STREAM",
    "SizingProfile",
    "StreamConfig",
    "StreamId",
    "StreamProfile",
    "Track",
    "asset_class_allowed_for_venue",
    "derive_leverage",
    "fallback_leverage_for_asset_class",
    "guard_token_for_product_class",
    "resolve_stream",
    "resolve_stream_profile",
]
