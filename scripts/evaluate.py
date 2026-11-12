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
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BINS = 10
EPS = 1e-6
# Okabe-Ito, colorblind safe. Color is never the only cue: marker shape and
# line dash also differ.
MODEL_COLOR = "#0072B2"
MARKET_COLOR = "#D55E00"
NEUTRAL_COLOR = "#999999"
VIEW_TITLES = {
    "a": "Calibration: model vs market",
    "b": "Model and market over time",
    "c": "Paper P&L",
    "d": "Edge vs realized return",
}


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


def reliability(ps: list[float], ys: list[int]) -> list[dict[str, float]]:
    """Ten equal-width bins; empty bins are left out."""
    rows = []
    for b in range(BINS):
        lo, hi = b / BINS, (b + 1) / BINS
        idx = [i for i, p in enumerate(ps) if lo <= p < hi or (b == BINS - 1 and p == 1.0)]
        if idx:
            rows.append(
                {
                    "lo": lo,
                    "hi": hi,
                    "count": len(idx),
                    "mean_p": sum(ps[i] for i in idx) / len(idx),
                    "freq": sum(ys[i] for i in idx) / len(idx),
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


def _layout(fig: Any, x_title: str, y_title: str, **extra: Any) -> None:
    layout: dict[str, Any] = {
        "margin": {"l": 56, "r": 16, "t": 16, "b": 48},
        "height": 380,
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "legend": {"orientation": "h", "y": -0.22},
        "hoverlabel": {"namelength": -1},
        "xaxis_title": x_title,
        "yaxis_title": y_title,
    }
    layout.update(extra)
    fig.update_layout(**layout)


def fig_reliability(s: dict[str, Any]) -> Any:
    import plotly.graph_objects as go

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[0, 1],
            y=[0, 1],
            mode="lines",
            name="Perfect calibration",
            line={"color": NEUTRAL_COLOR, "dash": "dot"},
            hoverinfo="skip",
        )
    )
    for name, rows, color, symbol in (
        ("Model", s["model_reliability"], MODEL_COLOR, "circle"),
        ("Market mid", s["market_reliability"], MARKET_COLOR, "diamond"),
    ):
        fig.add_trace(
            go.Scatter(
                x=[r["mean_p"] for r in rows],
                y=[r["freq"] for r in rows],
                mode="markers",
                name=name,
                marker={
                    "color": color,
                    "symbol": symbol,
                    "line": {"width": 1, "color": "white"},
                    "size": [8 + 22 * math.sqrt(r["count"] / s["n"]) for r in rows],
                },
                customdata=[[r["lo"], r["hi"], r["count"]] for r in rows],
                hovertemplate=(
                    f"{name}<br>bin %{{customdata[0]:.1f}} to %{{customdata[1]:.1f}}"
                    "<br>n = %{customdata[2]}<br>mean predicted %{x:.3f}"
                    "<br>observed %{y:.3f}<extra></extra>"
                ),
            )
        )
    _layout(
        fig,
        "Mean predicted probability",
        "Observed frequency",
        xaxis_range=[-0.03, 1.03],
        yaxis_range=[-0.05, 1.05],
    )
    return fig


def fig_timeseries(conn: sqlite3.Connection) -> tuple[Any, int]:
    import plotly.graph_objects as go

    eastern = ZoneInfo("America/New_York")
    settled = dict(conn.execute("SELECT ticker, result FROM settlements").fetchall())
    tickers = [
        r[0]
        for r in conn.execute(
            "SELECT ticker FROM cycles WHERE mid IS NOT NULL GROUP BY ticker "
            "ORDER BY MIN(meeting_date), ticker"
        )
    ]
    fig = go.Figure()
    buttons = []
    per = 5  # traces per contract
    for i, ticker in enumerate(tickers):
        rows = conn.execute(
            "SELECT ts, model_p, mid, best_bid, best_ask, meeting_date FROM cycles "
            "WHERE ticker = ? AND mid IS NOT NULL ORDER BY ts",
            (ticker,),
        ).fetchall()
        ts = [r[0] for r in rows]
        visible = i == 0
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=[r[4] / 100 for r in rows],
                mode="lines",
                line={"width": 0},
                showlegend=False,
                hoverinfo="skip",
                visible=visible,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=[r[3] / 100 for r in rows],
                mode="lines",
                line={"width": 0},
                fill="tonexty",
                name="Bid-ask",
                fillcolor="rgba(213,94,0,0.15)",
                hoverinfo="skip",
                visible=visible,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=[r[2] for r in rows],
                mode="lines",
                name="Market mid",
                line={"color": MARKET_COLOR, "dash": "dash"},
                visible=visible,
                hovertemplate="%{x}<br>mid %{y:.3f}<extra></extra>",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=ts,
                y=[r[1] for r in rows],
                mode="lines",
                name="Model",
                line={"color": MODEL_COLOR},
                visible=visible,
                hovertemplate="%{x}<br>model %{y:.3f}<extra></extra>",
            )
        )
        meeting = rows[-1][5]
        decision = (
            datetime.datetime.fromisoformat(meeting + "T14:00")
            .replace(tzinfo=eastern)
            .astimezone(datetime.UTC)
            .isoformat()
            if meeting
            else ts[-1]
        )
        result = settled.get(ticker)
        if result:
            end = 1.0 if result == "yes" else 0.0
            line = {
                "x": [decision] * 3,
                "y": [0, 1, end],
                "mode": "lines+markers+text",
                "marker": {"symbol": "star", "size": [0, 0, 14], "color": "#009E73"},
                "text": ["", "", f"settled {result.upper()}"],
            }
        else:
            line = {"x": [decision] * 2, "y": [0, 1], "mode": "lines"}
        fig.add_trace(
            go.Scatter(
                **line,
                line={"color": NEUTRAL_COLOR, "dash": "dot"},
                textposition="middle left",
                name="FOMC decision",
                visible=visible,
                hovertemplate="FOMC decision %{x}<extra></extra>",
            )
        )
        vis = [False] * (per * len(tickers))
        vis[per * i : per * i + per] = [True] * per
        buttons.append({"label": ticker, "method": "update", "args": [{"visible": vis}]})
    _layout(fig, "Time (UTC)", "Probability", yaxis_range=[-0.02, 1.08])
    if buttons:
        fig.update_layout(
            updatemenus=[
                {
                    "buttons": buttons,
                    "x": 0,
                    "y": 1.18,
                    "xanchor": "left",
                    "direction": "down",
                    "showactive": True,
                }
            ],
            margin={"t": 56},
        )
    return fig, len(tickers)


def fig_pnl(r: Results) -> tuple[Any, str]:
    import plotly.graph_objects as go

    ts = [p[0] for p in r.pnl]
    before = [p[1] for p in r.pnl]
    after = [p[2] for p in r.pnl]
    dd, at = max_drawdown(after)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=ts,
            y=after,
            mode="lines",
            line_shape="hv",
            name="After fees",
            line={"color": MODEL_COLOR},
            hovertemplate="%{x}<br>$%{y:.2f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=ts,
            y=before,
            mode="lines",
            line_shape="hv",
            name="Before fees",
            line={"color": MARKET_COLOR, "dash": "dash"},
            visible=False,
            hovertemplate="%{x}<br>$%{y:.2f}<extra></extra>",
        )
    )
    if dd > 0:
        fig.add_annotation(
            x=ts[at],
            y=after[at],
            text=f"max drawdown ${dd:.2f}",
            showarrow=True,
            arrowhead=2,
            ay=40,
        )
    fig.update_layout(
        updatemenus=[
            {
                "type": "buttons",
                "direction": "right",
                "x": 0,
                "y": 1.18,
                "xanchor": "left",
                "buttons": [
                    {
                        "label": "After fees",
                        "method": "update",
                        "args": [{"visible": [True, False]}],
                    },
                    {
                        "label": "Before fees",
                        "method": "update",
                        "args": [{"visible": [False, True]}],
                    },
                ],
            }
        ]
    )
    _layout(fig, "Settlement date (UTC)", "Cumulative P&L (USD)", margin={"t": 56})
    takeaway = (
        f"Paper P&L after fees ${after[-1]:.2f} (before fees ${before[-1]:.2f}) "
        f"over {len(after)} settled fills; max drawdown ${dd:.2f}"
    )
    return fig, takeaway


def fig_edges(r: Results) -> tuple[Any, str]:
    import plotly.graph_objects as go

    xs = [e[0] for e in r.edges]
    ys = [e[1] for e in r.edges]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="markers",
            name="Fill",
            marker={"color": MODEL_COLOR, "opacity": 0.45, "symbol": "circle"},
            hovertemplate="edge %{x:.1f}c<br>realized %{y:.1f}c<extra></extra>",
        )
    )
    lo, hi = math.floor(min(xs)), math.ceil(max(xs))
    width = max(1, math.ceil((hi - lo) / 8))
    bx, by = [], []
    for start in range(lo, hi + 1, width):
        bucket = [y for x, y in zip(xs, ys, strict=True) if start <= x < start + width]
        if bucket:
            bx.append(start + width / 2)
            by.append(sum(bucket) / len(bucket))
    fig.add_trace(
        go.Scatter(
            x=bx,
            y=by,
            mode="lines+markers",
            name="Binned mean",
            line={"color": MARKET_COLOR, "dash": "dash"},
            marker={"symbol": "square"},
            hovertemplate="edge bin %{x:.1f}c<br>mean %{y:.1f}c<extra></extra>",
        )
    )
    _layout(fig, "Model edge after fees (cents)", "Realized P&L per contract (cents)")
    mean_edge = sum(xs) / len(xs)
    mean_real = sum(ys) / len(ys)
    takeaway = (
        f"Mean edge after fees {mean_edge:.1f}c per contract; "
        f"mean realized {mean_real:.1f}c over {len(xs)} fills"
    )
    return fig, takeaway


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kalshibot evaluation</title>
<style>
:root {{ --bg: #ffffff; --fg: #1a1a1a; --muted: #5a5a5a; --card: #f5f5f4; --warn: #8a4b00;
         --warn-bg: #fff3e0; --grid: #dddddd; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #121212; --fg: #ececec; --muted: #a8a8a8; --card: #1e1e1e; --warn: #ffcc80;
           --warn-bg: #3a2a10; --grid: #3a3a3a; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg);
        font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px 48px; }}
h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
h2 {{ font-size: 1.15rem; margin: 0 0 4px; }}
.sub {{ color: var(--muted); margin: 0 0 24px; }}
section {{ background: var(--card); border-radius: 8px; padding: 16px; margin: 0 0 20px; }}
.takeaway {{ margin: 0 0 8px; }}
.question {{ color: var(--muted); margin: 0 0 8px; font-size: 0.9rem; }}
.banner {{ background: var(--warn-bg); color: var(--warn); border-radius: 6px;
           padding: 6px 10px; margin: 0 0 10px; font-weight: 600; font-size: 0.9rem; }}
.empty {{ color: var(--muted); padding: 32px 0; text-align: center; }}
code {{ font-size: 0.9em; }}
</style>
</head>
<body>
<main>
<h1>kalshibot evaluation</h1>
<p class="sub">{subtitle}</p>
{sections}
<p class="sub">Generated {generated} by <code>{command}</code>. Data: Kalshi market prices
and fed funds futures prices. No account balances, order IDs or keys.</p>
</main>
<script>
(function () {{
  var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  var css = getComputedStyle(document.documentElement);
  var fg = css.getPropertyValue("--fg").trim(), grid = css.getPropertyValue("--grid").trim();
  document.querySelectorAll(".js-plotly-plot").forEach(function (el) {{
    Plotly.relayout(el, {{"font.color": fg, "xaxis.gridcolor": grid, "yaxis.gridcolor": grid,
      "xaxis.zerolinecolor": grid, "yaxis.zerolinecolor": grid}});
  }});
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}})();
</script>
</body>
</html>
"""


def _section(key: str, question: str, takeaway: str, body: str, synthetic: bool) -> str:
    banner = (
        '<p class="banner">SYNTHETIC DATA from scripts/make_sample.py. Not real results.</p>'
        if synthetic
        else ""
    )
    return (
        f'<section id="view-{key}"><h2>{VIEW_TITLES[key]}</h2>{banner}'
        f'<p class="question">{html.escape(question)}</p>'
        f'<p class="takeaway">{html.escape(takeaway)}</p>{body}</section>'
    )


def render_html(conn: sqlite3.Connection, r: Results, s: dict[str, Any], command: str) -> str:
    import plotly.io as pio

    first = {"done": False}

    def plot(fig: Any, div_id: str) -> str:
        include = not first["done"]
        first["done"] = True
        out: str = pio.to_html(
            fig,
            full_html=False,
            include_plotlyjs=include,
            div_id=div_id,
            config={"responsive": True, "displaylogo": False},
        )
        return out

    since = (r.first_cycle or "")[:10]
    empty = f'<p class="empty">No settled contracts yet, {r.cycles} cycles logged since {since}</p>'
    sections = []
    ts_fig, n_contracts = fig_timeseries(conn)
    if s["n"]:
        body_a = plot(fig_reliability(s), "reliability")
        take_a = brier_takeaway(s)
    else:
        body_a, take_a = empty, "No contracts scored yet"
    sections.append(
        _section(
            "a",
            "When the model says 30%, does it happen 30% of the time?",
            take_a,
            body_a,
            r.synthetic,
        )
    )
    sections.append(
        _section(
            "b",
            "Where did the model and market disagree, and who was right?",
            f"{n_contracts} contracts logged; pick one from the menu",
            plot(ts_fig, "timeseries") if n_contracts else empty,
            r.synthetic,
        )
    )
    if r.pnl:
        fig_c, take_c = fig_pnl(r)
        body_c = plot(fig_c, "pnl")
    else:
        body_c, take_c = empty, "No settled paper fills yet"
    sections.append(
        _section("c", "Does the strategy make money after fees?", take_c, body_c, r.synthetic)
    )
    if r.edges:
        fig_d, take_d = fig_edges(r)
        body_d = plot(fig_d, "edges")
    else:
        body_d, take_d = empty, "No settled paper fills yet"
    sections.append(_section("d", "Do bigger edges earn more?", take_d, body_d, r.synthetic))
    if s["n"]:
        subtitle = (
            f"{s['n']} settled Kalshi Fed-rate contracts, {s['first_settled']} to "
            f"{s['last_settled']}. Model log loss {s['model_log_loss']:.3f}, "
            f"market {s['market_log_loss']:.3f}."
        )
    else:
        subtitle = f"{r.cycles} cycles logged since {since}."
    if r.synthetic:
        subtitle = "SYNTHETIC SAMPLE. " + subtitle
    generated = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    page = PAGE.format(
        subtitle=html.escape(subtitle),
        sections="\n".join(sections),
        generated=generated,
        command=html.escape(command),
    )
    if not first["done"]:
        page = page.replace('document.querySelectorAll(".js-plotly-plot")', "[]")
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
        out.write_text(render_html(conn, r, s, command))
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
