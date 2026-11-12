"""Fill the SQLite log from settled Kalshi markets so evaluate.py has data on day one.

    python scripts/backfill.py --prices data/fed_funds_futures_history.csv --meetings 8

Reads only public endpoints (no keys). For each settled market of the last N
meetings it stores daily YES bid/ask closes from candlesticks, the model
probability computed from the futures prices known on that day, and the
settled result. Without --prices it records market prices and results only.

You supply the prices file: CSV columns date (YYYY-MM-DD), month (YYYY-MM),
price, and current_upper_bps (upper bound of the target range that day, bps).
"""

import argparse
import csv
import datetime
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.client import KalshiAPIError, KalshiClient  # noqa: E402
from src.ledger import Ledger  # noqa: E402
from src.models.fedfunds import Month, meeting_probabilities, prob_upper_above  # noqa: E402
from src.strategy import meeting_date  # noqa: E402

History = dict[datetime.date, tuple[dict[Month, float], int]]
DAY = 1440  # candlestick period_interval in minutes


def load_history(path: Path) -> History:
    prices: defaultdict[datetime.date, dict[Month, float]] = defaultdict(dict)
    upper: dict[datetime.date, int] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            day = datetime.date.fromisoformat(row["date"])
            year, month = row["month"].strip().split("-")
            prices[day][(int(year), int(month))] = float(row["price"])
            upper[day] = int(row["current_upper_bps"])
    return {day: (p, upper[day]) for day, p in prices.items()}


def candles(
    client: KalshiClient, series: str, ticker: str, start: int, end: int
) -> list[dict[str, Any]]:
    """https://docs.kalshi.com/api-reference/market/get-market-candlesticks
    Markets settled before Kalshi's historical cutoff are served from /historical.
    """
    params = {"start_ts": start, "end_ts": end, "period_interval": DAY}
    try:
        data = client.request("GET", f"/series/{series}/markets/{ticker}/candlesticks", params)
    except KalshiAPIError as e:
        if e.status != 404:
            raise
        data = client.request("GET", f"/historical/markets/{ticker}/candlesticks", params)
    return list(data.get("candlesticks") or [])


def model_p(
    history: History, day: datetime.date, market: dict[str, Any], meetings: list[datetime.date]
) -> float | None:
    """Model price using only the futures prices known on `day`."""
    meeting = meeting_date(market)
    if day not in history or meeting is None:
        return None
    prices, upper = history[day]
    try:
        dist = meeting_probabilities(prices, [m for m in meetings if m > day], upper).get(meeting)
    except (KeyError, ValueError):
        return None
    return None if dist is None else prob_upper_above(dist, float(market["floor_strike"]))


def cents(dollars: str | None) -> int | None:
    return None if dollars is None else round(float(dollars) * 100)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--series", default="KXFED")
    parser.add_argument("--db", default="kalshibot.db")
    parser.add_argument("--prices", type=Path, help="archived futures prices CSV")
    parser.add_argument("--meetings", type=int, default=8, help="how many past meetings")
    parser.add_argument("--days", type=int, default=30, help="days of history per meeting")
    args = parser.parse_args(argv)

    client = KalshiClient()
    ledger = Ledger(args.db)
    history = load_history(args.prices) if args.prices else {}

    settled = [
        m
        for m in client.get_markets(args.series, status="settled")
        if m.get("result") in ("yes", "no") and meeting_date(m) is not None
    ]
    meetings = sorted({d for m in settled if (d := meeting_date(m)) is not None})
    keep = set(meetings[-args.meetings :])

    rows = 0
    for market in settled:
        meeting = meeting_date(market)
        if meeting not in keep:
            continue
        end = datetime.datetime.combine(meeting, datetime.time(), datetime.UTC)
        start = end - datetime.timedelta(days=args.days)
        for candle in candles(
            client, args.series, market["ticker"], int(start.timestamp()), int(end.timestamp())
        ):
            when = datetime.datetime.fromtimestamp(candle["end_period_ts"], datetime.UTC)
            bid = cents((candle.get("yes_bid") or {}).get("close_dollars"))
            ask = cents((candle.get("yes_ask") or {}).get("close_dollars"))
            ledger.log_cycle(
                ts=when.isoformat(timespec="seconds"),
                mode="BACKFILL",
                ticker=market["ticker"],
                strike=market.get("floor_strike"),
                meeting_date=meeting.isoformat(),
                model_p=model_p(history, when.date(), market, meetings),
                best_bid=bid,
                best_ask=ask,
                mid=None if bid is None or ask is None else (bid + ask) / 200.0,
                action="backfill",
            )
            rows += 1
        settled_ts = market.get("settlement_ts") or market.get("close_time") or ""
        ledger.set_settlement(market["ticker"], market["result"], settled_ts)
    print(f"backfilled {rows} daily rows for {len(keep)} meetings into {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
