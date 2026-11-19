# kalshibot

A trading bot for Kalshi's Fed-rate contracts. It turns Fed Funds futures prices into probabilities for each Fed meeting outcome, compares them with Kalshi's prices, sizes bets after fees, and scores its model against the market once contracts settle.

[![Evaluation report built from synthetic sample data](docs/report-screenshot.png)](https://allenwang0.github.io/kalshibot/)

## Highlights

- **Meeting-by-meeting rate model** that solves each Fed meeting's expected move from monthly futures averages.
- **Fee-aware sizing:** shrinks the model toward the market, prices in Kalshi's fee, then sizes with fractional Kelly (a bet-sizing rule) under exposure caps.
- **Honest scoring:** Brier score and log loss for model versus market, measured at least 7 days before settlement.
- **Runs offline:** a recorded-market replay runs a full paper cycle with no keys and no network.

## Run it

```sh
uv sync
cp .env.example .env
cp data/fed_funds_futures.example.csv data/fed_funds_futures.csv
CURRENT_TARGET_UPPER_BPS=400 uv run python main.py --once --replay tests/fixtures/replay.json
```

## How it works

- Each futures contract implies an average rate for its month; in a meeting month the rate after the meeting can be solved from that average.
- Each meeting's expected move is split between the two nearest 25 bp outcomes and chained from the current rate range.
- A contract's fair value is the model probability of every outcome above its strike, compared with the best ask in Kalshi's order book.
- Paper mode records fills and fees in SQLite; live mode sends limit orders.

## Limitations

- Futures-implied probabilities include a term premium, so they are not pure forecasts.
- No real contracts have settled in the log yet, so nothing here shows an edge.
- Paper mode by default; live trading needs `TRADING_MODE=LIVE` plus API keys, or the bot refuses to start. Not financial advice.
