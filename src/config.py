from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    API_KEY_ID: str | None = Field(None, validation_alias="KALSHI_API_KEY_ID")
    PRIVATE_KEY_PATH: str | None = Field(None, validation_alias="KALSHI_PRIVATE_KEY_PATH")
    # Production. Demo is https://external-api.demo.kalshi.co/trade-api/v2 and
    # needs demo keys. https://docs.kalshi.com/getting_started/api_environments
    BASE_URL: str = Field(
        "https://external-api.kalshi.com/trade-api/v2", validation_alias="KALSHI_API_URL"
    )
    # PAPER never sends orders to Kalshi. LIVE must be set explicitly.
    TRADING_MODE: Literal["PAPER", "LIVE"] = "PAPER"
    SERIES_TICKER: str = "KXFED"  # Kalshi series whose open markets the bot trades
    # CSV of 30-Day Fed Funds futures settlement prices (columns month, price),
    # supplied by the user. See data/fed_funds_futures.example.csv.
    FUTURES_PRICES_PATH: str = "data/fed_funds_futures.csv"
    # Upper bound of the current FOMC target range in bps, e.g. 400 for 3.75-4.00%.
    CURRENT_TARGET_UPPER_BPS: int | None = None
    PAPER_BANKROLL: float = 1000.0
    # SQLite log of cycles, paper fills and settlements (scripts/evaluate.py reads it).
    DB_PATH: str = "kalshibot.db"

    # Risk Settings
    KELLY_FRACTION: float = 0.20
    # Weight on the model's edge over the market price, in [0, 1]; 0 never trades.
    SHRINKAGE: float = Field(0.5, ge=0.0, le=1.0)
    FEE_RATE: float = 0.07  # Kalshi taker fee coefficient, see src/risk.py
    MAX_POSITION_SIZE: float = 100.0  # max dollars at risk per market
    MAX_SPREAD_CENTS: int = 10  # skip markets with a wider YES bid/ask spread
    MAX_TOTAL_EXPOSURE: float = 300.0  # max dollars at risk across all markets
    # Below $1 the one-cent fee rounding is a large share of the order.
    MIN_ORDER_DOLLARS: float = 1.0
    MIN_BANKROLL_DOLLARS: float = 1.0

    # Loop and request pacing
    # Fed-rate books move on data releases, not by the second; one cycle a
    # minute keeps request use far under the rate limit.
    LOOP_SECONDS: float = 60.0
    # Basic tier read budget is 200 tokens/s and most requests cost 10 tokens.
    # https://docs.kalshi.com/getting_started/rate_limits
    RATE_LIMIT_PER_SEC: float = 20.0
    MAX_WORKERS: int = 8  # concurrent orderbook fetches

    @model_validator(mode="after")
    def _live_needs_keys(self) -> "Settings":
        if self.TRADING_MODE == "LIVE" and not (self.API_KEY_ID and self.PRIVATE_KEY_PATH):
            raise ValueError(
                "TRADING_MODE=LIVE requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH"
            )
        return self


settings = Settings()
