# Kalshi Deep Trading Bot

A straightforward trading bot for [Kalshi](https://kalshi.com) prediction markets that uses Octagon Deep Research for market analysis and OpenAI for structured betting decisions.

![Kalshi Deep Trading Bot]

## How It Works

The bot follows a simple 6-step workflow:

1. **Fetch Events:** Gets top 50 events from Kalshi sorted by volume (filtered by status and time).
2. **Process Markets:** Uses top 10 highest volume markets per event.
3. **Research Events:** Uses Octagon Deep Research to analyze event + markets (without odds).
4. **Fetch Market Odds:** Gets current bid/ask prices for all markets.
5. **Make Decisions:** Feeds research results and market odds into OpenAI for structured betting decisions.
6. **Place Bets:** Executes the recommended bets via Kalshi API.

## Features

- **Simple & Direct:** No complex strategies or risk management systems.
- **AI-Powered:** Uses Octagon Deep Research for market analysis and OpenAI for decision making.
- **Event-Based:** Analyzes entire events with all markets for better context.
- **Flexible Environment:** Supports both demo and production environments.
- **Dry Run Mode:** Test the bot without placing real bets.
- **Rich Console:** Beautiful progress tracking and result display with probability predictions.
- **Risk Management:** Basic position sizing, confidence thresholds, and hedging capabilities.

## Quick Start

### 1. Install uv
If not already installed:
```bash
curl -LsSf [https://astral.sh/uv/install.sh](https://astral.sh/uv/install.sh) | sh