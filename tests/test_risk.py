import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.config import settings
from src.risk import RiskManager, fee_adjusted_price, taker_fee

kelly = RiskManager.calculate_kelly_size
prices = st.integers(min_value=1, max_value=99).map(lambda c: c / 100)
probs = st.floats(min_value=0.0, max_value=1.0)


def test_fee_matches_the_schedule_rounding():
    # 0.07 * 1 * 0.5 * 0.5 = 1.75 cents, charged as 2 cents; 100 at 0.5 = $1.75.
    assert taker_fee(1, 0.50) == 0.02
    assert taker_fee(100, 0.50) == 1.75
    assert taker_fee(10, 0.10) == 0.07  # 6.3 cents rounds up


def test_one_cent_edge_is_eaten_by_the_fee():
    # Without fees a 51% model against a 50 cent ask looked like a bet.
    assert kelly(10_000, 0.51, 0.50) == 0.0


def test_no_edge_and_negative_edge_size_to_zero():
    assert kelly(10_000, 0.50, 0.50) == 0.0
    assert kelly(10_000, 0.30, 0.50) == 0.0


@pytest.mark.parametrize("p,q", [(1.0, 0.0), (1.0, 1.0), (0.9, 1.0), (float("nan"), 0.5),
                                 (1.2, 0.5), (0.0, 0.01)])
def test_degenerate_inputs_size_to_zero_or_stay_capped(p, q):
    size = kelly(10_000, p, q)
    assert 0.0 <= size <= settings.MAX_POSITION_SIZE
    if q in (0.0, 1.0) or p != p or p > 1:
        assert size == 0.0


def test_zero_shrinkage_never_trades(monkeypatch):
    monkeypatch.setattr(settings, "SHRINKAGE", 0.0)
    assert kelly(10_000, 0.99, 0.10) == 0.0


@given(bankroll=st.floats(min_value=0, max_value=1e7), p=probs, q=prices)
def test_size_is_bounded(bankroll, p, q):
    assert 0.0 <= kelly(bankroll, p, q) <= settings.MAX_POSITION_SIZE


@given(p1=probs, p2=probs, q=prices)
def test_size_is_monotonic_in_model_probability(p1, p2, q):
    lo, hi = sorted((p1, p2))
    assert kelly(1_000, lo, q) <= kelly(1_000, hi, q)


@given(q=prices)
def test_fee_adjusted_price_stays_inside_the_contract_range(q):
    assert q <= fee_adjusted_price(q) < 1.0
