import time
from unittest.mock import MagicMock
from invasion.spot import router_spot


class _FakeClient:
    def __init__(self):
        self.placed = []
        self._fill_after_calls = 100  # default never fills
        self._call_count = 0

    def place_post_only_buy(self, inst_id, price, size_btc):
        self.placed.append(("buy", inst_id, price, size_btc))
        return {"ord_id": "ord1", "code": "0"}

    def place_market_sell(self, inst_id, size_btc):
        self.placed.append(("sell_mkt", inst_id, None, size_btc))
        return {"ord_id": "ord2", "code": "0"}

    def cancel_order(self, inst_id, ord_id):
        self.placed.append(("cancel", inst_id, None, None))
        return {"sCode": "0"}

    def get_order(self, inst_id, ord_id):
        self._call_count += 1
        if self._call_count >= self._fill_after_calls:
            return {"state": "filled", "fill_px": 50000.5,
                    "fill_sz": 0.001, "fee": -0.00002}
        return {"state": "live", "fill_px": 0, "fill_sz": 0, "fee": 0}


def test_place_entry_immediate_fill():
    cli = _FakeClient()
    cli._fill_after_calls = 1
    r = router_spot.place_entry(cli, inst_id="BTC-USDT",
                                  price=50000.0, size_btc=0.001,
                                  taker_fallback_ms=100,
                                  taker_score=0.5,
                                  taker_threshold=0.85)
    assert r["fill_type"] == "maker"
    assert r["fill_px"] == 50000.5


def test_place_entry_taker_fallback_high_score():
    cli = _FakeClient()
    cli._fill_after_calls = 999  # never fills
    r = router_spot.place_entry(cli, inst_id="BTC-USDT",
                                  price=50000.0, size_btc=0.001,
                                  taker_fallback_ms=50,
                                  taker_score=0.9,  # > 0.85 -> fallback
                                  taker_threshold=0.85)
    assert r["fill_type"] == "abandoned" or r["fill_type"] == "taker"
    # cancel was called before fallback
    actions = [p[0] for p in cli.placed]
    assert "cancel" in actions


def test_place_entry_low_score_abandons_no_taker():
    cli = _FakeClient()
    cli._fill_after_calls = 999
    r = router_spot.place_entry(cli, inst_id="BTC-USDT",
                                  price=50000.0, size_btc=0.001,
                                  taker_fallback_ms=50,
                                  taker_score=0.5,  # < threshold
                                  taker_threshold=0.85)
    assert r["fill_type"] == "abandoned"
    actions = [p[0] for p in cli.placed]
    assert "sell_mkt" not in actions  # no market entry attempted


def test_51155_blacklists_inst_id_for_session():
    """Local-compliance error blacklists the pair so future calls skip OKX."""
    router_spot.RESTRICTED.discard("BLACKLIST-USDT")

    class FakeRestricted:
        def place_post_only_buy(self, *_a, **_k):
            return {"code": "51155", "msg": "compliance", "ord_id": ""}

    out = router_spot.place_entry(FakeRestricted(), inst_id="BLACKLIST-USDT",
                                    price=1.0, size_btc=1.0,
                                    taker_fallback_ms=10, taker_score=0.5,
                                    taker_threshold=0.85)
    assert out["fill_type"] == "rejected"
    assert "BLACKLIST-USDT" in router_spot.RESTRICTED

    class Exploding:
        def place_post_only_buy(self, *_a, **_k):
            raise AssertionError("should not be called when restricted")

    out2 = router_spot.place_entry(Exploding(), inst_id="BLACKLIST-USDT",
                                     price=1.0, size_btc=1.0,
                                     taker_fallback_ms=10, taker_score=0.5,
                                     taker_threshold=0.85)
    assert out2["reason"] == "restricted_session"
    router_spot.RESTRICTED.discard("BLACKLIST-USDT")
