import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    API_KEY_ID: str = os.getenv("KALSHI_API_KEY_ID")
    PRIVATE_KEY_PATH: str = os.getenv("KALSHI_PRIVATE_KEY_PATH")
    BASE_URL: str = os.getenv("KALSHI_API_URL", "https://api.kalshi.com/trade-api/v2")
    TRADING_MODE: str = os.getenv("TRADING_MODE", "PAPER")

    # Risk Settings
    KELLY_FRACTION: float = float(os.getenv("KELLY_FRACTION", 0.20))
    MAX_POSITION_SIZE: float = float(os.getenv("MAX_POSITION_SIZE", 100.0))


settings = Settings()