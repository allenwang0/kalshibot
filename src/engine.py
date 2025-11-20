import math
from src.client import KalshiClient
from src.risk import RiskManager
from src.strategy import AlphaModel


class TradingBot:
    def __init__(self):
        self.client = KalshiClient()
        self.alpha = AlphaModel()
        self.risk = RiskManager()

    def run_cycle(self, ticker_list):
        bankroll = self.client.get_balance()
        print(f"Current Bankroll: ${bankroll:.2f}")
        if bankroll < 1: return

        for ticker in ticker_list:
            ob = self.client.get_market_orderbook(ticker)
            if not ob or 'yes' not in ob.get('orderbook', {}): continue

            yes_book = ob['orderbook']['yes']
            if not yes_book: continue

            best_ask_cents = yes_book[0][0]
            market_price = best_ask_cents / 100.0

            fair_prob = self.alpha.calculate_fair_value(ticker)
            alloc = self.risk.calculate_kelly_size(bankroll, fair_prob, market_price)

            print(f"[{ticker}] Price: {market_price:.2f} | Model: {fair_prob:.2f} | Alloc: ${alloc:.2f}")

            if alloc > 1.0:
                count = math.floor(alloc / market_price)
                if count > 0:
                    self.client.place_order(ticker, "yes", count, best_ask_cents)
                    print(f">>> ORDER SENT: Buy {count} @ {best_ask_cents}¢")