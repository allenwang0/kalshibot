"""Offline stand-ins for KalshiClient and AlphaModel. Values are fabricated."""

BOOK = {"orderbook": {"yes": [[40, 10], [45, 5]], "no": [[50, 8], [52, 3]]}}


class FakeClient:
    def __init__(self, book=BOOK, balance=500.0):
        self.book = book
        self.balance = balance
        self.live_orders = []

    def get_balance(self):
        return self.balance

    def get_market_orderbook(self, ticker):
        return self.book

    def place_order(self, ticker, side, count, price):
        self.live_orders.append((ticker, side, count, price))
        return {"order": {"order_id": "fake"}}


class FakeAlpha:
    def __init__(self, prob=0.90):
        self.prob = prob

    def calculate_fair_value(self, market):
        return self.prob
