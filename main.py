import time
import requests
from src.client import KalshiAPIError
from src.engine import TradingBot

def main():
    print("Initializing Kalshi Quantitative Engine...")
    bot = TradingBot()
    try:
        while True:
            try:
                bot.run_cycle()
            except (KalshiAPIError, requests.RequestException) as e:
                print(f"[CYCLE SKIPPED] {e}")
            time.sleep(60)
    except KeyboardInterrupt:
        print("Stopped.")

if __name__ == "__main__":
    main()