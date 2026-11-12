import argparse
import logging
import time

import requests

from src.client import KalshiAPIError
from src.config import settings
from src.engine import TradingBot

log = logging.getLogger("kalshibot")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Trade Kalshi Fed-rate markets.")
    parser.add_argument("--series", default=settings.SERIES_TICKER, help="Kalshi series ticker")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args(argv)
    settings.SERIES_TICKER = args.series

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log.info("starting in %s mode on series %s", settings.TRADING_MODE, settings.SERIES_TICKER)
    if settings.TRADING_MODE == "LIVE":
        log.warning("LIVE mode: orders spend real money")
    bot = TradingBot()
    try:
        while True:
            try:
                bot.run_cycle()
            except (KalshiAPIError, requests.RequestException) as e:
                log.error("cycle skipped: %s", e)
                if args.once:
                    raise SystemExit(1) from e
            if args.once:
                break
            time.sleep(settings.LOOP_SECONDS)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
