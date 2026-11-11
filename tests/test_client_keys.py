from src.client import KalshiClient


def test_client_constructs_without_key_env_vars():
    # PAPER mode only reads public market data, so a missing private key must
    # not stop the bot from starting. Before the fix, open(None) raised TypeError.
    client = KalshiClient()
    assert client.private_key is None


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"orderbook": {"yes": [], "no": []}}


class _RecordingSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json})
        return _FakeResponse()


def test_market_data_request_is_unsigned_without_keys():
    client = KalshiClient()
    client.session = _RecordingSession()
    client.get_market_orderbook("KXFED-TEST")
    headers = client.session.calls[0]["headers"]
    assert "KALSHI-ACCESS-SIGNATURE" not in headers


def test_live_mode_without_keys_fails_at_startup():
    import pytest
    from pydantic import ValidationError
    from src.config import Settings

    with pytest.raises(ValidationError, match="TRADING_MODE=LIVE requires"):
        Settings(_env_file=None, TRADING_MODE="LIVE")
