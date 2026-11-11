from typing import Any, Protocol

from src.client import KalshiClient
from src.config import settings
from src.ledger import Ledger
from src.risk import taker_fee


class Executor(Protocol):
    def get_balance(self) -> float: ...

    def get_positions(self) -> dict[str, float]: ...

    def cancel_resting_orders(self, ticker: str) -> None: ...

    def place_order(
        self, ticker: str, side: str, count: int, price: int, model_p: float | None = None
    ) -> Any: ...


class PaperExecutor:
    """Simulates taker fills at the limit price, with fees, in the ledger. Never calls Kalshi."""

    def __init__(self, ledger: Ledger | str | None = None, bankroll: float | None = None):
        if not isinstance(ledger, Ledger):
            ledger = Ledger(ledger or settings.DB_PATH)
        self.ledger = ledger
        self.bankroll = settings.PAPER_BANKROLL if bankroll is None else bankroll

    def fills(self) -> list[tuple[str, str, int, int]]:
        rows = self.ledger.db.execute("SELECT ticker, side, count, price_cents FROM fills")
        return [(r[0], r[1], r[2], r[3]) for r in rows]

    def get_balance(self) -> float:
        """Starting bankroll minus cost and fees, plus $1 per YES contract settled YES."""
        spent, payout = self.ledger.db.execute(
            "SELECT COALESCE(SUM(f.count * f.price_cents + f.fee_cents), 0),"
            " COALESCE(SUM(CASE WHEN s.result = 'yes' THEN f.count * 100 ELSE 0 END), 0)"
            " FROM fills f LEFT JOIN settlements s ON f.ticker = s.ticker"
        ).fetchone()
        return float(self.bankroll + (payout - spent) / 100.0)

    def get_positions(self) -> dict[str, float]:
        """Cost basis in dollars of unsettled positions, matching Kalshi's market_exposure."""
        rows = self.ledger.db.execute(
            "SELECT ticker, SUM(count * price_cents) FROM fills"
            " WHERE ticker NOT IN (SELECT ticker FROM settlements) GROUP BY ticker"
        )
        return {ticker: cents / 100.0 for ticker, cents in rows}

    def cancel_resting_orders(self, ticker: str) -> None:
        pass  # paper orders fill immediately, so nothing rests

    def place_order(
        self, ticker: str, side: str, count: int, price: int, model_p: float | None = None
    ) -> None:
        fee_cents = round(taker_fee(count, price / 100) * 100)
        self.ledger.add_fill(ticker, side, count, price, fee_cents, model_p)


class LiveExecutor:
    """Sends real orders through KalshiClient."""

    def __init__(self, client: KalshiClient):
        self.client = client

    def get_balance(self) -> float:
        return self.client.get_balance()

    def get_positions(self) -> dict[str, float]:
        return self.client.get_positions()

    def cancel_resting_orders(self, ticker: str) -> None:
        for order in self.client.get_resting_orders(ticker):
            self.client.cancel_order(order["order_id"], ticker)

    def place_order(
        self, ticker: str, side: str, count: int, price: int, model_p: float | None = None
    ) -> Any:
        return self.client.place_order(ticker, side, count, price)


def make_executor(client: KalshiClient, ledger: Ledger) -> Executor:
    if settings.TRADING_MODE == "LIVE":
        return LiveExecutor(client)
    return PaperExecutor(ledger)
