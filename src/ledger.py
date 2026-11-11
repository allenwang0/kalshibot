import datetime
import sqlite3
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    ts TEXT, mode TEXT, ticker TEXT, strike REAL, meeting_date TEXT,
    model_p REAL, best_bid INTEGER, best_ask INTEGER, mid REAL, action TEXT);
CREATE TABLE IF NOT EXISTS fills (
    ts TEXT, ticker TEXT, side TEXT, count INTEGER, price_cents INTEGER,
    fee_cents INTEGER, model_p REAL);
CREATE TABLE IF NOT EXISTS settlements (ticker TEXT PRIMARY KEY, result TEXT, settled_ts TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


class Ledger:
    """SQLite log of every cycle, paper fills and settlements. Holds no
    account balance, order IDs or keys, so it can feed a public report."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def log_cycle(self, **row: Any) -> None:
        cols = ("ts", "mode", "ticker", "strike", "meeting_date", "model_p",
                "best_bid", "best_ask", "mid", "action")
        values = [row.get(c) for c in cols]
        self.db.execute(f"INSERT INTO cycles VALUES ({','.join('?' * len(cols))})", values)
        self.db.commit()

    def add_fill(self, ticker: str, side: str, count: int, price_cents: int,
                 fee_cents: int, model_p: float | None, ts: str | None = None) -> None:
        self.db.execute("INSERT INTO fills VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (ts or utc_now(), ticker, side, count, price_cents, fee_cents, model_p))
        self.db.commit()

    def set_settlement(self, ticker: str, result: str, settled_ts: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO settlements VALUES (?, ?, ?)",
                        (ticker, result, settled_ts))
        self.db.commit()

    def settlements(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT ticker, result FROM settlements").fetchall())

    def logged_tickers(self) -> list[str]:
        rows = self.db.execute("SELECT DISTINCT ticker FROM cycles UNION "
                               "SELECT DISTINCT ticker FROM fills").fetchall()
        return [r[0] for r in rows]

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.db.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])
