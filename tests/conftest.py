import os
import tempfile

# Tests must never read a developer's real .env or key env vars. Settings are
# built at import time, so scrub the environment and import src.config from an
# empty directory before any test module imports it.
for _name in list(os.environ):
    if _name.startswith("KALSHI_") or _name in {
        "TRADING_MODE", "KELLY_FRACTION", "MAX_POSITION_SIZE", "MAX_TOTAL_EXPOSURE",
        "MAX_SPREAD_CENTS", "SERIES_TICKER", "PAPER_BANKROLL", "PAPER_DB_PATH", "FUTURES_PRICES_PATH",
        "CURRENT_TARGET_UPPER_BPS",
    }:
        del os.environ[_name]
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp())
import src.config  # noqa: E402,F401
os.chdir(_cwd)
