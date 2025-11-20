import time
import base64
import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import serialization
from src.config import settings


class KalshiClient:
    def __init__(self):
        self.session = requests.Session()
        self.base_url = settings.BASE_URL
        self.key_id = settings.API_KEY_ID
        self.private_key = self._load_private_key()

    def _load_private_key(self):
        try:
            with open(settings.PRIVATE_KEY_PATH, "rb") as key_file:
                return serialization.load_pem_private_key(
                    key_file.read(), password=None
                )
        except FileNotFoundError:
            raise FileNotFoundError(f"Private key not found at {settings.PRIVATE_KEY_PATH}")

    def _sign_request(self, method: str, path: str, timestamp: str) -> str:
        payload = f"{timestamp}{method}{path}".encode('utf-8')
        signature = self.private_key.sign(
            payload,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode('utf-8')

    def request(self, method: str, endpoint: str, params=None, data=None):
        timestamp = str(int(time.time() * 1000))
        path_for_sign = f'/trade-api/v2{endpoint}'

        headers = {
            'KALSHI-ACCESS-KEY': self.key_id,
            'KALSHI-ACCESS-TIMESTAMP': timestamp,
            'KALSHI-ACCESS-SIGNATURE': self._sign_request(method, path_for_sign, timestamp),
            'Content-Type': 'application/json'
        }

        url = f"{self.base_url}{endpoint}"
        response = self.session.request(method, url, headers=headers, params=params, json=data)

        if response.status_code not in [200, 201]:
            print(f"[API ERROR] {response.status_code}: {response.text}")
            return None
        return response.json()

    def get_market_orderbook(self, ticker: str):
        return self.request('GET', f'/markets/{ticker}/orderbook')

    def get_balance(self):
        data = self.request('GET', '/portfolio/balance')
        if data:
            return data.get('balance', 0) / 100.0
        return 0.0

    def place_order(self, ticker: str, side: str, count: int, price: int):
        payload = {
            "action": "buy", "type": "limit", "side": side,
            "ticker": ticker, "count": count,
            "yes_price": price if side == 'yes' else None,
            "no_price": price if side == 'no' else None,
            "buy_max_cost": count * price
        }
        return self.request('POST', '/portfolio/orders', data=payload)