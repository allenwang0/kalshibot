from dataclasses import dataclass
from typing import Optional

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


def _levels(resp) -> tuple:
    """Return (yes_levels, no_levels) as [(price_cents, size)], from either response shape."""
    if resp.get("orderbook_fp"):
        ob = resp["orderbook_fp"]
        conv = lambda lv: [(round(float(p) * 100), float(n)) for p, n in (lv or [])]
        return conv(ob.get("yes_dollars")), conv(ob.get("no_dollars"))
    ob = resp.get("orderbook") or {}
    conv = lambda lv: [(int(p), float(n)) for p, n in (lv or [])]
    return conv(ob.get("yes")), conv(ob.get("no"))


def parse_orderbook(resp) -> Optional[Book]:
    """Best YES bid and ask. None when either side is empty or missing."""
    if not resp:
        return None
    yes, no = _levels(resp)
    if not yes or not no:
        return None
    bid_price, bid_size = max(yes)
    no_price, ask_size = max(no)
    return Book(bid_price, 100 - no_price, bid_size, ask_size)
