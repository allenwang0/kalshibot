"""Meeting-by-meeting target-range probabilities from 30-Day Fed Funds futures.

Method (the one CME FedWatch describes,
https://www.cmegroup.com/articles/2023/understanding-the-cme-group-fedwatch-tool-methodology.html):

1. A contract settles on the average daily effective rate over its month, so
   the implied average rate is 100 - price.
2. In a month with a meeting on day d of n days, the old rate holds through the
   meeting day and the new target takes effect the next day:
       avg = (d * start + (n - d) * end) / n
   A month with no meeting pins its own rate, so it is used as the end rate of
   the meeting month before it and the start rate of the meeting month after it.
   Otherwise the start is the previous meeting's end and the end is solved from
   the equation above.
3. The expected move at a meeting, (end - start) / 25 bp, is split between the
   two whole 25 bp moves around it. Moves chain from the current target range,
   so each meeting's distribution is the previous one shifted by that split.

Rates are in percent, target ranges are keyed by their upper bound in basis
points. Futures-implied rates include a term premium, so these are
risk-neutral, not real-world, probabilities.
"""

import calendar
import csv
import datetime
import math
from pathlib import Path

STEP_BPS = 25
MIN_UPPER_BPS = 25  # the 0 to 0.25% range is the floor

Month = tuple[int, int]
Distribution = dict[int, float]


def load_prices(path: str | Path) -> dict[Month, float]:
    """Read a CSV with columns month (YYYY-MM) and price; lines starting with # are notes."""
    prices: dict[Month, float] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(line for line in f if not line.startswith("#")):
            year, month = row["month"].strip().split("-")
            prices[(int(year), int(month))] = float(row["price"])
    return prices


def _shift(month: Month, by: int) -> Month:
    index = month[0] * 12 + (month[1] - 1) + by
    return index // 12, index % 12 + 1


def _step(dist: Distribution, move_bps: float) -> Distribution:
    steps = move_bps / STEP_BPS
    low = math.floor(steps)
    weight_high = steps - low
    out: Distribution = {}
    for upper, p in dist.items():
        for k, w in ((low, 1.0 - weight_high), (low + 1, weight_high)):
            if w > 0:
                target = max(MIN_UPPER_BPS, upper + k * STEP_BPS)
                out[target] = out.get(target, 0.0) + p * w
    return out


def meeting_rates(
    prices: dict[Month, float], meetings: list[datetime.date]
) -> list[tuple[datetime.date, float, float]]:
    """(meeting, start rate, end rate) for each meeting the prices can resolve.

    Stops at the first meeting that lacks the prices it needs, because every
    later meeting chains from it.
    """
    meeting_months = {(m.year, m.month) for m in meetings}
    if len(meeting_months) != len(meetings):
        raise ValueError("at most one meeting per month is supported")

    def anchor(month: Month) -> float | None:
        if month in meeting_months or month not in prices:
            return None
        return 100.0 - prices[month]

    out: list[tuple[datetime.date, float, float]] = []
    prev_end: float | None = None
    for meeting in sorted(meetings):
        month = (meeting.year, meeting.month)
        n = calendar.monthrange(*month)[1]
        d = meeting.day
        avg = 100.0 - prices[month] if month in prices else None
        start = anchor(_shift(month, -1))
        if start is None:
            start = prev_end
        end = anchor(_shift(month, 1))
        if start is not None and end is None and avg is not None and d < n:
            end = (n * avg - d * start) / (n - d)
        elif start is None and end is not None and avg is not None:
            start = (n * avg - (n - d) * end) / d
        if start is None or end is None:
            break
        out.append((meeting, start, end))
        prev_end = end
    return out


def meeting_probabilities(
    prices: dict[Month, float], meetings: list[datetime.date], current_upper_bps: int
) -> dict[datetime.date, Distribution]:
    """Probability of each target range (by upper bound, bps) after each meeting."""
    dist: Distribution = {current_upper_bps: 1.0}
    out: dict[datetime.date, Distribution] = {}
    for meeting, start, end in meeting_rates(prices, meetings):
        dist = _step(dist, (end - start) * 100.0)
        out[meeting] = dist
    return out


def prob_upper_above(dist: Distribution, strike_percent: float) -> float:
    """P(upper bound of the target range > strike), the KXFED YES condition."""
    strike_bps = round(strike_percent * 100)
    return sum(p for upper, p in dist.items() if upper > strike_bps)
