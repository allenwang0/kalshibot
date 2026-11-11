import math

from src.config import settings

# Kalshi general trading fee for a taker fill of C contracts at P dollars:
#   fee = round up to the cent of FEE_RATE * C * P * (1 - P), FEE_RATE = 0.07.
# https://kalshi.com/docs/kalshi-fee-schedule.pdf ("General Trading Fees Table").
# KXFED has fee_type quadratic_with_maker_fees and fee_multiplier 1
# (GET /series/KXFED). Orders here cross the spread, so the taker rate applies.


def taker_fee(count: int, price: float) -> float:
    """Fee in dollars for `count` contracts at `price` dollars."""
    raw_cents = settings.FEE_RATE * count * price * (1.0 - price) * 100
    return math.ceil(round(raw_cents, 6)) / 100.0


def fee_adjusted_price(price: float) -> float:
    """Cost per contract including the unrounded per-contract fee."""
    return price + settings.FEE_RATE * price * (1.0 - price)


class RiskManager:
    @staticmethod
    def calculate_kelly_size(bankroll: float, win_prob: float, market_price: float) -> float:
        """Dollars to hold in a YES contract bought at market_price (dollars)."""
        if not 0.0 < market_price < 1.0 or not 0.0 <= win_prob <= 1.0:
            return 0.0
        # Shrink the model toward the market: the model is an estimate, not truth.
        p = market_price + settings.SHRINKAGE * (win_prob - market_price)
        q = fee_adjusted_price(market_price)
        # Binary contract bought at q pays 1: win 1 - q per dollar risked q.
        # Kelly fraction f = p - (1 - p) * q / (1 - q) = (p - q) / (1 - q).
        if p <= q:
            return 0.0
        raw_kelly = (p - q) / (1.0 - q)
        allocation = bankroll * raw_kelly * settings.KELLY_FRACTION
        return max(0.0, min(allocation, settings.MAX_POSITION_SIZE))
