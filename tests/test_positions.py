import random

import src.engine as engine
from src.config import settings
from src.executor import PaperExecutor
from tests.fakes import FakeAlpha, FakeClient

DEEP_BOOK = {"orderbook": {"yes": [[45, 1000]], "no": [[52, 1000]]}}


def _paper_bot(tmp_path, client, bankroll=1000.0, prob=0.90):
    ex = PaperExecutor(str(tmp_path / "paper.db"), bankroll)
    return engine.TradingBot(client=client, executor=ex, alpha=FakeAlpha(prob)), ex


def test_second_cycle_with_target_held_sends_nothing(tmp_path):
    # Without position tracking every 60s cycle re-sized from the full bankroll
    # and stacked another order on top of the last one.
    bot, ex = _paper_bot(tmp_path, FakeClient(book=DEEP_BOOK))
    bot.run_cycle(["KXFED-TEST-T4.00"])
    assert len(ex.fills()) == 1
    bot.run_cycle(["KXFED-TEST-T4.00"])
    assert len(ex.fills()) == 1


def test_live_cancels_resting_orders_and_buys_only_the_difference(monkeypatch):
    class LiveFake(FakeClient):
        def __init__(self):
            super().__init__(book=DEEP_BOOK, balance=950.0)
            self.cancelled = []

        def get_positions(self):
            return {"KXFED-TEST-T4.00": 50.0}

        def get_resting_orders(self, ticker):
            return [{"order_id": "o1", "ticker": ticker}]

        def cancel_order(self, order_id, ticker):
            self.cancelled.append(order_id)

    from src.executor import LiveExecutor
    client = LiveFake()
    bot = engine.TradingBot(client=client, executor=LiveExecutor(client), alpha=FakeAlpha())
    bot.run_cycle(["KXFED-TEST-T4.00"])
    assert client.cancelled == ["o1"]
    (_, _, count, price), = client.live_orders
    # target is capped at MAX_POSITION_SIZE dollars; $50 is already held
    assert count * price / 100 <= settings.MAX_POSITION_SIZE - 50.0


def test_exposure_caps_hold_over_random_books_and_bankrolls(tmp_path):
    # Property check (seeded, so failures reproduce): across random books,
    # bankrolls, model probabilities and repeated cycles, dollars at risk never
    # exceed MAX_POSITION_SIZE per market or MAX_TOTAL_EXPOSURE overall.
    rng = random.Random(1234)
    tickers = [f"KXFED-TEST-T{i}" for i in range(6)]
    for trial in range(200):
        bankroll = rng.uniform(1, 20000)

        class RandomClient(FakeClient):
            def get_market_orderbook(self, ticker):
                bid = rng.randint(1, 97)
                ask = rng.randint(bid + 1, 99)
                return {"orderbook": {"yes": [[bid, rng.randint(1, 5000)]],
                                      "no": [[100 - ask, rng.randint(1, 5000)]]}}

        ex = PaperExecutor(":memory:", bankroll)
        bot = engine.TradingBot(client=RandomClient(), executor=ex, alpha=FakeAlpha(rng.random()))
        for _ in range(3):
            bot.alpha.prob = rng.random()
            bot.run_cycle(tickers)
        positions = {}
        for ticker, _, count, price in ex.fills():
            positions[ticker] = positions.get(ticker, 0.0) + count * price / 100
        assert sum(positions.values()) <= settings.MAX_TOTAL_EXPOSURE + 1e-9, trial
        assert all(v <= settings.MAX_POSITION_SIZE + 1e-9 for v in positions.values()), trial
        assert ex.get_balance() >= -1e-9, trial
