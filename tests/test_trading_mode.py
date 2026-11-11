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
