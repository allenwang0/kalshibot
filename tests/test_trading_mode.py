import src.engine as engine
from src.config import settings
from tests.fakes import FakeAlpha, FakeClient


def test_default_mode_sends_no_live_orders_and_records_paper_fill(monkeypatch, tmp_path):
    # The safety contract: with TRADING_MODE unset, a cycle with positive edge
    # must never reach KalshiClient.place_order. It is recorded as a paper fill.
    client = FakeClient()
    monkeypatch.setattr(engine, "KalshiClient", lambda: client)
    monkeypatch.setattr(engine, "AlphaModel", FakeAlpha)
    monkeypatch.chdir(tmp_path)  # the paper ledger is created here
    assert settings.TRADING_MODE == "PAPER"

    bot = engine.TradingBot()
    bot.run_cycle(["KXFED-TEST-T4.00"])

    assert client.live_orders == []
    assert len(bot.executor.fills()) == 1


def test_live_mode_routes_orders_to_kalshi(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(settings, "TRADING_MODE", "LIVE")
    bot = engine.TradingBot(client=client, alpha=FakeAlpha())
    bot.run_cycle(["KXFED-TEST-T4.00"])
    assert len(client.live_orders) == 1


def test_order_is_posted_at_the_yes_ask_not_the_lowest_bid():
    client = FakeClient()
    bot = engine.TradingBot(client=client, executor=_LiveFor(client), alpha=FakeAlpha())
    bot.run_cycle(["KXFED-TEST-T4.00"])
    (_, side, count, price), = client.live_orders
    assert (side, price) == ("yes", 48)
    assert count <= 3  # never more than the size resting at the ask


def test_wide_spread_is_skipped(monkeypatch):
    client = FakeClient(book={"orderbook": {"yes": [[10, 5]], "no": [[50, 5]]}})  # 10 / 50
    monkeypatch.setattr(settings, "MAX_SPREAD_CENTS", 10)
    bot = engine.TradingBot(client=client, executor=_LiveFor(client), alpha=FakeAlpha())
    bot.run_cycle(["KXFED-TEST-T4.00"])
    assert client.live_orders == []


def _LiveFor(client):
    from src.executor import LiveExecutor
    return LiveExecutor(client)
