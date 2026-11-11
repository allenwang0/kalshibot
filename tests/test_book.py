import pytest

from src.book import parse_orderbook
from tests.fakes import BOOK

# Kalshi's orderbook lists resting bids only, for YES and for NO. A NO bid at
# X cents is a YES ask at 100 - X. Reading yes[0] as the ask makes the bot size
# against, and post at, a stale low bid instead of the price it can buy at.


def test_best_bid_and_ask_from_both_sides():
    book = parse_orderbook(BOOK)
    assert book.best_yes_bid == 45
    assert book.best_yes_ask == 100 - 52
    assert book.bid_size == 5
    assert book.ask_size == 3
    assert book.spread == 3


def test_empty_no_side_has_no_ask():
    assert parse_orderbook({"orderbook": {"yes": [[40, 10]], "no": []}}) is None


def test_null_yes_side_has_no_bid():
    assert parse_orderbook({"orderbook": {"yes": None, "no": [[52, 3]]}}) is None


def test_one_level_per_side():
    book = parse_orderbook({"orderbook": {"yes": [[30, 7]], "no": [[60, 2]]}})
    assert (book.best_yes_bid, book.best_yes_ask, book.bid_size, book.ask_size) == (30, 40, 7, 2)


def test_fixed_point_dollar_shape():
    # Current docs shape: orderbook_fp with dollar-string prices and fp counts.
    resp = {
        "orderbook_fp": {
            "yes_dollars": [["0.4000", "10.00"], ["0.4500", "5.00"]],
            "no_dollars": [["0.5000", "8.00"], ["0.5200", "3.00"]],
        }
    }
    book = parse_orderbook(resp)
    assert (book.best_yes_bid, book.best_yes_ask, book.bid_size, book.ask_size) == (45, 48, 5, 3)


@pytest.mark.parametrize("resp", [None, {}, {"orderbook": {}}])
def test_missing_book(resp):
    assert parse_orderbook(resp) is None
