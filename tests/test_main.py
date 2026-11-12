from pathlib import Path
from typing import Any

import pytest
import requests
import responses

import main as main_mod
from src.client import KalshiAPIError
from src.config import settings
from src.engine import TradingBot
from src.replay import ReplaySession


def test_single_run_that_fails_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scheduled --once run must not report success when the cycle was skipped."""

    def failing_cycle(self: object) -> None:
        raise KalshiAPIError(503, "unavailable")

    monkeypatch.setattr("src.engine.TradingBot.run_cycle", failing_cycle)
    monkeypatch.setattr("src.engine.TradingBot.__init__", lambda self: None)
    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--once"])
    assert exc.value.code == 1


def test_loop_skips_a_failed_cycle_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def flaky_cycle(self: object) -> None:
        calls.append(1)
        raise requests.ConnectionError("down")

    def sleep(seconds: float) -> None:
        if len(calls) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("src.engine.TradingBot.run_cycle", flaky_cycle)
    monkeypatch.setattr("src.engine.TradingBot.__init__", lambda self: None)
    monkeypatch.setattr(main_mod.time, "sleep", sleep)
    main_mod.main([])
    assert len(calls) == 2


ROOT = Path(__file__).parents[1]


def test_replay_quickstart_runs_one_paper_cycle_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The README quickstart: no keys, no network, recorded market data, paper fills."""
    monkeypatch.setattr(
        settings, "FUTURES_PRICES_PATH", str(ROOT / "data/fed_funds_futures.example.csv")
    )
    monkeypatch.setattr(settings, "CURRENT_TARGET_UPPER_BPS", 400)
    assert settings.TRADING_MODE == "PAPER" and settings.API_KEY_ID is None
    bots: list[TradingBot] = []

    class Recording(TradingBot):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            bots.append(self)

    monkeypatch.setattr(main_mod, "TradingBot", Recording)
    with responses.RequestsMock() as rsps:
        main_mod.main(["--once", "--replay", str(ROOT / "tests/fixtures/replay.json")])
        assert len(rsps.calls) == 0

    [bot] = bots
    rows = bot.ledger.db.execute("SELECT COUNT(*) FROM cycles").fetchone()[0]
    fills = bot.ledger.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert rows == 5
    assert fills > 0


def test_replay_session_refuses_anything_but_recorded_gets() -> None:
    session = ReplaySession(ROOT / "tests/fixtures/replay.json")
    with pytest.raises(RuntimeError, match="no recorded response"):
        session.request("POST", "https://x/trade-api/v2/portfolio/events/orders")


def test_replay_is_refused_in_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TRADING_MODE", "LIVE")
    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--once", "--replay", str(ROOT / "tests/fixtures/replay.json")])
    assert exc.value.code == 2
