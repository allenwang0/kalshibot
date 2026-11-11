import sqlite3
import time
from typing import Protocol

from src.config import settings


class Executor(Protocol):
    def get_balance(self) -> float: ...

    def get_positions(self) -> dict: ...

    def cancel_resting_orders(self, ticker: str) -> None: ...

    def place_order(self, ticker: str, side: str, count: int, price: int): ...


class PaperExecutor:
    """Simulates fills at the limit price and records them in SQLite. Never calls Kalshi."""

    def __init__(self, db_path: str = None, bankroll: float = None):
        self.db = sqlite3.connect(db_path or settings.PAPER_DB_PATH)
        self.bankroll = settings.PAPER_BANKROLL if bankroll is None else bankroll
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS fills ("
            "ts REAL, ticker TEXT, side TEXT, count INTEGER, price_cents INTEGER)"
        )
        self.db.commit()

    def fills(self):
        return self.db.execute("SELECT ticker, side, count, price_cents FROM fills").fetchall()

    def get_balance(self) -> float:
        spent = self.db.execute("SELECT COALESCE(SUM(count * price_cents), 0) FROM fills").fetchone()[0]
        return self.bankroll - spent / 100.0

    def get_positions(self) -> dict:
        rows = self.db.execute("SELECT ticker, SUM(count * price_cents) FROM fills GROUP BY ticker")
        return {ticker: cents / 100.0 for ticker, cents in rows}

    def cancel_resting_orders(self, ticker: str) -> None:
        pass  # paper orders fill immediately, so nothing rests

    def place_order(self, ticker: str, side: str, count: int, price: int):
        self.db.execute(
            "INSERT INTO fills VALUES (?, ?, ?, ?, ?)", (time.time(), ticker, side, count, price)
        )
        self.db.commit()


class LiveExecutor:
    """Sends real orders through KalshiClient."""

    def __init__(self, client):
        self.client = client

    def get_balance(self) -> float:
        return self.client.get_balance()

    def get_positions(self) -> dict:
        return self.client.get_positions()

    def cancel_resting_orders(self, ticker: str) -> None:
        for order in self.client.get_resting_orders(ticker):
            self.client.cancel_order(order["order_id"], ticker)

    def place_order(self, ticker: str, side: str, count: int, price: int):
        return self.client.place_order(ticker, side, count, price)


def make_executor(client) -> Executor:
    if settings.TRADING_MODE == "LIVE":
        return LiveExecutor(client)
    return PaperExecutor()
