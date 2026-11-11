from typing import Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    API_KEY_ID: Optional[str] = Field(None, validation_alias="KALSHI_API_KEY_ID")
    PRIVATE_KEY_PATH: Optional[str] = Field(None, validation_alias="KALSHI_PRIVATE_KEY_PATH")
    BASE_URL: str = Field("https://api.kalshi.com/trade-api/v2", validation_alias="KALSHI_API_URL")
    # PAPER never sends orders to Kalshi. LIVE must be set explicitly.
    TRADING_MODE: Literal["PAPER", "LIVE"] = "PAPER"

    # Risk Settings
    KELLY_FRACTION: float = 0.20
    MAX_POSITION_SIZE: float = 100.0  # max dollars at risk per market
    MAX_TOTAL_EXPOSURE: float = 300.0  # max dollars at risk across all markets

    @model_validator(mode="after")
    def _live_needs_keys(self):
        if self.TRADING_MODE == "LIVE" and not (self.API_KEY_ID and self.PRIVATE_KEY_PATH):
            raise ValueError(
                "TRADING_MODE=LIVE requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH"
            )
        return self


settings = Settings()
