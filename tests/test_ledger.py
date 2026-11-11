import pytest

import src.engine as engine
from src.client import TokenBucket
from src.executor import PaperExecutor
from tests.fakes import FakeAlpha, FakeClient

MARKET = {
    "ticker": "KXFED-26DEC-T3.50",
    "strike_type": "greater",
    "floor_strike": 3.5,
    "close_time": "2026-12-09T19:00:00Z",
}


def test_every_market_is_logged_each_cycle_with_model_and_book():
    # The evaluation needs one row per market per cycle, traded or not.
    ex = PaperExecutor(":memory:", 1000.0)
    wide = {"ticker": "KXFED-WIDE"}

    class Client(FakeClient):
        def get_market_orderbook(self, ticker):
            if ticker == "KXFED-WIDE":
                return {"orderbook": {"yes": [[10, 5]], "no": [[50, 5]]}}
            return self.book

    bot = engine.TradingBot(client=Client(), executor=ex, alpha=FakeAlpha(0.9))
    bot.run_cycle([MARKET, wide])
    rows = ex.ledger.db.execute(
        "SELECT ticker, meeting_date, model_p, best_bid, best_ask, mid, action FROM cycles"
    ).fetchall()
    assert rows[0][:6] == ("KXFED-26DEC-T3.50", "2026-12-09", 0.9, 45, 48, 0.465)
    assert rows[0][6].startswith("buy ")
    assert rows[1][0] == "KXFED-WIDE" and rows[1][6] == "skip: wide spread"


def test_paper_cash_pays_fees_and_settlement_returns_the_payout():
    ex = PaperExecutor(":memory:", 100.0)
    ex.place_order("A", "yes", 10, 50, model_p=0.7)  # $5.00 + 18 cents fee
    ex.place_order("B", "yes", 10, 50, model_p=0.7)
    assert ex.get_balance() == pytest.approx(100 - 2 * 5.18)
    assert ex.get_positions() == {"A": 5.0, "B": 5.0}
    ex.ledger.set_settlement("A", "yes", "2026-12-09T19:05:00+00:00")
    ex.ledger.set_settlement("B", "no", "2026-12-09T19:05:00+00:00")
    assert ex.get_positions() == {}
    assert ex.get_balance() == pytest.approx(100 - 2 * 5.18 + 10.0)


def test_token_bucket_spaces_requests_at_the_rate_limit():
    waits = []
    bucket = TokenBucket(rate=5, sleep=lambda s: (waits.append(s), _advance(bucket, s)))
    for _ in range(10):
        bucket.acquire()
    # The first 5 use the burst; the next 5 each wait about 1/5 s.
    assert len(waits) >= 5 and sum(waits) == pytest.approx(1.0, rel=0.05)


def _advance(bucket, seconds):
    bucket.updated -= seconds  # simulate time passing without sleeping
