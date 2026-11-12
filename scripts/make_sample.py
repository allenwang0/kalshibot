"""Write a SYNTHETIC log for trying the report before real data exists.

    python scripts/make_sample.py --db sample.db --seed 7

Every number is drawn from a seeded random process; none is a real price or
result. The database is marked synthetic, the report shows a banner on every
view, and evaluate.py refuses to build a portfolio card from it.
"""

import argparse
import datetime
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ledger import Ledger  # noqa: E402
from src.risk import taker_fee  # noqa: E402

MEETINGS = [
    "2025-01-29",
    "2025-03-19",
    "2025-05-07",
    "2025-06-18",
    "2025-07-30",
    "2025-09-17",
    "2025-10-29",
    "2025-12-10",
]
STRIKES = [3.50, 3.75, 4.00, 4.25, 4.50, 4.75]
DAYS = 21
CYCLES_PER_DAY = 4


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def make(db_path: Path, seed: int) -> None:
    if db_path.exists():
        db_path.unlink()
    rng = random.Random(seed)
    ledger = Ledger(str(db_path))
    ledger.set_meta("synthetic", "1")
    ledger.set_meta("seed", str(seed))
    for meeting in MEETINGS:
        decision = datetime.datetime.fromisoformat(meeting + "T18:00:00+00:00")
        center = rng.uniform(3.6, 4.6)
        for strike in STRIKES:
            ticker = f"SYNTH-{meeting}-T{strike:.2f}"
            p0 = _sigmoid((center - strike) * 4 + rng.gauss(0, 0.5))
            outcome = int(rng.random() < p0)
            n = DAYS * CYCLES_PER_DAY
            logit = math.log(p0 / (1 - p0))
            for i in range(n):
                t = (i + 1) / n
                # The true probability drifts toward the outcome as the meeting nears.
                target = 6.0 if outcome else -6.0
                logit += (target - logit) * 0.01 * t**3 + rng.gauss(0, 0.12)
                true_p = _sigmoid(logit)
                mid = min(0.99, max(0.01, true_p + rng.gauss(0, 0.03)))
                model_p = min(1.0, max(0.0, true_p + rng.gauss(0, 0.06)))
                half = rng.randint(1, 3)
                bid = max(1, round(mid * 100) - half)
                ask = min(99, round(mid * 100) + half)
                ts = (
                    decision
                    - datetime.timedelta(days=DAYS)
                    + datetime.timedelta(hours=24 / CYCLES_PER_DAY * i)
                )
                ts_s = ts.isoformat(timespec="seconds")
                fee_per = taker_fee(1, ask / 100) * 100
                action = "hold"
                if model_p * 100 - ask - fee_per > 3 and rng.random() < 0.15:
                    count = rng.randint(5, 40)
                    fee_cents = round(taker_fee(count, ask / 100) * 100)
                    ledger.add_fill(ticker, "yes", count, ask, fee_cents, model_p, ts=ts_s)
                    action = f"buy {count}"
                ledger.log_cycle(
                    ts=ts_s,
                    mode="PAPER",
                    ticker=ticker,
                    strike=strike,
                    meeting_date=meeting,
                    model_p=model_p,
                    best_bid=bid,
                    best_ask=ask,
                    mid=(bid + ask) / 200,
                    action=action,
                )
            settled = (decision + datetime.timedelta(minutes=20)).isoformat(timespec="seconds")
            ledger.set_settlement(ticker, "yes" if outcome else "no", settled)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a synthetic kalshibot log.")
    parser.add_argument("--db", default="sample.db")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    make(Path(args.db), args.seed)
    print(f"wrote SYNTHETIC sample to {args.db} (seed {args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
