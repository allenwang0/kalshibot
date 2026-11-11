import time

import src.engine as engine
from src.executor import PaperExecutor
from src.strategy import AlphaModel
from tests.fakes import FakeClient

# Fabricated markets in the shape of GET /markets (ticker, strike_type,
# floor_strike, close_time). https://docs.kalshi.com/api-reference/market/get-markets
MARKETS = [
    {"ticker": "KXFED-25DEC-T3.75", "strike_type": "greater", "floor_strike": 3.75,
     "close_time": "2025-12-10T18:55:00Z"},
    {"ticker": "KXFED-26JAN-T3.50", "strike_type": "greater", "floor_strike": 3.5,
     "close_time": "2026-01-28T18:55:00Z"},
    {"ticker": "KXFED-26MAR-T3.25", "strike_type": "greater", "floor_strike": 3.25,
     "close_time": "2026-03-18T17:55:00Z"},  # no CME meeting loaded for March
]
CME = {
    "2025-12-10": [{"min": 350, "max": 375, "prob": 0.4}, {"min": 375, "max": 400, "prob": 0.6}],
    "2026-01-28": [{"min": 325, "max": 350, "prob": 0.3}, {"min": 350, "max": 375, "prob": 0.7}],
}


def _alpha():
    alpha = AlphaModel()
    alpha.cache, alpha.last_update = dict(CME), time.time()
    return alpha


def test_markets_map_to_the_fomc_meeting_they_settle_on():
    # Tickers outside a hardcoded DEC24/NOV24 table used to get fair value 0.0.
    alpha = _alpha()
    assert alpha.parse_market(MARKETS[0]) == ("2025-12-10", 375.0)
    assert alpha.parse_market(MARKETS[1]) == ("2026-01-28", 350.0)
    assert alpha.parse_market(MARKETS[2]) == (None, None)


def test_fair_value_uses_the_mapped_meeting():
    alpha = _alpha()
    assert abs(alpha.calculate_fair_value(MARKETS[0]) - 0.6) < 1e-9
    assert abs(alpha.calculate_fair_value(MARKETS[1]) - 0.7) < 1e-9
    assert alpha.calculate_fair_value(MARKETS[2]) is None  # unknown is not 0.0


def test_bad_strike_is_skipped_not_crashed():
    alpha = _alpha()
    assert alpha.parse_market({**MARKETS[0], "floor_strike": None}) == (None, None)


def test_cycle_discovers_markets_by_series(monkeypatch):
    class DiscoveringClient(FakeClient):
        def get_markets(self, series_ticker):
            self.series = series_ticker
            return MARKETS[:1]

    client = DiscoveringClient()
    seen = []

    class RecordingAlpha:
        def calculate_fair_value(self, market):
            seen.append(market["ticker"])
            return None

    bot = engine.TradingBot(client=client, executor=PaperExecutor(":memory:"),
                            alpha=RecordingAlpha())
    bot.run_cycle()
    assert client.series == engine.settings.SERIES_TICKER
    assert seen == ["KXFED-25DEC-T3.75"]
