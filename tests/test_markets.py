import datetime

import pytest

import src.engine as engine
from src.executor import PaperExecutor
from src.strategy import AlphaModel, meeting_date
from tests.fakes import FakeClient

# Fabricated markets in the shape of GET /markets (ticker, strike_type,
# floor_strike, close_time). https://docs.kalshi.com/api-reference/market/get-markets
MARKETS = [
    {
        "ticker": "KXFED-26DEC-T3.50",
        "strike_type": "greater",
        "floor_strike": 3.5,
        "close_time": "2026-12-09T18:55:00Z",
    },
    {
        "ticker": "KXFED-27JAN-T3.25",
        "strike_type": "greater",
        "floor_strike": 3.25,
        "close_time": "2027-01-27T18:55:00Z",
    },
    {
        "ticker": "KXFED-27MAR-T3.25",
        "strike_type": "greater",
        "floor_strike": 3.25,
        "close_time": "2027-03-17T17:55:00Z",
    },  # no futures prices that far out
]
# Same fabricated prices as tests/test_fedfunds.py: Dec -10 bp, Jan -15 bp.
DEC_PRICE = 100 - (9 * 3.64 + 22 * 3.54) / 31
PRICES = f"month,price\n2026-11,96.36\n2026-12,{DEC_PRICE}\n2027-01,96.55\n2027-02,96.61\n"


@pytest.fixture
def alpha(tmp_path):
    path = tmp_path / "ff.csv"
    path.write_text(PRICES)
    model = AlphaModel(prices_path=str(path), current_upper_bps=375)
    model.refresh(MARKETS)
    return model


def test_markets_map_to_the_fomc_meeting_they_settle_on():
    # Tickers outside a hardcoded DEC24/NOV24 table used to get fair value 0.0.
    assert meeting_date(MARKETS[0]) == datetime.date(2026, 12, 9)
    assert meeting_date(MARKETS[1]) == datetime.date(2027, 1, 27)
    assert meeting_date({**MARKETS[0], "strike_type": "between"}) is None


def test_close_time_is_read_in_eastern_time_across_month_and_year_boundaries():
    # 01:00Z on Feb 1 is still Jan 31 in New York; reading the UTC date put a
    # Jan 31 meeting in February.
    assert meeting_date({**MARKETS[0], "close_time": "2024-02-01T01:00:00Z"}) == datetime.date(
        2024, 1, 31
    )
    assert meeting_date({**MARKETS[0], "close_time": "2026-01-01T03:00:00Z"}) == datetime.date(
        2025, 12, 31
    )


def test_fair_value_is_p_upper_bound_above_strike(alpha):
    assert alpha.calculate_fair_value(MARKETS[0]) == pytest.approx(0.6)  # hold at 3.75
    assert alpha.calculate_fair_value(MARKETS[1]) == pytest.approx(0.76)  # 3.50 or 3.75
    assert alpha.calculate_fair_value(MARKETS[2]) is None  # unknown is not 0.0


def test_bad_strike_is_skipped_not_crashed(alpha):
    assert alpha.calculate_fair_value({**MARKETS[0], "floor_strike": None}) is None


def test_missing_inputs_leave_the_model_empty_not_zero(tmp_path):
    no_file = AlphaModel(prices_path=str(tmp_path / "missing.csv"), current_upper_bps=375)
    no_file.refresh(MARKETS)
    no_rate = AlphaModel(prices_path=str(tmp_path / "missing.csv"), current_upper_bps=None)
    no_rate.current_upper_bps = None
    no_rate.refresh(MARKETS)
    for model in (no_file, no_rate):
        assert all(model.calculate_fair_value(m) is None for m in MARKETS)


def test_cycle_discovers_markets_by_series():
    class DiscoveringClient(FakeClient):
        def get_markets(self, series_ticker):
            self.series = series_ticker
            return MARKETS[:1]

    client = DiscoveringClient()
    seen = []

    class RecordingAlpha:
        def refresh(self, markets):
            seen.append([m["ticker"] for m in markets])

        def calculate_fair_value(self, market):
            seen.append(market["ticker"])
            return None

    bot = engine.TradingBot(
        client=client, executor=PaperExecutor(":memory:"), alpha=RecordingAlpha()
    )
    bot.run_cycle()
    assert client.series == engine.settings.SERIES_TICKER
    assert seen == [["KXFED-26DEC-T3.50"], "KXFED-26DEC-T3.50"]
