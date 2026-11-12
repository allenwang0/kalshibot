import pytest
import requests

import main as main_mod
from src.client import KalshiAPIError


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
