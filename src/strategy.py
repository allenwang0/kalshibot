import datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from src.config import settings
from src.models.fedfunds import Distribution, load_prices, meeting_probabilities, prob_upper_above

log = logging.getLogger(__name__)
EASTERN = ZoneInfo("America/New_York")


def meeting_date(market: dict[str, Any]) -> datetime.date | None:
    """The FOMC decision date a KXFED market settles on, or None.

    KXFED markets ask whether the upper bound of the target range is above
    floor_strike after a meeting (strike_type "greater"), and close on the
    decision day. close_time is UTC; FOMC dates are Eastern.
    https://docs.kalshi.com/api-reference/market/get-markets
    """
    if market.get("strike_type") != "greater":
        return None
    try:
        close = datetime.datetime.fromisoformat(market["close_time"])
    except (KeyError, TypeError, ValueError):
        return None
    return close.astimezone(EASTERN).date()


class AlphaModel:
    """Fair value per market from the futures-implied meeting distributions."""

    def __init__(self, prices_path: str | None = None, current_upper_bps: int | None = None):
        self.prices_path = prices_path or settings.FUTURES_PRICES_PATH
        self.current_upper_bps = (
            settings.CURRENT_TARGET_UPPER_BPS if current_upper_bps is None else current_upper_bps
        )
        self.distributions: dict[datetime.date, Distribution] = {}

    def refresh(self, markets: list[dict[str, Any]]) -> None:
        """Recompute distributions for the meetings these markets settle on.

        On missing inputs the model is left empty, so no market gets a price
        and nothing trades; it never falls back to a stale or zero price.
        """
        self.distributions = {}
        if self.current_upper_bps is None:
            log.error("CURRENT_TARGET_UPPER_BPS is not set; no model prices")
            return
        meetings = sorted({d for m in markets if (d := meeting_date(m)) is not None})
        try:
            prices = load_prices(self.prices_path)
            self.distributions = meeting_probabilities(prices, meetings, self.current_upper_bps)
        except (OSError, ValueError, KeyError) as e:
            log.error("cannot read futures prices from %s: %s", self.prices_path, e)
            return
        missing = [m for m in meetings if m not in self.distributions]
        if missing:
            log.warning("no futures prices to price meetings from %s on", missing[0])

    def calculate_fair_value(self, market: dict[str, Any]) -> float | None:
        """Model probability that the market settles YES, or None if unknown."""
        meeting = meeting_date(market)
        dist = self.distributions.get(meeting) if meeting else None
        if dist is None:
            return None
        try:
            strike = float(market["floor_strike"])
        except (KeyError, TypeError, ValueError):
            return None
        return prob_upper_above(dist, strike)
