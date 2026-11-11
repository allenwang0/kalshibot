import time
from src.engine import TradingBot

def main():
    print("Initializing Kalshi Quantitative Engine...")
    bot = TradingBot()
    try:
        while True:
            bot.run_cycle()
            time.sleep(60)
    except KeyboardInterrupt:
        print("Stopped.")

if __name__ == "__main__":
    main()