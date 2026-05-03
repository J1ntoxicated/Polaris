import os, sqlite3, tempfile, time
import pytest

from invasion.spot import store_spot, reconcile_spot


@pytest.fixture
def tmp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    monkeypatch.setattr(store_spot, "_DB_PATH", path)
    store_spot.bootstrap()
    yield path
    os.unlink(path)


def test_pending_fill_zombie_after_5min(tmp_db):
    old = int(time.time()) - 600
    tid = store_spot.insert_trade({
        "ticker": "BTC", "inst_id": "BTC-USDT", "side": "buy",
        "entry_ts": old, "size_usd": 100,
        "strategy_id": "x", "status": "pending_fill",
        "signal_meta": "{}", "cell_key": "k"})
    # Broker shows order not present
    cleaned = reconcile_spot.cleanup_pending_zombies(
        broker_orders={}, max_age_sec=300)
    assert tid in cleaned
    with store_spot.get_conn() as c:
        st = c.execute("SELECT status FROM trades WHERE id=?",
                        [tid]).fetchone()[0]
    assert st == "abandoned"


def test_open_trade_no_balance_marked_zombie(tmp_db):
    tid = store_spot.insert_trade({
        "ticker": "BTC", "inst_id": "BTC-USDT", "side": "buy",
        "entry_ts": int(time.time()) - 4000, "size_usd": 100,
        "strategy_id": "x", "status": "open",
        "signal_meta": "{}", "cell_key": "k"})
    with store_spot.get_conn() as c:
        c.execute("UPDATE trades SET entry_px=50000, qty=0.001 WHERE id=?",
                    [tid])
        c.commit()
    cleaned = reconcile_spot.cleanup_open_zombies(
        broker_balance_qty={"BTC": 0.0}, max_age_sec=3600)
    assert tid in cleaned
    with store_spot.get_conn() as c:
        st, et = c.execute(
            "SELECT status, exit_type FROM trades WHERE id=?",
            [tid]).fetchone()
    assert st == "closed"
    assert et == "zombie_cleanup"


def test_recent_open_trade_not_touched(tmp_db):
    tid = store_spot.insert_trade({
        "ticker": "BTC", "inst_id": "BTC-USDT", "side": "buy",
        "entry_ts": int(time.time()) - 60, "size_usd": 100,
        "strategy_id": "x", "status": "open",
        "signal_meta": "{}", "cell_key": "k"})
    cleaned = reconcile_spot.cleanup_open_zombies(
        broker_balance_qty={"BTC": 0.0}, max_age_sec=3600)
    assert tid not in cleaned
