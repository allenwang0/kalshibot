import os
import tempfile

# Tests must never read a developer's real .env or key env vars. Settings are
# built at import time, so scrub the environment and move to an empty
# directory before any src module is imported.
for _name in list(os.environ):
    if _name.startswith("KALSHI_") or _name in {
        "TRADING_MODE", "KELLY_FRACTION", "MAX_POSITION_SIZE", "MAX_TOTAL_EXPOSURE",
        "MAX_SPREAD_CENTS", "SERIES_TICKER", "PAPER_BANKROLL", "PAPER_DB_PATH",
    }:
        del os.environ[_name]
os.chdir(tempfile.mkdtemp())
