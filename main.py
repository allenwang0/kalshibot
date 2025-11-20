import time
from src.engine import TradingBot

WATCHLIST = [
    "KX-FEDRATE-DEC24-5.00", 
    "KX-FEDRATE-DEC24-5.25",
    "KX-FEDRATE-DEC24-5.50"
]

def main():
    print("Initializing Kalshi Quantitative Engine...")
    bot = TradingBot()
    try:
        while True:
            bot.run_cycle(WATCHLIST)
            time.sleep(60)
    except KeyboardInterrupt:
        print("Stopped.")

if __name__ == "__main__":
    main()