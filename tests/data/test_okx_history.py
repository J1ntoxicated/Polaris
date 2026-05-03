"""tests/data/test_okx_history.py — Pure parser test (mocked I/O)."""
from __future__ import annotations

import pytest

from src.data.okx_history import _parse_okx_response


class TestParseOKXResponse:
    def test_valid_response(self) -> None:
        payload = {
            "code": "0",
            "msg": "",
            "data": [
                # newest first
                ["1700003600000", "100", "105", "99", "103", "1500", "15.0", "1545.0", "1"],
                ["1700000000000", "95", "102", "94", "100", "2000", "21.0", "2025.0", "1"],
            ],
        }
        candles = _parse_okx_response(payload)
        assert len(candles) == 2
        # 시간순 정렬 확인
        assert candles[0].timestamp_ms == 1700000000000
        assert candles[1].timestamp_ms == 1700003600000

    def test_empty_data(self) -> None:
        payload = {"code": "0", "msg": "", "data": []}
        assert _parse_okx_response(payload) == []

    def test_error_code(self) -> None:
        payload = {"code": "50001", "msg": "rate limit"}
        with pytest.raises(RuntimeError, match="OKX error"):
            _parse_okx_response(payload)

    def test_skips_malformed_rows(self) -> None:
        payload = {
            "code": "0",
            "data": [
                ["1700003600000", "100", "105", "99", "103", "1500"],  # valid
                ["bad_row"],  # malformed → skip
                ["1700000000000", "100", "100", "100", "100", "0"],  # valid
            ],
        }
        candles = _parse_okx_response(payload)
        assert len(candles) == 2
