import os
import tempfile

# Tests must never read a developer's real .env or key env vars. Settings are
# built at import time, so scrub the environment and import src.config from an
# empty directory before any test module imports it.
for _name in list(os.environ):
    if _name.startswith("KALSHI_") or _name in {
        "TRADING_MODE",
        "KELLY_FRACTION",
        "MAX_POSITION_SIZE",
        "MAX_TOTAL_EXPOSURE",
        "MAX_SPREAD_CENTS",
        "SERIES_TICKER",
        "PAPER_BANKROLL",
        "DB_PATH",
        "MIN_ORDER_DOLLARS",
        "MIN_BANKROLL_DOLLARS",
        "LOOP_SECONDS",
        "RATE_LIMIT_PER_SEC",
        "MAX_WORKERS",
        "FUTURES_PRICES_PATH",
        "CURRENT_TARGET_UPPER_BPS",
        "SHRINKAGE",
        "FEE_RATE",
    }:
        del os.environ[_name]
# Ledgers built from default settings stay in memory, never in the working tree.
os.environ["DB_PATH"] = ":memory:"
_cwd = os.getcwd()
os.chdir(tempfile.mkdtemp())
import src.config  # noqa: E402,F401

os.chdir(_cwd)
