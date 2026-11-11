from dataclasses import dataclass
from typing import Any

# Kalshi's orderbook returns resting bids only, for YES and for NO; "no asks are
# returned". A NO bid at X cents is a YES ask at 100 - X.
# https://docs.kalshi.com/api-reference/market/get-market-orderbook
# The best level is taken with max() so the result does not depend on sort order.


@dataclass(frozen=True)
class Book:
    best_yes_bid: int  # cents
    best_yes_ask: int  # cents
    bid_size: float
    ask_size: float

    @property
    def spread(self) -> int:
        return self.best_yes_ask - self.best_yes_bid


Levels = list[tuple[int, float]]


def _cents(levels: list[list[Any]] | None, dollars: bool) -> Levels:
    if dollars:
        return [(round(float(p) * 100), float(n)) for p, n in (levels or [])]
    return [(int(p), float(n)) for p, n in (levels or [])]


def _levels(resp: dict[str, Any]) -> tuple[Levels, Levels]:
    """(yes_levels, no_levels) as [(price_cents, size)], from either response shape.

    The current API returns orderbook_fp with dollar strings; "orderbook" with
    integer cents is the older shape.
    """
    if resp.get("orderbook_fp"):
        ob = resp["orderbook_fp"]
        return _cents(ob.get("yes_dollars"), True), _cents(ob.get("no_dollars"), True)
    ob = resp.get("orderbook") or {}
    return _cents(ob.get("yes"), False), _cents(ob.get("no"), False)


def parse_orderbook(resp: dict[str, Any] | None) -> Book | None:
    """Best YES bid and ask. None when either side is empty or missing."""
    if not resp:
        return None
    yes, no = _levels(resp)
    if not yes or not no:
        return None
    bid_price, bid_size = max(yes)
    no_price, ask_size = max(no)
    return Book(bid_price, 100 - no_price, bid_size, ask_size)
