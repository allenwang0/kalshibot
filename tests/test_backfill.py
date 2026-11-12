"""P2 #13: public Kalshi history plus a futures archive become scoreable rows, offline."""

import datetime
import sqlite3
import sys
from pathlib import Path

import pytest
import responses

from src.config import settings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backfill  # noqa: E402

BASE = settings.BASE_URL
# Shapes from the Kalshi markets and candlesticks docs; values fabricated.
MARKETS = [
    {
        "ticker": "KXFED-26DEC-T4.25",
        "strike_type": "greater",
        "floor_strike": 4.25,
        "close_time": "2026-12-09T19:00:00Z",
        "settlement_ts": "2026-12-09T19:10:00Z",
        "result": "yes",
    },
    {
        "ticker": "KXFED-26DEC-T4.50",
        "strike_type": "greater",
        "floor_strike": 4.5,
        "close_time": "2026-12-09T19:00:00Z",
        "settlement_ts": "2026-12-09T19:10:00Z",
        "result": "no",
    },
]


def candle(ts: int, bid: str, ask: str) -> dict[str, object]:
    return {
        "end_period_ts": ts,
        "yes_bid": {"close_dollars": bid},
        "yes_ask": {"close_dollars": ask},
    }


@responses.activate
def test_backfill_writes_rows_model_and_results(tmp_path: Path) -> None:
    # Dec 2026 meeting on day 9 of 31; Nov implies 4.33, Dec averages 4.2245, so the
    # post-meeting rate is (31 * 4.2245 - 9 * 4.33) / 22 = 4.181341 and
    # P(hold at 450 bps) = 1 - 0.148659 / 0.25 = 0.405364.
    prices = tmp_path / "history.csv"
    prices.write_text(
        "date,month,price,current_upper_bps\n"
        "2026-12-01,2026-11,95.67,450\n"
        "2026-12-01,2026-12,95.7755,450\n"
    )
    responses.get(f"{BASE}/markets", json={"markets": MARKETS, "cursor": ""})
    day = int(datetime.datetime(2026, 12, 1, 12, tzinfo=datetime.UTC).timestamp())
    responses.get(
        f"{BASE}/series/KXFED/markets/KXFED-26DEC-T4.25/candlesticks",
        json={"candlesticks": [candle(day, "0.5500", "0.6000")]},
    )
    responses.get(f"{BASE}/series/KXFED/markets/KXFED-26DEC-T4.50/candlesticks", status=404)
    responses.get(
        f"{BASE}/historical/markets/KXFED-26DEC-T4.50/candlesticks",
        json={"candlesticks": [candle(day, "0.0400", "0.0600")]},
    )
    db = tmp_path / "log.db"
    assert backfill.main(["--db", str(db), "--prices", str(prices)]) == 0

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT ticker, meeting_date, model_p, best_bid, best_ask, mode FROM cycles ORDER BY ticker"
    ).fetchall()
    assert [r[:2] for r in rows] == [
        ("KXFED-26DEC-T4.25", "2026-12-09"),
        ("KXFED-26DEC-T4.50", "2026-12-09"),
    ]
    assert rows[0][2] == pytest.approx(0.405364, abs=1e-6)
    assert rows[1][2] == pytest.approx(0.0)
    assert rows[0][3:] == (55, 60, "BACKFILL")
    assert dict(conn.execute("SELECT ticker, result FROM settlements")) == {
        "KXFED-26DEC-T4.25": "yes",
        "KXFED-26DEC-T4.50": "no",
    }
    assert all(c.request.method == "GET" for c in responses.calls)

    import evaluate

    scored = evaluate.compute(evaluate.load(db))
    assert len(scored.predictions) == 2
