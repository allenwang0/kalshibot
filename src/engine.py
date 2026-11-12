import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.book import Book, parse_orderbook
from src.client import KalshiClient
from src.config import settings
from src.executor import Executor, PaperExecutor, make_executor
from src.ledger import Ledger, utc_now
from src.risk import RiskManager, fee_adjusted_price, taker_fee
from src.strategy import AlphaModel, meeting_date

log = logging.getLogger(__name__)


class TradingBot:
    def __init__(
        self,
        client: Any = None,
        executor: Executor | None = None,
        alpha: Any = None,
        risk: RiskManager | None = None,
        ledger: Ledger | None = None,
    ):
        self.client = client or KalshiClient()
        if ledger is None:
            ledger = executor.ledger if isinstance(executor, PaperExecutor) else None
        self.ledger = ledger or Ledger(settings.DB_PATH)
        self.executor = executor or make_executor(self.client, self.ledger)
        self.alpha = alpha or AlphaModel()
        self.risk = risk or RiskManager()

    def _fetch_books(self, tickers: list[str]) -> dict[str, Book | None]:
        # The client's token bucket keeps concurrent requests under the rate limit.
        with ThreadPoolExecutor(max_workers=settings.MAX_WORKERS) as pool:
            books = pool.map(
                lambda t: parse_orderbook(self.client.get_market_orderbook(t)), tickers
            )
            return dict(zip(tickers, books, strict=True))

    def _alert_edge(self, ticker: str, book: Book, model_p: float) -> None:
        mid = (book.best_yes_bid + book.best_yes_ask) / 200.0
        ask = book.best_yes_ask / 100.0
        edge = abs(model_p - mid) - (fee_adjusted_price(ask) - ask)
        if edge > settings.EDGE_ALERT_THRESHOLD:
            log.warning(
                "EDGE %s model %.3f mid %.3f edge after fee %.3f", ticker, model_p, mid, edge
            )

    def run_cycle(self, markets: list[dict[str, Any]] | None = None) -> None:
        if markets is None:
            markets = self.client.get_markets(settings.SERIES_TICKER)
        self.alpha.refresh(markets)
        # Cancel first, then read the account. A resting order that fills before
        # its cancel lands must already be in the positions used for sizing.
        for market in markets:
            self.executor.cancel_resting_orders(market["ticker"])
        cash = self.executor.get_balance()
        positions = self.executor.get_positions()
        total_exposure = sum(positions.values())
        bankroll = cash + total_exposure  # Kelly sizes against equity, not cash alone
        log.info("bankroll %.2f (cash %.2f, at risk %.2f)", bankroll, cash, total_exposure)
        if bankroll < settings.MIN_BANKROLL_DOLLARS:
            log.warning("bankroll below %.2f, not trading", settings.MIN_BANKROLL_DOLLARS)
            return

        books = self._fetch_books([m["ticker"] for m in markets])
        ts = utc_now()
        for market in markets:
            ticker = market["ticker"]
            book = books[ticker]
            fair_prob = self.alpha.calculate_fair_value(market)
            meeting = meeting_date(market)
            row: dict[str, Any] = {
                "ts": ts,
                "mode": settings.TRADING_MODE,
                "ticker": ticker,
                "strike": market.get("floor_strike"),
                "meeting_date": meeting.isoformat() if meeting else None,
                "model_p": fair_prob,
            }
            if book is not None:
                row.update(
                    best_bid=book.best_yes_bid,
                    best_ask=book.best_yes_ask,
                    mid=(book.best_yes_bid + book.best_yes_ask) / 200.0,
                )
            if book is not None and fair_prob is not None:
                self._alert_edge(ticker, book, fair_prob)
            if book is None:
                row["action"] = "skip: empty book side"
            elif book.spread > settings.MAX_SPREAD_CENTS:
                row["action"] = "skip: wide spread"
            elif fair_prob is None:
                row["action"] = "skip: no model price"
            else:
                price = book.best_yes_ask / 100.0
                target = self.risk.calculate_kelly_size(bankroll, fair_prob, price)
                held = positions.get(ticker, 0.0)
                # Buy only the gap to the target, within the exposure cap and cash.
                alloc = min(target - held, settings.MAX_TOTAL_EXPOSURE - total_exposure, cash)
                # Targets, holdings and caps are all cost basis (Kalshi's
                # market_exposure, fees excluded); cash must also cover the fee.
                count = 0
                if alloc >= settings.MIN_ORDER_DOLLARS:
                    count = min(math.floor(alloc / price), math.floor(book.ask_size))
                while count > 0 and count * price + taker_fee(count, price) > cash:
                    count -= 1
                if count > 0:
                    self.executor.place_order(
                        ticker, "yes", count, book.best_yes_ask, model_p=fair_prob
                    )
                    cost = count * price
                    positions[ticker] = held + cost
                    total_exposure += cost
                    cash -= cost + taker_fee(count, price)
                    row["action"] = f"buy {count}"
                    log.info(
                        "%s buy %d at %d cents, model %.3f",
                        ticker,
                        count,
                        book.best_yes_ask,
                        fair_prob,
                    )
                else:
                    row["action"] = "hold"
            self.ledger.log_cycle(**row)
