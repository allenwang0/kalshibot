"""Futures-implied meeting probabilities.

The worked example is computed by hand from the method in src/models/fedfunds.py.
It is not CME's published example: the CME methodology page could not be
retrieved when this was written, so the plan's "reproduce CME's example within
0.5 pp" check is still open.
"""
import datetime
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.models.fedfunds import (
    load_prices,
    meeting_probabilities,
    meeting_rates,
    prob_upper_above,
)

DEC = datetime.date(2026, 12, 9)  # day 9 of 31
JAN = datetime.date(2027, 1, 27)
# Nov and Feb have no meeting, so they pin the start of Dec and the end of Jan.
# Dec average = (9 * 3.64 + 22 * 3.54) / 31, so the Dec meeting implies -10 bp.
PRICES = {
    (2026, 11): 100 - 3.64,
    (2026, 12): 100 - (9 * 3.64 + 22 * 3.54) / 31,
    (2027, 1): 100 - 3.45,
    (2027, 2): 100 - 3.39,
}


def test_worked_example_two_meetings():
    rates = meeting_rates(PRICES, [DEC, JAN])
    assert [round(s, 9) for _, s, _ in rates] == [3.64, 3.54]
    assert [round(e, 9) for _, _, e in rates] == [3.54, 3.39]
    probs = meeting_probabilities(PRICES, [DEC, JAN], current_upper_bps=375)
    # -10 bp = 40% of a 25 bp cut.
    assert probs[DEC] == pytest.approx({350: 0.4, 375: 0.6})
    # -15 bp more = 60% of a cut on each branch: .4*.6, .4*.4 + .6*.6, .6*.4
    assert probs[JAN] == pytest.approx({325: 0.24, 350: 0.52, 375: 0.24})
    # KXFED YES is "upper bound above strike".
    assert prob_upper_above(probs[JAN], 3.50) == pytest.approx(0.24)
    assert prob_upper_above(probs[JAN], 3.25) == pytest.approx(0.76)


def test_meeting_without_prices_stops_the_chain_instead_of_guessing():
    probs = meeting_probabilities({(2026, 11): 96.36}, [DEC, JAN], 375)
    assert probs == {}


def test_last_day_meeting_uses_next_month_for_the_end_rate():
    meeting = datetime.date(2024, 1, 31)
    prices = {(2023, 12): 100 - 5.33, (2024, 1): 100 - 5.33, (2024, 2): 100 - 5.08}
    probs = meeting_probabilities(prices, [meeting], 550)
    assert probs[meeting] == pytest.approx({525: 1.0})


def test_load_prices_reads_month_and_price(tmp_path):
    path = tmp_path / "ff.csv"
    path.write_text("month,price\n2026-11,96.36\n2026-12, 96.43\n")
    assert load_prices(path) == {(2026, 11): 96.36, (2026, 12): 96.43}


months = st.lists(st.integers(min_value=0, max_value=23), min_size=1, max_size=8, unique=True)


@given(meeting_month_offsets=months,
       rates=st.lists(st.floats(min_value=0.0, max_value=8.0), min_size=26, max_size=26),
       days=st.lists(st.integers(min_value=1, max_value=28), min_size=26, max_size=26),
       upper=st.sampled_from([25, 100, 375, 525]))
def test_each_meeting_is_a_probability_distribution(meeting_month_offsets, rates, days, upper):
    meetings = []
    for off in sorted(meeting_month_offsets):
        y, m = 2026 + (off + 1) // 12, (off + 1) % 12 + 1
        meetings.append(datetime.date(y, m, days[off]))
    prices = {}
    for off in range(26):
        prices[(2026 + off // 12, off % 12 + 1)] = 100 - rates[off]
    for dist in meeting_probabilities(prices, meetings, upper).values():
        assert math.isclose(sum(dist.values()), 1.0, abs_tol=1e-9)
        assert all(p >= 0 for p in dist.values())
        assert all(u >= 25 and u % 25 == 0 for u in dist)
