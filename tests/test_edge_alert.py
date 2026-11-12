"""P2 #14: a paper run flags large model-vs-market gaps so the author can trade by hand."""

import logging

import pytest

import src.engine as engine
from src.executor import PaperExecutor
from tests.fakes import FakeAlpha, FakeClient

MARKET = {
    "ticker": "KXFED-26DEC-T3.50",
    "strike_type": "greater",
    "floor_strike": 3.5,
    "close_time": "2026-12-09T19:00:00Z",
}


def run(prob: float, caplog: pytest.LogCaptureFixture) -> list[str]:
    bot = engine.TradingBot(
        client=FakeClient(), executor=PaperExecutor(":memory:", 1000.0), alpha=FakeAlpha(prob)
    )
    with caplog.at_level(logging.WARNING, logger="src.engine"):
        bot.run_cycle([MARKET])
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("EDGE")]


def test_large_gap_after_fees_logs_one_edge_line(caplog: pytest.LogCaptureFixture) -> None:
    # Book: bid 45, ask 48, mid 0.465. Model 0.70: gap 0.235 minus fee 0.0175.
    [line] = run(0.70, caplog)
    assert "KXFED-26DEC-T3.50" in line and "model 0.700" in line and "mid 0.465" in line


def test_gap_inside_threshold_after_fees_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    # Gap 0.06 minus fee 0.0175 = 0.0425, below the 0.05 default threshold.
    assert run(0.525, caplog) == []


def test_alert_works_when_the_model_is_below_the_market(caplog: pytest.LogCaptureFixture) -> None:
    [line] = run(0.20, caplog)
    assert "model 0.200" in line
