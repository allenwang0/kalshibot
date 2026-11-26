"""Run one offline paper cycle on the replay fixture and save what the bot logged.

    uv run python scripts/replay_snapshot.py [--out results/replay_cycle.json]

Runs `main.py --once --replay tests/fixtures/replay.json` against the example
futures CSV into a throwaway SQLite log, then writes the cycle rows (model
probability, best bid and ask per strike, action), the paper fills and the
sizing settings to JSON. Both inputs are fabricated: the fixture keeps the
API's response shapes with made-up prices, and the CSV is synthetic. The output
shows the bot's arithmetic on one snapshot; it is not a trading result.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "tests/fixtures/replay.json"
PRICES = "data/fed_funds_futures.example.csv"
CURRENT_UPPER_BPS = 400


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="results/replay_cycle.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "replay.db"
        env = dict(
            os.environ,
            DB_PATH=str(db),
            FUTURES_PRICES_PATH=PRICES,
            CURRENT_TARGET_UPPER_BPS=str(CURRENT_UPPER_BPS),
            TRADING_MODE="PAPER",
        )
        subprocess.run(
            [sys.executable, "main.py", "--once", "--replay", FIXTURE],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
        )
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        cycles = [
            dict(r)
            for r in conn.execute(
                "SELECT ticker, strike, meeting_date, model_p, best_bid, best_ask, action"
                " FROM cycles ORDER BY meeting_date, strike"
            )
        ]
        fills = [
            dict(r) for r in conn.execute("SELECT ticker, count, price_cents, fee_cents FROM fills")
        ]
        conn.close()
    sys.path.insert(0, str(ROOT))
    from src.config import settings

    fixture_note = json.loads((ROOT / FIXTURE).read_text())["_note"]
    prices_note = (ROOT / PRICES).read_text().splitlines()[0].lstrip("# ")
    out = {
        "inputs": {
            "fixture": FIXTURE,
            "fixture_note": fixture_note,
            "prices": PRICES,
            "prices_note": prices_note,
            "current_target_upper_bps": CURRENT_UPPER_BPS,
        },
        "settings": {
            "kelly_fraction": settings.KELLY_FRACTION,
            "shrinkage": settings.SHRINKAGE,
            "fee_rate": settings.FEE_RATE,
            "max_position": settings.MAX_POSITION_SIZE,
            "max_exposure": settings.MAX_TOTAL_EXPOSURE,
            "paper_bankroll": settings.PAPER_BANKROLL,
        },
        "cycles": cycles,
        "fills": fills,
    }
    path = ROOT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
