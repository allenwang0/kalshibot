import math
from src.book import parse_orderbook
from src.client import KalshiClient
from src.config import settings
from src.executor import make_executor
from src.risk import RiskManager
from src.strategy import AlphaModel


class TradingBot:
    def __init__(self, client=None, executor=None, alpha=None, risk=None):
        self.client = client or KalshiClient()
        self.executor = executor or make_executor(self.client)
        self.alpha = alpha or AlphaModel()
        self.risk = risk or RiskManager()

    def run_cycle(self, markets=None):
        if markets is None:
            markets = self.client.get_markets(settings.SERIES_TICKER)
        cash = self.executor.get_balance()
        positions = self.executor.get_positions()
        total_exposure = sum(positions.values())
        bankroll = cash + total_exposure  # Kelly sizes against equity, not cash alone
        print(f"Current Bankroll: ${bankroll:.2f} (cash ${cash:.2f})")
        if bankroll < 1: return

        for market in markets:
            ticker = market['ticker']
            self.executor.cancel_resting_orders(ticker)
            book = parse_orderbook(self.client.get_market_orderbook(ticker))
            if book is None or book.spread > settings.MAX_SPREAD_CENTS: continue

            best_ask_cents = book.best_yes_ask
            market_price = best_ask_cents / 100.0

            fair_prob = self.alpha.calculate_fair_value(market)
            if fair_prob is None:
                print(f"[{ticker}] No model price, skipping")
                continue
            target = self.risk.calculate_kelly_size(bankroll, fair_prob, market_price)
            held = positions.get(ticker, 0.0)
            # Buy only the gap to the target, within the overall exposure cap and cash.
            alloc = min(target - held, settings.MAX_TOTAL_EXPOSURE - total_exposure, cash)

            print(f"[{ticker}] Price: {market_price:.2f} | Model: {fair_prob:.2f} | Alloc: ${alloc:.2f}")

            if alloc > 1.0:
                count = min(math.floor(alloc / market_price), math.floor(book.ask_size))
                if count > 0:
                    self.executor.place_order(ticker, "yes", count, best_ask_cents)
                    cost = count * market_price
                    positions[ticker] = held + cost
                    total_exposure += cost
                    cash -= cost
                    print(f">>> ORDER SENT: Buy {count} @ {best_ask_cents}¢")