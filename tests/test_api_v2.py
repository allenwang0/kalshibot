"""Request shapes for the current Kalshi API. Expected values come from
https://docs.kalshi.com/openapi.yaml (CreateOrderV2Request, CancelOrderV2) and
https://docs.kalshi.com/getting_started/api_keys. No network."""

import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.client import KalshiClient
from src.config import settings
from tests.test_errors import _client, _Resp


def test_default_host_is_the_documented_production_server():
    # api.kalshi.com is not a documented host; orders sent there go nowhere.
    assert settings.BASE_URL == "https://external-api.kalshi.com/trade-api/v2"


def test_order_uses_create_order_v2_with_dollar_strings():
    c = _client(
        _Resp(201, {"order_id": "x", "fill_count": "0.00", "remaining_count": "3.00", "ts_ms": 1})
    )
    c.place_order("KXFED-26DEC-T3.75", "yes", 3, 48)
    call = c.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == settings.BASE_URL + "/portfolio/events/orders"
    body = dict(call["json"])
    body.pop("client_order_id")
    assert body == {
        "ticker": "KXFED-26DEC-T3.75",
        "side": "bid",
        "count": "3.00",
        "price": "0.4800",
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
    }


@pytest.mark.parametrize(
    "side,count,price", [("no", 1, 50), ("yes", 0, 50), ("yes", 1, 0), ("yes", 1, 100)]
)
def test_order_outside_the_bot_strategy_is_refused_before_any_request(side, count, price):
    c = _client()
    with pytest.raises(ValueError):
        c.place_order("KXFED-X", side, count, price)
    assert c.session.calls == []


def test_cancel_uses_v2_path_with_market_ticker_for_routing():
    c = _client(_Resp(200, {"order_id": "o1", "client_order_id": "", "reduced_by": "3.00"}))
    c.cancel_order("o1", "KXFED-X")
    call = c.session.calls[0]
    assert (call["method"], call["url"]) == (
        "DELETE",
        settings.BASE_URL + "/portfolio/events/orders/o1",
    )


def _signed_headers(key, endpoint, params=None):
    c = KalshiClient()
    c.key_id = "test-key-id"
    c.private_key = key
    seen = {}

    class Session:
        def request(self, method, url, headers=None, **kwargs):
            seen.update(headers)
            return _Resp(200, {"balance": 1})

    c.session = Session()
    c.request("GET", endpoint, params=params)
    return seen


def test_rsa_signature_uses_digest_length_salt_and_full_path_without_query():
    # Kalshi verifies PSS with salt = digest length. A MAX_LENGTH salt does not verify.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    h = _signed_headers(key, "/portfolio/orders", params={"status": "resting"})
    message = (h["KALSHI-ACCESS-TIMESTAMP"] + "GET" + "/trade-api/v2/portfolio/orders").encode()
    key.public_key().verify(
        base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_ed25519_key_signs_the_message_directly():
    key = Ed25519PrivateKey.generate()
    h = _signed_headers(key, "/portfolio/balance")
    message = (h["KALSHI-ACCESS-TIMESTAMP"] + "GET" + "/trade-api/v2/portfolio/balance").encode()
    key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), message)
    with pytest.raises(InvalidSignature):
        key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), message + b"x")


def test_get_market_returns_the_market_object():
    c = _client(
        _Resp(
            200,
            {
                "market": {
                    "ticker": "KXFED-26OCT-T5.25",
                    "status": "finalized",
                    "result": "no",
                    "settlement_ts": "2026-10-28T18:17:54Z",
                }
            },
        )
    )
    assert c.get_market("KXFED-26OCT-T5.25")["result"] == "no"
    assert c.session.calls[0]["url"].endswith("/markets/KXFED-26OCT-T5.25")
