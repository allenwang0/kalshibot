import json

import pytest

import src.client as client_mod
from src.client import KalshiClient
from src.strategy import AlphaModel


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.text = json.dumps(payload) if payload is not None else "error"
        self._payload = payload

    def json(self):
        return self._payload


class _ScriptedSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None):
        self.calls.append({"method": method, "url": url, "json": json})
        return self.responses.pop(0)


def _client(*responses):
    c = KalshiClient()
    c.session = _ScriptedSession(*responses)
    c.key_id = "test-key-id"
    c._sign_request = lambda method, path, ts: "sig"  # no real key in tests
    c._sleep = lambda seconds: c.sleeps.append(seconds)
    c.sleeps = []
    return c


def test_balance_500_raises_instead_of_reporting_zero():
    # A failed balance call used to become a $0.00 bankroll, hiding the outage.
    c = _client(*[_Resp(500)] * 10)
    with pytest.raises(Exception) as err:
        c.get_balance()
    assert err.value.status == 500


def test_5xx_is_retried_with_capped_backoff():
    c = _client(*[_Resp(503)] * 10)
    with pytest.raises(client_mod.KalshiAPIError):
        c.get_balance()
    assert len(c.session.calls) == client_mod.MAX_RETRIES + 1
    assert all(0 <= s <= client_mod.BACKOFF_CAP for s in c.sleeps)


def test_429_then_success_returns_data():
    c = _client(_Resp(429), _Resp(200, {"balance": 12345}))
    assert c.get_balance() == 123.45


def test_4xx_is_not_retried():
    c = _client(_Resp(400), _Resp(200, {"balance": 1}))
    with pytest.raises(client_mod.KalshiAPIError):
        c.get_balance()
    assert len(c.session.calls) == 1


def test_retried_order_reuses_one_client_order_id_and_sends_one_price():
    # A retried POST must carry the same client_order_id so it cannot fill twice.
    c = _client(_Resp(502), _Resp(201, {"order": {"order_id": "x"}}))
    c.place_order("KXFED-TEST", "yes", 3, 48)
    first, second = (call["json"] for call in c.session.calls)
    assert first["client_order_id"] and first["client_order_id"] == second["client_order_id"]
    assert first["yes_price"] == 48 and "no_price" not in first


def test_failed_cme_fetch_is_not_cached():
    # A CME outage used to blank the model for the full cache window.
    class FlakyCME:
        calls = 0

        def get_probabilities(self):
            FlakyCME.calls += 1
            return {} if FlakyCME.calls == 1 else {"2025-12-10": []}

    alpha = AlphaModel()
    alpha.cme = FlakyCME()
    alpha._update_data()
    assert alpha.last_update == 0
    alpha._update_data()
    assert FlakyCME.calls == 2 and "2025-12-10" in alpha.cache
