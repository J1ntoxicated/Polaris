from unittest.mock import patch, MagicMock
from invasion.spot import okx_spot_client


def _client():
    return okx_spot_client.OKXSpotClient(
        api_key="k", secret="s", passphrase="p")


def test_signature_includes_simulated_header():
    cli = _client()
    headers = cli._sign_headers("GET", "/api/v5/account/balance", "")
    assert headers["x-simulated-trading"] == "1"
    assert "OK-ACCESS-KEY" in headers


def test_place_post_only_buy_calls_correct_endpoint():
    cli = _client()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"code": "0",
        "data": [{"ordId": "abc123", "clOrdId": "x", "tag": "",
                   "sCode": "0", "sMsg": ""}]}
    with patch.object(cli._session, "post", return_value=fake_resp) as mp:
        r = cli.place_post_only_buy("BTC-USDT", price=50000.0,
                                       size_btc=0.001)
    assert r["ord_id"] == "abc123"
    args, kwargs = mp.call_args
    body = kwargs["json"]
    assert body["instId"] == "BTC-USDT"
    assert body["ordType"] == "post_only"
    assert body["side"] == "buy"
    assert body["tdMode"] == "cash"
    assert body["px"] == "50000.0"
    assert body["sz"] == "0.001"


def test_place_market_sell():
    cli = _client()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"code": "0",
        "data": [{"ordId": "x", "sCode": "0"}]}
    with patch.object(cli._session, "post", return_value=fake_resp) as mp:
        r = cli.place_market_sell("BTC-USDT", size_btc=0.001)
    body = mp.call_args.kwargs["json"]
    assert body["ordType"] == "market"
    assert body["side"] == "sell"


def test_get_order_returns_status():
    cli = _client()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"code": "0",
        "data": [{"ordId": "abc", "state": "filled",
                   "fillPx": "50001", "fillSz": "0.001",
                   "fee": "-0.00002"}]}
    with patch.object(cli._session, "get", return_value=fake_resp):
        r = cli.get_order("BTC-USDT", "abc")
    assert r["state"] == "filled"
    assert r["fill_px"] == 50001.0


def test_post_4xx_returns_error_dict_no_raise():
    """Auth/quota errors must not crash the runtime — they fold into
    a synthetic dict so exit/entry loops keep going."""
    cli = _client()
    fake_resp = MagicMock(status_code=401, text="unauthorized")
    with patch.object(cli._session, "post", return_value=fake_resp):
        r = cli.place_market_sell("BTC-USDT", size_btc=0.001)
    assert r["code"] == "401"
    assert "401" in r["msg"]


def test_get_4xx_returns_empty_data_no_raise():
    cli = _client()
    fake_resp = MagicMock(status_code=403, text="forbidden")
    with patch.object(cli._session, "get", return_value=fake_resp):
        bal = cli.get_balance_usdt()
    assert bal == 0.0   # empty data → 0


def test_get_balance_usdt():
    cli = _client()
    fake_resp = MagicMock(status_code=200)
    fake_resp.json.return_value = {"code": "0",
        "data": [{"details": [{"ccy": "USDT", "availBal": "9876.5"}]}]}
    with patch.object(cli._session, "get", return_value=fake_resp):
        bal = cli.get_balance_usdt()
    assert bal == 9876.5
