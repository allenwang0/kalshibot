import base64
import random
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from src.config import settings

PrivateKey = RSAPrivateKey | Ed25519PrivateKey

# Kalshi returns 429 when rate limited, with no Retry-After header, and
# recommends exponential backoff. https://docs.kalshi.com/getting_started/rate_limits
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE = 0.5  # seconds
BACKOFF_CAP = 8.0  # seconds
# Without a timeout a stalled connection blocks the loop forever and the
# Timeout retry below never fires.
REQUEST_TIMEOUT = 10.0  # seconds


class TokenBucket:
    """Thread-safe limiter: at most `rate` requests per second, burst of `rate`."""

    def __init__(self, rate: float, sleep: Callable[[float], None] = time.sleep):
        self.rate = rate
        self.tokens = rate
        self.updated = time.monotonic()
        self.lock = threading.Lock()
        self.sleep = sleep

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            self.sleep(wait)


class KalshiAPIError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Kalshi API error {status}: {body}")
        self.status = status
        self.body = body


class KalshiClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.base_url = settings.BASE_URL
        self.key_id = settings.API_KEY_ID
        self.private_key: PrivateKey | None = None
        self._sleep = time.sleep
        self.limiter = TokenBucket(settings.RATE_LIMIT_PER_SEC)

    def _load_private_key(self) -> PrivateKey:
        if not settings.PRIVATE_KEY_PATH:
            raise RuntimeError(
                "KALSHI_PRIVATE_KEY_PATH is not set; it is needed for authenticated requests"
            )
        try:
            with open(settings.PRIVATE_KEY_PATH, "rb") as key_file:
                key = serialization.load_pem_private_key(key_file.read(), password=None)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Private key not found at {settings.PRIVATE_KEY_PATH}"
            ) from None
        if not isinstance(key, RSAPrivateKey | Ed25519PrivateKey):
            raise ValueError("Kalshi keys are RSA or Ed25519")
        return key

    def _sign_request(self, method: str, path: str, timestamp: str) -> str:
        # Message is timestamp + method + path without the query string. RSA keys
        # sign with PSS, SHA-256 and a salt equal to the digest length; Ed25519
        # keys sign the message directly.
        # https://docs.kalshi.com/getting_started/api_keys
        if self.private_key is None:
            self.private_key = self._load_private_key()
        payload = f"{timestamp}{method}{path}".encode()
        if isinstance(self.private_key, Ed25519PrivateKey):
            return base64.b64encode(self.private_key.sign(payload)).decode("utf-8")
        signature = self.private_key.sign(
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def _needs_auth(self, endpoint: str) -> bool:
        # Portfolio endpoints always need a signature. Market data is signed only
        # when keys are configured, so PAPER mode runs without any keys.
        return endpoint.startswith("/portfolio") or bool(self.key_id and settings.PRIVATE_KEY_PATH)

    def request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Send a request, retrying 429, 5xx and connection errors with capped
        exponential backoff and full jitter. Raises KalshiAPIError on failure."""
        url = f"{self.base_url}{endpoint}"
        for attempt in range(MAX_RETRIES + 1):
            headers = {"Content-Type": "application/json"}
            if self._needs_auth(endpoint):
                timestamp = str(int(time.time() * 1000))
                path_for_sign = urlparse(url).path
                headers.update(
                    {
                        "KALSHI-ACCESS-KEY": self.key_id or "",
                        "KALSHI-ACCESS-TIMESTAMP": timestamp,
                        "KALSHI-ACCESS-SIGNATURE": self._sign_request(
                            method, path_for_sign, timestamp
                        ),
                    }
                )

            last_try = attempt == MAX_RETRIES
            self.limiter.acquire()
            try:
                response = self.session.request(
                    method, url, headers=headers, params=params, json=data, timeout=REQUEST_TIMEOUT
                )
            except (requests.ConnectionError, requests.Timeout):
                if last_try:
                    raise
            else:
                if 200 <= response.status_code < 300:
                    return response.json() if response.text else {}
                if response.status_code not in RETRY_STATUSES or last_try:
                    raise KalshiAPIError(response.status_code, response.text)
            self._sleep(random.uniform(0, min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt)))

    def get_market_orderbook(self, ticker: str) -> dict[str, Any]:
        book: dict[str, Any] = self.request("GET", f"/markets/{ticker}/orderbook")
        return book

    def get_markets(self, series_ticker: str, status: str = "open") -> list[dict[str, Any]]:
        # https://docs.kalshi.com/api-reference/market/get-markets
        markets, cursor = [], None
        while True:
            params = {"series_ticker": series_ticker, "status": status, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            data = self.request("GET", "/markets", params=params)
            markets.extend(data.get("markets", []))
            cursor = data.get("cursor")
            if not cursor:
                return markets

    def get_balance(self) -> float:
        # balance is in cents. https://docs.kalshi.com/api-reference/portfolio/get-balance
        data = self.request("GET", "/portfolio/balance")
        return float(data["balance"]) / 100.0

    def get_positions(self) -> dict[str, float]:
        """Dollars at risk (cost basis) per market ticker.
        https://docs.kalshi.com/api-reference/portfolio/get-positions"""
        positions, cursor = {}, None
        while True:
            params = {"count_filter": "position", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            data = self.request("GET", "/portfolio/positions", params=params)
            for p in data.get("market_positions", []):
                positions[p["ticker"]] = float(p["market_exposure_dollars"])
            cursor = data.get("cursor")
            if not cursor:
                return positions

    def get_resting_orders(self, ticker: str) -> list[dict[str, Any]]:
        # https://docs.kalshi.com/api-reference/orders/get-orders
        data = self.request(
            "GET", "/portfolio/orders", params={"ticker": ticker, "status": "resting"}
        )
        orders: list[dict[str, Any]] = data.get("orders", [])
        return orders

    def cancel_order(self, order_id: str, ticker: str) -> Any:
        # https://docs.kalshi.com/api-reference/orders/cancel-order-v2
        return self.request(
            "DELETE", f"/portfolio/events/orders/{order_id}", params={"market_ticker": ticker}
        )

    def place_order(self, ticker: str, side: str, count: int, price: int) -> Any:
        """Limit buy of `count` YES contracts at `price` cents.

        Create Order V2 quotes the YES leg only: side "bid" buys YES. Count and
        price are fixed-point strings, price in dollars. The legacy
        POST /portfolio/orders is being deprecated.
        https://docs.kalshi.com/api-reference/orders/create-order-v2
        One client_order_id per order, reused by request() retries, so a retried
        POST cannot create a second order.
        """
        if side != "yes" or not 1 <= price <= 99 or count < 1:
            raise ValueError(f"refusing order: side={side} count={count} price={price}")
        payload = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": "bid",
            "count": f"{count}.00",
            "price": f"{price / 100:.4f}",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
        }
        return self.request("POST", "/portfolio/events/orders", data=payload)
