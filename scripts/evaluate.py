"""Score the model against the market on settled contracts and build the report.

    python scripts/evaluate.py                       # print metrics
    python scripts/evaluate.py --update-settlements  # fetch results (public endpoint)
    python scripts/evaluate.py --html docs/report/index.html
    python scripts/evaluate.py --card site/card.json

Reads the SQLite log written by the bot (DB_PATH). Each settled contract is
scored once, on the last logged cycle with both a model price and a book that
is at least --horizon-days before settlement. Scoring at the final cycle would
reward whoever is closest to certain minutes before the decision.
"""

import argparse
import datetime
import html
import json
import math
import os
import platform
import random
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BINS = 5
EPS = 1e-6
FIGURES = ("delta", "cal", "pnl", "edge", "ts")  # chart ids, in page order


@dataclass
class Prediction:
    ticker: str
    model_p: float
    mid: float
    outcome: int


@dataclass
class Results:
    synthetic: bool
    cycles: int
    first_cycle: str | None
    predictions: list[Prediction]
    first_settled: str | None = None
    last_settled: str | None = None
    pnl: list[tuple[str, float, float]] = field(default_factory=list)  # ts, before, after fees
    edges: list[tuple[float, float]] = field(default_factory=list)  # edge, realized (cents)

    @property
    def n(self) -> int:
        return len(self.predictions)


def brier(ps: list[float], ys: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / len(ps)


def log_loss(ps: list[float], ys: list[int]) -> float:
    total = 0.0
    for p, y in zip(ps, ys, strict=True):
        p = min(max(p, EPS), 1 - EPS)
        total -= y * math.log(p) + (1 - y) * math.log(1 - p)
    return total / len(ps)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval for k successes out of n."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def reliability(ps: list[float], ys: list[int]) -> list[dict[str, float]]:
    """Equal-width bins with a 95 % Wilson interval each; empty bins are left out."""
    rows = []
    for b in range(BINS):
        lo, hi = b / BINS, (b + 1) / BINS
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (b == BINS - 1 and p == 1.0)]
        if idx:
            yes = sum(ys[i] for i in idx)
            ci_lo, ci_hi = wilson(yes, len(idx))
            rows.append(
                {
                    "lo": lo,
                    "hi": hi,
                    "count": len(idx),
                    "yes": yes,
                    "mean_p": sum(ps[i] for i in idx) / len(idx),
                    "freq": yes / len(idx),
                    "ci_lo": ci_lo,
                    "ci_hi": ci_hi,
                }
            )
    return rows


def max_drawdown(values: list[float]) -> tuple[float, int]:
    """Largest peak-to-trough fall and the index of the trough."""
    peak, worst, at = 0.0, 0.0, 0
    for i, v in enumerate(values):
        peak = max(peak, v)
        if peak - v > worst:
            worst, at = peak - v, i
    return worst, at


def load(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        sys.exit(f"error: no database at {db_path}; run the bot or scripts/make_sample.py first")
    conn = sqlite3.connect(db_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"cycles", "fills", "settlements"} <= tables:
        sys.exit(f"error: {db_path} is not a kalshibot log (missing tables)")
    return conn


def compute(conn: sqlite3.Connection, horizon_days: float = 7.0) -> Results:
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall()) if _has_meta(conn) else {}
    cycles, first_cycle = conn.execute("SELECT COUNT(*), MIN(ts) FROM cycles").fetchone()
    results = Results(meta.get("synthetic") == "1", cycles, first_cycle, [])
    settlements = {
        t: (res, ts)
        for t, res, ts in conn.execute("SELECT ticker, result, settled_ts FROM settlements")
    }
    chosen: dict[str, tuple[float, float]] = {}
    rows = conn.execute(
        "SELECT ticker, ts, model_p, mid FROM cycles WHERE model_p IS NOT NULL"
        " AND mid IS NOT NULL ORDER BY ticker, ts"
    )
    for ticker, ts, model_p, mid in rows:
        if ticker not in settlements:
            continue
        cutoff = _parse_ts(settlements[ticker][1]) - datetime.timedelta(days=horizon_days)
        if _parse_ts(ts) <= cutoff:
            chosen[ticker] = (model_p, mid)  # rows are in time order; keep the last
    settled_ts = []
    for ticker, (model_p, mid) in sorted(chosen.items()):
        result, ts = settlements[ticker]
        results.predictions.append(Prediction(ticker, model_p, mid, int(result == "yes")))
        settled_ts.append(ts)
    if settled_ts:
        results.first_settled, results.last_settled = min(settled_ts)[:10], max(settled_ts)[:10]

    fills = conn.execute(
        """SELECT s.settled_ts, f.count, f.price_cents, f.fee_cents, f.model_p, s.result
           FROM fills f JOIN settlements s ON s.ticker = f.ticker ORDER BY s.settled_ts"""
    ).fetchall()
    before = after = 0.0
    for ts, count, price, fee, model_p, result in fills:
        payout = 100 * count if result == "yes" else 0
        before += (payout - count * price) / 100
        after += (payout - count * price - fee) / 100
        results.pnl.append((ts, before, after))
        if model_p is not None:
            per_fee = fee / count
            results.edges.append(
                (model_p * 100 - price - per_fee, payout / count - price - per_fee)
            )
    return results


def _parse_ts(ts: str) -> datetime.datetime:
    parsed = datetime.datetime.fromisoformat(ts)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.UTC)


def _has_meta(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'")
    return row.fetchone() is not None


def summary(r: Results) -> dict[str, Any]:
    if not r.n:
        return {"n": 0, "cycles": r.cycles, "first_cycle": r.first_cycle, "synthetic": r.synthetic}
    ps = [p.model_p for p in r.predictions]
    ms = [p.mid for p in r.predictions]
    ys = [p.outcome for p in r.predictions]
    return {
        "n": r.n,
        "cycles": r.cycles,
        "first_cycle": r.first_cycle,
        "synthetic": r.synthetic,
        "first_settled": r.first_settled,
        "last_settled": r.last_settled,
        "model_brier": brier(ps, ys),
        "market_brier": brier(ms, ys),
        "model_log_loss": log_loss(ps, ys),
        "market_log_loss": log_loss(ms, ys),
        "model_reliability": reliability(ps, ys),
        "market_reliability": reliability(ms, ys),
        "pnl_after_fees": r.pnl[-1][2] if r.pnl else 0.0,
        "pnl_before_fees": r.pnl[-1][1] if r.pnl else 0.0,
        "settled_fills": len(r.pnl),
    }


def brier_takeaway(s: dict[str, Any]) -> str:
    verdict = "beats" if s["model_brier"] < s["market_brier"] else "does not beat"
    return (
        f"Model Brier {s['model_brier']:.3f} vs market {s['market_brier']:.3f} over "
        f"{s['n']} contracts: the model {verdict} the market"
    )


# ---------------------------------------------------------------- report
#
# The page is one self-contained HTML file: report-kit v1 (docs/kit/, vendored,
# never edited) is inlined, the data is inlined as JSON, and the charts are
# drawn by the kit's SVG helpers. Every number on the page comes from the log.

DOCS = ROOT / "docs"
KIT = DOCS / "kit"
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 1
REPO_URL = "https://github.com/allenwang0/kalshibot"


def bootstrap_delta(
    ps: list[float], ms: list[float], ys: list[int], score: Any
) -> tuple[float, float, float]:
    """score(model) - score(market) and a 95 % percentile interval from
    resampling contracts with replacement (fixed seed)."""
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(ys)
    deltas = []
    for _ in range(BOOTSTRAP_REPS):
        idx = [rng.randrange(n) for _ in range(n)]
        deltas.append(
            score([ps[i] for i in idx], [ys[i] for i in idx])
            - score([ms[i] for i in idx], [ys[i] for i in idx])
        )
    deltas.sort()
    return (
        score(ps, ys) - score(ms, ys),
        deltas[int(0.025 * BOOTSTRAP_REPS)],
        deltas[int(0.975 * BOOTSTRAP_REPS) - 1],
    )


def pnl_by_settlement(r: Results) -> list[tuple[str, float, float]]:
    """Cumulative P&L at each settlement time. Fills that settle together are
    one step: their order inside a settlement is arbitrary, so a drawdown
    measured between them would not be real."""
    out: dict[str, tuple[float, float]] = {}
    for ts, before, after in r.pnl:
        out[ts] = (before, after)
    return [(ts, b, a) for ts, (b, a) in sorted(out.items())]


def edge_bins(edges: list[tuple[float, float]]) -> list[dict[str, float]]:
    """Fills grouped by model edge; mean realized P&L with a normal 95 % CI."""
    xs = [e[0] for e in edges]
    lo, hi = math.floor(min(xs)), math.ceil(max(xs))
    width = max(1, math.ceil((hi - lo) / 8))
    rows = []
    for start in range(lo, hi + 1, width):
        bucket = [(x, y) for x, y in edges if start <= x < start + width]
        if not bucket:
            continue
        ys = [y for _, y in bucket]
        mean = sum(ys) / len(ys)
        sd = math.sqrt(sum((y - mean) ** 2 for y in ys) / (len(ys) - 1)) if len(ys) > 1 else 0.0
        half = 1.96 * sd / math.sqrt(len(ys)) if len(ys) > 1 else 0.0
        rows.append(
            {
                "lo": start,
                "hi": start + width,
                "n": len(ys),
                "edge": sum(x for x, _ in bucket) / len(bucket),
                "mean": mean,
                "ci_lo": mean - half,
                "ci_hi": mean + half,
            }
        )
    return rows


def contracts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-contract series for the explorer, rounded so the page is small and
    byte-identical across platforms (libm differs in the last bit)."""
    settled = dict(conn.execute("SELECT ticker, result FROM settlements").fetchall())
    tickers = [
        row[0]
        for row in conn.execute(
            "SELECT ticker FROM cycles WHERE mid IS NOT NULL GROUP BY ticker "
            "ORDER BY MIN(meeting_date), ticker"
        )
    ]
    out = []
    for ticker in tickers:
        rows = conn.execute(
            "SELECT ts, model_p, mid, best_bid, best_ask, meeting_date FROM cycles "
            "WHERE ticker = ? AND mid IS NOT NULL ORDER BY ts",
            (ticker,),
        ).fetchall()
        gaps = [abs(r[1] - r[2]) for r in rows if r[1] is not None]
        out.append(
            {
                "ticker": ticker,
                "meeting": rows[-1][5],
                "result": settled.get(ticker),
                "t": [int(_parse_ts(r[0]).timestamp()) for r in rows],
                "model": [None if r[1] is None else round(r[1], 3) for r in rows],
                "mid": [round(r[2], 3) for r in rows],
                "bid": [None if r[3] is None else r[3] for r in rows],
                "ask": [None if r[4] is None else r[4] for r in rows],
                "gap": sum(gaps) / len(gaps) if gaps else 0.0,
            }
        )
    return out


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def _cents(v: float) -> str:
    return f"{v:.1f} ¢"


def _usd(v: float) -> str:
    return f"−${-v:,.2f}" if v < 0 else f"${v:,.2f}"


def _signed(v: float) -> str:
    return f"{v:+.3f}".replace("-", "−")


def _verdict(lo: float, hi: float) -> str:
    if lo > 0:
        return "market"
    if hi < 0:
        return "model"
    return "tie"


def report_data(conn: sqlite3.Connection, r: Results, s: dict[str, Any]) -> dict[str, Any]:
    """Everything the page shows, computed from the log."""
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall()) if _has_meta(conn) else {}
    d: dict[str, Any] = {
        "n": s["n"],
        "synthetic": r.synthetic,
        "seed": meta.get("seed"),
        "cycles": r.cycles,
        "since": (r.first_cycle or "")[:10],
    }
    if not s["n"]:
        return d
    ps = [p.model_p for p in r.predictions]
    ms = [p.mid for p in r.predictions]
    ys = [p.outcome for p in r.predictions]
    d["delta_log_loss"] = bootstrap_delta(ps, ms, ys, log_loss)
    d["delta_brier"] = bootstrap_delta(ps, ms, ys, brier)
    for key in ("model_reliability", "market_reliability"):
        d[key] = [dict(row) for row in s[key]]
    if r.pnl:
        steps = pnl_by_settlement(r)
        dd, at = max_drawdown([0.0] + [a for _, _, a in steps])
        d["pnl"] = {
            "start": r.first_cycle,
            "steps": [[ts, round(b, 2), round(a, 2)] for ts, b, a in steps],
            "after": s["pnl_after_fees"],
            "before": s["pnl_before_fees"],
            "fills": s["settled_fills"],
            "drawdown": dd,
            "drawdown_at": steps[at - 1][0][:10] if dd > 0 else None,
            "settlements": len(steps),
        }
    if r.edges:
        d["edges"] = {
            "bins": edge_bins(r.edges),
            "mean_edge": sum(e[0] for e in r.edges) / len(r.edges),
            "mean_real": sum(e[1] for e in r.edges) / len(r.edges),
            "n": len(r.edges),
        }
    return d


def _figure(fid: str, title: str, sub: str, legend: str = "") -> str:
    return (
        f'<figure class="figure" aria-labelledby="f-{fid}"><div class="fig-head"><div>'
        f'<h3 id="f-{fid}">{html.escape(title)}</h3><p class="sub">{html.escape(sub)}</p></div>'
        '<div class="seg" role="radiogroup" aria-label="View" data-view-toggle>'
        '<button role="radio" aria-checked="true" data-value="chart">Chart</button>'
        '<button role="radio" aria-checked="false" data-value="table">Table</button></div></div>'
        f'{legend}<div id="c-{fid}"></div><div id="t-{fid}" hidden></div></figure>'
    )


def _legend(*items: tuple[str, str]) -> str:
    keys = {
        "dot": '<circle cx="5" cy="5" r="4" fill="var(--ink-3)"/>',
        "ring": '<circle cx="5" cy="5" r="4" fill="none" stroke="var(--ink-3)" stroke-width="2"/>',
        "line": '<line x1="0" y1="5" x2="10" y2="5" stroke="var(--ink-3)" stroke-width="2"/>',
    }
    spans = "".join(
        f'<span><svg width="10" height="10">{keys[k]}</svg>{html.escape(t)}</span>'
        for k, t in items
    )
    return f'<div class="legend" aria-hidden="true">{spans}</div>'


def _section(sid: str, eyebrow: str, h2: str, lede: str, body: str) -> str:
    return (
        f'<section class="s wrap" id="{sid}" aria-labelledby="h-{sid}">'
        f'<p class="eyebrow">{html.escape(eyebrow)}</p><h2 id="h-{sid}">{html.escape(h2)}</h2>'
        f'<p class="lede">{html.escape(lede)}</p>{body}</section>'
    )


def _caveat(lead: str, text: str) -> str:
    return f'<p class="caveat" role="note"><b>{html.escape(lead)}</b> {html.escape(text)}</p>'


def _card(span: int, label: str, metric: str, unit: str, cmp: str, foot: str) -> str:
    u = f' <span class="unit">{html.escape(unit)}</span>' if unit else ""
    return (
        f'<div class="card span-{span}"><span class="label">{html.escape(label)}</span>'
        f'<span class="metric num">{html.escape(metric)}{u}</span>'
        f'<span class="cmp">{html.escape(cmp)}</span><span class="foot">{foot}</span></div>'
    )


def render_html(
    conn: sqlite3.Connection, r: Results, s: dict[str, Any], horizon_days: float = 7.0
) -> str:
    d = report_data(conn, r, s)
    hz = f"{horizon_days:g}"
    synth = r.synthetic
    seed = d["seed"]
    synth_label = (
        f"Synthetic sample (seed {seed}), not real results"
        if seed
        else ("Synthetic sample, not real results")
    )
    sha = os.environ.get("GITHUB_SHA") or _git("rev-parse", "HEAD")
    short = sha[:7] if sha else "unknown"
    date = _git("log", "-1", "--format=%cs") or ""
    parts: list[str] = []
    n = s["n"]

    # ---- hero
    if n:
        verdicts = {_verdict(*d["delta_log_loss"][1:]), _verdict(*d["delta_brier"][1:])}
        prefix = "On a synthetic sample of" if synth else "On"
        if verdicts == {"tie"}:
            h1 = f"{prefix} {n} contracts, model and market score within noise of each other"
        elif verdicts <= {"model", "tie"}:
            h1 = f"{prefix} {n} contracts, the model out-forecasts the market mid"
        elif verdicts <= {"market", "tie"}:
            h1 = f"{prefix} {n} contracts, the market mid out-forecasts the model"
        else:
            h1 = f"{prefix} {n} contracts, the two scores disagree on model versus market"
    else:
        h1 = "No contracts have settled yet, so nothing is scored"
    lede = (
        "kalshibot turns Fed Funds futures into a probability for each Kalshi Fed-rate "
        "contract. This page scores those probabilities against the market mid at least "
        f"{hz} days before each contract settled, then follows the paper trades."
    )
    conds = []
    if synth:
        conds.append(f'<span class="key">{html.escape(synth_label)}</span>')
    if n:
        conds += [
            f"<span>{n} settled contracts, {s['first_settled']} to {s['last_settled']}</span>",
            f"<span>Scored ≥ {hz} days before settlement</span>",
            "<span>Paper trading only</span>",
        ]
    hero = (
        '<section class="hero wrap" aria-labelledby="h1"><p class="eyebrow">'
        "Forecast evaluation · Python</p>"
        f'<h1 id="h1">{html.escape(h1)}</h1><p class="lede">{html.escape(lede)}</p>'
        f'<p class="conditions">{"".join(conds)}</p>'
    )
    if n:
        dl, db = d["delta_log_loss"], d["delta_brier"]
        hero += _figure(
            "delta",
            "Both 95 % intervals cross zero"
            if {_verdict(*dl[1:]), _verdict(*db[1:])} == {"tie"}
            else "Score differences, model minus market",
            f"Model − market · lower favors the model · n = {n} contracts",
        )
        hero += (
            "<figcaption>Each dot is the model's score minus the market mid's score on the same "
            f"{n} contracts (log loss {_signed(dl[0])}, Brier {_signed(db[0])}). Lines are 95 % "
            f"intervals from {BOOTSTRAP_REPS:,} bootstrap resamples of contracts. An interval "
            "that crosses zero means this sample can't tell the two apart.</figcaption>"
        )
    else:
        since = d["since"]
        hero += (
            f'<p class="empty">No settled contracts yet, {r.cycles} cycles logged since {since}</p>'
        )
    parts.append(hero + "</section>")
    if not n:
        return _page(parts, d, short, date)

    # ---- bento
    foot_n = f"n = {n} contracts · lower is better"
    cards = [
        _card(
            4,
            "Log loss, model",
            f"{s['model_log_loss']:.3f}",
            "",
            f"vs {s['market_log_loss']:.3f} for the market mid",
            html.escape(foot_n),
        ),
        _card(
            4,
            "Brier score, model",
            f"{s['model_brier']:.3f}",
            "",
            f"vs {s['market_brier']:.3f} for the market mid",
            html.escape(foot_n),
        ),
    ]
    if "pnl" in d:
        p = d["pnl"]
        foot = f"{p['fills']} settled fills"
        if synth:
            foot = f"<b>{html.escape(synth_label)}</b> · {foot}"
        cards.append(
            _card(
                4,
                "Paper P&L, after fees",
                _usd(p["after"]),
                "",
                f"{_usd(p['before'])} before {_usd(p['before'] - p['after'])} in fees",
                foot,
            )
        )
    parts.append(
        '<section class="wrap" aria-label="Key results"><div class="bento">'
        + "".join(cards)
        + "</div></section>"
    )

    # ---- calibration
    rel = d["model_reliability"] + d["market_reliability"]
    inside = sum(1 for b in rel if b["ci_lo"] <= b["mean_p"] <= b["ci_hi"])
    counts = [b["count"] for b in rel]
    widths = [b["ci_hi"] - b["ci_lo"] for b in rel]
    parts.append(
        _section(
            "calibration",
            "Is each forecaster calibrated?",
            f"With {n} contracts, the calibration diagram is mostly noise",
            f"In {inside} of {len(rel)} bins across both forecasters, the mean forecast falls "
            "inside the 95 % interval of what happened. The intervals are too wide to rank the "
            "two forecasters; the score differences above are the better test.",
            _figure(
                "cal",
                "Observed YES rate per forecast bin",
                f"{BINS} equal-width bins · n per bin shown as model / market · 95 % Wilson CI",
                _legend(
                    ("dot", "Observed YES rate"),
                    ("ring", "Mean forecast"),
                    ("line", "95 % interval"),
                ),
            )
            + "<figcaption>Contracts are grouped by each forecaster's own probability, so a bin "
            "can hold different contracts for the model and the market. A calibrated "
            "forecaster's ring sits inside its line.</figcaption>"
            + _caveat(
                "Small n.",
                f"Each bin holds {min(counts)}–{max(counts)} contracts, so its 95 % interval is "
                f"{min(widths):.2f}–{max(widths):.2f} wide. Read differences between bins as "
                "noise.",
            ),
        )
    )

    # ---- P&L
    if "pnl" in d:
        p = d["pnl"]
        sign = "made" if p["after"] >= 0 else "lost"
        h2 = f"Paper trading {sign} {_usd(abs(p['after']))} after fees over {p['fills']} fills"
        if synth:
            h2 += " on the synthetic sample"
        lede_c = (
            f"Fees took {_usd(p['before'] - p['after'])} of the {_usd(p['before'])} earned before "
            f"fees. The worst fall from a peak was {_usd(p['drawdown'])}"
            + (f", reached at the {p['drawdown_at']} settlement." if p["drawdown_at"] else ".")
        )
        body = _figure(
            "pnl",
            "Cumulative P&L after and before fees",
            f"USD · {p['fills']} fills over {p['settlements']} settlement dates",
        )
        body += (
            "<figcaption>Cumulative P&L at each settlement date, starting at $0 on the first "
            "logged cycle. Fills that settle at the same time count as one step. The dashed line "
            "adds the fees back.</figcaption>"
        )
        if synth:
            body += _caveat(
                synth_label + ".",
                "make_sample.py adds noise to a known true probability and logs a fill only "
                "when the model's edge passes a threshold, so this P&L is set by the generator. "
                "It shows the accounting works, not that the strategy makes money.",
            )
        parts.append(_section("pnl", "Does the strategy make money after fees?", h2, lede_c, body))

    # ---- edges
    if "edges" in d:
        e = d["edges"]
        lede_d = (
            f"Mean edge after fees was {_cents(e['mean_edge'])} per contract; mean realized P&L "
            f"was {_cents(e['mean_real'])} over {e['n']} fills. Each contract pays 100 ¢ or 0, "
            "so single fills swing widely and only bin means carry signal."
        )
        any_pos = any(b["ci_lo"] > 0 for b in e["bins"])
        h2 = (
            "Realized returns are too noisy at this sample size to confirm the edge"
            if not any_pos
            else "Some edge bins earn a positive return beyond noise"
        )
        body = _figure(
            "edge",
            "Mean realized P&L by model edge bin",
            f"Cents per contract · {e['n']} fills · 95 % CI of the bin mean",
            _legend(
                ("dot", "Mean realized"),
                ("ring", "Mean model edge"),
                ("line", "95 % interval"),
            ),
        )
        body += (
            "<figcaption>Fills grouped by the model's edge after fees at the time of the trade. "
            "If the edge were real and correctly sized, each dot would sit on its ring. "
            "Intervals are mean ± 1.96 standard errors.</figcaption>"
        )
        parts.append(_section("edges", "Do bigger edges earn more?", h2, lede_d, body))

    # ---- explorer
    cs = contracts(conn)
    if cs:
        d["contracts"] = cs
        default = max(range(len(cs)), key=lambda i: cs[i]["gap"])
        mean_gap = sum(c["gap"] for c in cs) / len(cs)
        options = "".join(
            f'<option value="{i}"{" selected" if i == default else ""}>'
            f"{html.escape(c['ticker'])}</option>"
            for i, c in enumerate(cs)
        )
        body = (
            '<div class="picker"><label for="kb-contract">Contract</label>'
            f'<select id="kb-contract">{options}</select>'
            '<span class="status" id="kb-status" aria-live="polite"></span></div>'
            + _figure(
                "ts",
                "Model and market mid over the logged cycles",
                "Probability of YES · band shows bid to ask",
            )
            + "<figcaption>Every logged cycle for one contract. Opens on the contract where the "
            "model and market disagreed most on average.</figcaption>"
        )
        parts.append(
            _section(
                "explorer",
                "Where did the model and market disagree?",
                f"The model sat {mean_gap * 100:.1f} ¢ from the market mid on average across "
                f"{len(cs)} contracts",
                "Pick a contract to see where the two split and how it settled.",
                body,
            )
        )

    # ---- method
    parts.append(
        '<section class="s wrap" id="method" aria-labelledby="h-method">'
        '<p class="eyebrow">Methodology</p><h2 id="h-method">How this was scored</h2>'
        '<div class="prose" style="margin-top:24px">'
        "<p>Each settled contract is scored once, on the last logged cycle that has both a "
        f"model price and an order book and falls at least {hz} days before "
        "settlement. Scoring the final cycle would reward whoever is closest to certain minutes "
        "before the decision.</p>"
        "<p>The market's forecast is the mid of the best bid and ask at that cycle. Paper "
        "fills pay 100 ¢ per contract on YES and nothing on NO, minus the price and Kalshi's "
        "taker fee.</p></div>"
        '<dl class="kv prose"><dt>Brier score</dt><dd>Mean squared error of the probability; '
        "0 is perfect.</dd><dt>Log loss</dt><dd>Mean negative log-likelihood of the outcome; "
        "punishes confident misses hardest.</dd><dt>Bootstrap interval</dt><dd>"
        f"{BOOTSTRAP_REPS:,} resamples of contracts with replacement, seed {BOOTSTRAP_SEED}; "
        "2.5th to 97.5th percentile.</dd><dt>Wilson interval</dt><dd>95 % binomial interval "
        "for the YES rate in a bin.</dd></dl>"
        '<div class="two"><div><h3>What this shows</h3><ul>'
        "<li>The pipeline logs prices, scores model and market on the same contracts, and "
        "accounts for fees end to end.</li>"
        f"<li>On these {n} contracts, how far apart the two forecasters' scores are, with "
        "uncertainty.</li></ul></div>"
        "<div><h3>What it doesn't show</h3><ul>"
        + (
            "<li>Real performance: every price and outcome here is synthetic.</li>"
            if synth
            else "<li>Performance beyond this log's meetings and contracts.</li>"
        )
        + "<li>An edge: the P&L depends on how the fills were generated, and the bins are "
        "too small to confirm the model's edge.</li>"
        "<li>Calibration at this n: bins hold too few contracts to judge.</li></ul></div></div>"
        "</section>"
    )

    # ---- reproduce
    env = [
        ("Data", synth_label if synth else "Paper-trading log (SQLite)"),
        ("Settled contracts", f"{n}, {s['first_settled']} to {s['last_settled']}"),
        ("Cycles logged", f"{r.cycles:,}"),
        ("Scoring horizon", f"≥ {hz} days before settlement"),
        ("Bootstrap", f"{BOOTSTRAP_REPS:,} resamples, seed {BOOTSTRAP_SEED}"),
        ("Python", platform.python_version()),
        ("Commit", f'<span class="mono">{html.escape(short)}</span>'),
    ]
    rows = "".join(
        f'<tr><th scope="row">{html.escape(k)}</th><td>'
        f"{v if k == 'Commit' else html.escape(v)}</td></tr>"
        for k, v in env
    )
    seed_arg = f" --seed {seed}" if seed else ""
    parts.append(
        '<section class="s wrap" id="reproduce" aria-labelledby="h-repro">'
        '<p class="eyebrow">Reproduce</p><h2 id="h-repro">Run it yourself</h2>'
        '<div class="codewrap" style="margin-top:24px"><pre><code>'
        f"git clone {REPO_URL}.git &amp;&amp; cd kalshibot\n"
        "uv sync\n"
        + (f"uv run python scripts/make_sample.py --db sample.db{seed_arg}\n" if synth else "")
        + "uv run python scripts/evaluate.py --db sample.db --html docs/report/index.html"
        "</code></pre></div>"
        '<div class="tablewrap env" tabindex="0" role="region" aria-label="Environment" '
        'style="margin-top:24px"><table><caption>Environment for this build</caption>'
        '<thead><tr><th scope="col">Field</th><th scope="col" style="text-align:left">Value'
        f"</th></tr></thead><tbody>{rows}</tbody></table></div></section>"
    )
    return _page(parts, d, short, date)


def _rounded(v: Any) -> Any:
    """Round floats to 6 places: enough for every figure, and it hides last-bit libm
    differences between macOS and Linux so local and CI builds match byte for byte."""
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, dict):
        return {k: _rounded(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_rounded(x) for x in v]
    return v


def _page(parts: list[str], d: dict[str, Any], short: str, date: str) -> str:
    def kit(name: str) -> str:
        # report.js mentions "</script>" in a comment; escape it so inlining
        # doesn't close the surrounding <script> tag early.
        return (KIT / name).read_text().replace("</script", "<\\/script")

    data = json.dumps(_rounded(d), separators=(",", ":"))
    data = data.replace("</", "<\\/")
    footer = f'Generated from commit <span class="mono">{html.escape(short)}</span>' + (
        f" · {html.escape(date)}" if date else ""
    )
    description = (
        "Scores a Fed Funds futures model against Kalshi market prices on settled Fed-rate "
        "contracts and tracks paper P&L after fees."
    )
    page = (DOCS / "report.template.html").read_text()
    for key, value in (
        ("%%KIT_CSS%%", kit("report.css")),
        ("%%LOCAL_CSS%%", (DOCS / "report.local.css").read_text()),
        ("%%KIT_JS%%", kit("report.js") + "\n" + kit("charts.js")),
        ("%%LOCAL_JS%%", (DOCS / "report.local.js").read_text()),
        ("%%DESCRIPTION%%", description),
        ("%%REPO%%", REPO_URL),
        ("%%FOOTER%%", footer),
        ("%%BODY%%", "\n".join(parts)),
        ("%%DATA%%", data),
    ):
        page = page.replace(key, value)
    return page


# ---------------------------------------------------------------- card


def build_card(s: dict[str, Any], command: str) -> dict[str, Any]:
    if s["synthetic"]:
        raise SystemExit("error: refusing to write a card from synthetic sample data")
    if not s["n"]:
        raise SystemExit("error: refusing to write a card with zero settled contracts")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    takeaway = brier_takeaway(s)[:140]

    def points(rows: list[dict[str, float]]) -> list[list[float]]:
        return [[round(r["mean_p"], 4), round(r["freq"], 4)] for r in rows]

    return {
        "schema_version": 1,
        "repo": "kalshibot",
        "sha": sha,
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "command": command,
        "data_note": (
            f"{s['n']} settled Kalshi Fed-rate contracts, {s['first_settled']} to "
            f"{s['last_settled']}, paper mode"
        ),
        "metric": {
            "label": "Model Brier score",
            "value": round(s["model_brier"], 4),
            "unit": "",
            "higher_is_better": False,
        },
        "takeaway": takeaway,
        "chart": {
            "type": "line",
            "x_label": "Mean predicted probability",
            "x_unit": "",
            "x_scale": "linear",
            "y_label": "Observed frequency",
            "y_unit": "",
            "y_scale": "linear",
            "series": [
                {"name": "Model", "style": "dots", "points": points(s["model_reliability"])},
                {"name": "Market mid", "style": "dots", "points": points(s["market_reliability"])},
                {"name": "Perfect calibration", "style": "dashed", "points": [[0, 0], [1, 1]]},
            ],
        },
    }


# ---------------------------------------------------------------- settlements


def update_settlements(db_path: Path) -> int:
    from src.client import KalshiClient
    from src.ledger import Ledger

    ledger = Ledger(str(db_path))
    client = KalshiClient()
    known = ledger.settlements()
    added = 0
    for ticker in ledger.logged_tickers():
        if ticker in known:
            continue
        market = client.get_market(ticker)
        if market.get("result") in ("yes", "no") and market.get("settlement_ts"):
            ledger.set_settlement(ticker, market["result"], market["settlement_ts"])
            added += 1
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=None, help="SQLite log (default: DB_PATH setting)")
    parser.add_argument("--html", help="write the report to this path")
    parser.add_argument("--card", help="write the portfolio card JSON to this path")
    parser.add_argument(
        "--update-settlements",
        action="store_true",
        help="fetch results for logged markets from the public Kalshi API",
    )
    parser.add_argument(
        "--horizon-days",
        type=float,
        default=7.0,
        help="score each contract this many days before settlement",
    )
    args = parser.parse_args(argv)
    command = "python scripts/evaluate.py " + shlex.join(argv if argv is not None else sys.argv[1:])
    command = command.strip()

    if args.db is None:
        from src.config import settings

        args.db = settings.DB_PATH
    db_path = Path(args.db)
    if args.update_settlements:
        load(db_path)
        print(f"settlements added: {update_settlements(db_path)}")
    conn = load(db_path)
    r = compute(conn, args.horizon_days)
    s = summary(r)
    if s["n"]:
        print(brier_takeaway(s))
        print(f"log loss: model {s['model_log_loss']:.4f}, market {s['market_log_loss']:.4f}")
        print(f"settled: {s['first_settled']} to {s['last_settled']}")
        print(
            f"paper P&L after fees: ${s['pnl_after_fees']:.2f} "
            f"(before fees ${s['pnl_before_fees']:.2f}, {s['settled_fills']} settled fills)"
        )
    else:
        print(
            f"No settled contracts yet, {r.cycles} cycles logged since {(r.first_cycle or '')[:10]}"
        )
    if r.synthetic:
        print("note: synthetic sample data")
    if args.html:
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(conn, r, s, args.horizon_days))
        print(f"wrote {out}")
    if args.card:
        card = build_card(s, command)
        out = Path(args.card)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(card, indent=2) + "\n")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
