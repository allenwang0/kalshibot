"""Build docs/index.html (the project brief), docs/replay.svg (the README image)
and the README Highlights block from results/replay_cycle.json and the sources.

    python3 scripts/build_brief.py [--out docs/index.html] [--readme README.md]

Standard library only. Every number is computed here from the committed
snapshot or read from constants in src/; none is typed by hand. The snapshot's
inputs are fabricated (replay fixture prices, synthetic futures CSV), so the page
shows the bot's arithmetic, never a result. Components come from report-kit/.
"""

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "report-kit"))
import report_kit as rk  # noqa: E402
from report_kit import Raw, esc  # noqa: E402

REPO = "allenwang0/kalshibot"
REPO_URL = f"https://github.com/{REPO}"
PAGE_URL = "https://allenwang0.github.io/kalshibot/"
HOME_URL = "https://allenwang0.github.io/"
README_START = "<!-- results:start -->"
README_END = "<!-- results:end -->"
WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
}
FIG_LABEL = "Replay fixture: fabricated prices, not a result"
# Light-theme values of the kit tokens, for the standalone README SVG (no page CSS there).
SVG_STYLE = (
    "<style>.c-grid{stroke:rgba(0,0,0,.07);fill:none}.c-axis{stroke:#6E6E73;fill:none}"
    ".c-muted{fill:#A1A1A6}.c-accent{fill:#0066CC}.c-band{fill:#F2F2F5}"
    "text{font:500 13px -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
    ".c-label{fill:#515154}.c-value{fill:#19191B;font-weight:600}.c-tick,.c-title{fill:#6E6E73;font-size:12px}"
    ".c-hi{fill:#0066CC;font-weight:600}</style><rect width='100%' height='100%' fill='#FFFFFF'/>"
)


def source(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def line_range(rel, start, end=None):
    lines = source(rel).splitlines()
    first = next(i for i, line in enumerate(lines, 1) if start in line)
    if end is None:
        return first, first
    return first, next(i for i, line in enumerate(lines, 1) if i >= first and end in line)


def horizon_days():
    return float(
        re.search(r"horizon_days: float = ([\d.]+)", source("scripts/evaluate.py")).group(1)
    )


def git_last_commit(rel):
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", rel],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return out or None


def row_label(c):
    day = datetime.date.fromisoformat(c["meeting_date"])
    return f"{day:%b} {day.day} · above {c['strike']:.2f}%"


def replay_chart(snap, uid, width):
    """Per strike: market bid-ask band (muted), model probability (accent dot)."""
    phone = width < 420
    rows = snap["cycles"]
    label_w = 0 if phone else max(rk.text_width(row_label(c)) for c in rows) + 18
    value_w = 48  # model values sit in their own column right of the plot
    plot_l, plot_r = label_w + 16, width - value_w - 16
    x = lambda p: plot_l + (plot_r - plot_l) * p
    top, row_h = 62, (52 if phone else 40)
    bottom = top + row_h * len(rows)
    parts = []
    for t in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<path class="c-grid" d="M{rk.coord(x(t))} {top - 8}V{rk.coord(bottom)}"/>')
        parts.append(rk.svg_text(x(t), bottom + 18, f"{t:.0%}", "c-tick", "middle"))
    parts.append(rk.svg_text(0, 16, FIG_LABEL, "c-title"))
    parts.append('<rect x="0" y="30" width="18" height="10" rx="2" class="c-muted"/>')
    parts.append(rk.svg_text(24, 39, "market bid–ask", "c-title"))
    kx = 24 + rk.text_width("market bid–ask", 12) + 18
    parts.append(f'<circle cx="{rk.coord(kx)}" cy="35" r="5" class="c-accent"/>')
    parts.append(rk.svg_text(kx + 10, 39, "model", "c-title"))
    for i, c in enumerate(rows):
        cy = top + i * row_h + row_h / 2 + (8 if phone else 0)
        if phone:
            parts.append(rk.svg_text(0, cy - 14, row_label(c), "c-label"))
        else:
            parts.append(rk.svg_text(label_w, cy + 4, row_label(c), "c-label", "end"))
        bid, ask, p = c["best_bid"] / 100, c["best_ask"] / 100, c["model_p"]
        parts.append(
            f'<rect x="{rk.coord(x(bid))}" y="{rk.coord(cy - 5)}" width="{rk.coord(max(x(ask) - x(bid), 2))}" '
            f'height="10" rx="2" class="c-muted"/>'
        )
        parts.append(
            f'<circle cx="{rk.coord(x(p))}" cy="{rk.coord(cy)}" r="5.5" class="c-accent"/>'
        )
        parts.append(rk.svg_text(width, cy + 4, f"{p:.2f}", "c-value", "end"))
    parts.append(rk.svg_text(width, top - 14, "model", "c-title", "end"))
    parts.append(
        rk.svg_text(plot_r, bottom + 38, "Probability the contract settles YES", "c-title", "end")
    )
    desc = (
        "; ".join(
            f"{row_label(c)}: market {c['best_bid']}–{c['best_ask']} cents, model {c['model_p']:.2f}"
            for c in rows
        )
        + ". Fabricated replay prices and synthetic futures; not market data."
    )
    return rk.svg(
        uid,
        width,
        bottom + 46,
        "Model probability against the market's bid-ask range per strike",
        desc,
        "".join(parts),
    )


def build_page(snap, shas):
    s = snap["settings"]
    rows = snap["cycles"]
    fills = {f["ticker"]: f for f in snap["fills"]}
    example = max(snap["fills"], key=lambda f: f["fee_cents"])
    horizon = horizon_days()
    outside = sum(1 for c in rows if not c["best_bid"] / 100 <= c["model_p"] <= c["best_ask"] / 100)
    R = rk.ref

    def blob(rel, a=None, b=None):
        anchor = f"#L{a}" + (f"-L{b}" if b and b != a else "") if a else ""
        return f"{REPO_URL}/blob/{shas.get(rel) or 'main'}/{rel}{anchor}"

    fee = line_range("src/risk.py", "# Kalshi general trading fee", "return math.ceil")
    cfg = line_range("src/config.py", "KELLY_FRACTION", "MAX_TOTAL_EXPOSURE")
    kelly = line_range("src/risk.py", "def calculate_kelly_size", "return max(0.0")
    score = line_range("scripts/evaluate.py", "def compute(", "chosen[ticker] = (model_p, mid)")
    csv_line = line_range("src/models/fedfunds.py", "def load_prices")
    evidence = [
        (
            "E1",
            f"Model vs market on {len(rows)} strikes; {len(fills)} paper buys",
            "results/replay_cycle.json",
            blob("results/replay_cycle.json"),
            "Verified",
        ),
        (
            "E2",
            f"Taker fee = ceil({s['fee_rate']:g}·C·P·(1−P)) cents",
            f"src/risk.py:{fee[0]}–{fee[1]}",
            blob("src/risk.py", *fee),
            "Verified",
        ),
        (
            "E3",
            f"Kelly {s['kelly_fraction']:g}, shrinkage {s['shrinkage']:g}, ${s['max_position']:,.0f} per market, "
            f"${s['max_exposure']:,.0f} total",
            f"src/config.py:{cfg[0]}–{cfg[1]}; src/risk.py:{kelly[0]}–{kelly[1]}",
            blob("src/config.py", *cfg),
            "Verified",
        ),
        (
            "E4",
            f"Scored on the last price at least {rk.num(f'{horizon:g}', 'days')} before settlement",
            f"scripts/evaluate.py:{score[0]}–{score[1]}",
            blob("scripts/evaluate.py", *score),
            "Verified",
        ),
        (
            "E5",
            "Futures prices are read from a hand-maintained CSV",
            f"src/models/fedfunds.py:{csv_line[0]}",
            blob("src/models/fedfunds.py", *csv_line),
            "Verified",
        ),
    ]

    takeaway = (
        f"On fabricated prices, the model falls outside the market's bid–ask range on all {WORDS[outside]} strikes"
        if outside == len(rows)
        else f"On fabricated prices, the model falls outside the market's bid–ask range on {outside} of {len(rows)} strikes"
    )
    replay_table = rk.data_table(
        "Replay cycle per strike",
        ["Strike", "Best bid (¢)", "Best ask (¢)", "Model p", "Action"],
        [
            [
                row_label(c),
                str(c["best_bid"]),
                str(c["best_ask"]),
                f"{c['model_p']:.3f}",
                c["action"],
            ]
            for c in rows
        ],
        numeric={1, 2, 3},
    )

    hero = rk.hero(
        eyebrow="Trading bot · Python · Paper mode",
        title="kalshibot",
        subtitle="Prices Kalshi's Fed-rate contracts from Fed Funds futures, sizes bets after Kalshi's fee, and "
        "scores its model against the market once contracts settle.",
        context="No real contract has settled in its log yet, so nothing here shows an edge. The replay chart "
        "uses fabricated prices to show what the bot computes.",
        actions=[("Source", REPO_URL, True)],
        figure=rk.join(
            [
                rk.tag(
                    "p",
                    "One paper cycle on the replay fixture, per strike",
                    class_="card-label",
                ),
                rk.variants(
                    replay_chart(snap, "rp-d", 398),
                    replay_chart(snap, "rp-t", 640),
                    replay_chart(snap, "rp-p", 280),
                ),
            ]
        ),
    )
    body = rk.join(
        [
            Raw('<div class="prose">'),
            rk.tag(
                "p",
                Raw(
                    f"<b>{esc(takeaway)}.</b> The replay chart is one offline paper cycle: "
                    f"<code>main.py --once --replay</code> on the replay fixture (recorded API response shapes, "
                    f"fabricated prices) and the synthetic example futures CSV, current upper bound "
                    f"{snap['inputs']['current_target_upper_bps'] / 100:.2f}%. It shows the bot's arithmetic on one "
                    f"snapshot; the gaps are artifacts of made-up prices, not an edge.{R('E1')} "
                    f'<a class="src" href="{esc(blob("results/replay_cycle.json"))}">Data: replay_cycle.json</a>'
                ),
            ),
            Raw("</div>"),
            replay_table,
            Raw('<div class="prose" style="margin-top:24px">'),
            rk.tag(
                "p",
                Raw(
                    "Each KXFED contract asks whether the upper bound of the Fed's target range will be above a strike after "
                    "a given meeting. Each 30-day Fed Funds future implies an average rate for its month; in a meeting month "
                    "the rate after the meeting can be solved from that average, and each meeting's expected move is split "
                    f"between the two nearest {rk.num(25, 'bp')} outcomes and chained forward. A contract's model price is the probability "
                    "of every outcome above its strike."
                ),
            ),
            rk.tag(
                "p",
                Raw(
                    f"Sizing shrinks the model halfway to the market, prices in the fee, and bets a fraction of Kelly:"
                    f"{R('E3')}"
                ),
            ),
            Raw("</div>"),
            rk.code(
                f"p'   = ask + {s['shrinkage']:g} × (model − ask)\n"
                f"q    = ask + {s['fee_rate']:g} × ask × (1 − ask)          # fee per contract\n"
                f"bet  = bankroll × {s['kelly_fraction']:g} × (p' − q) / (1 − q)   # 0 if p' ≤ q\n"
                f"cap  = ${s['max_position']:,.0f} per market, ${s['max_exposure']:,.0f} across markets\n"
                f"fee  = ceil({s['fee_rate']:g} × C × P × (1 − P)) cents      # taker fill of C contracts at P",
                "Sizing rule",
            ),
            Raw('<div class="prose" style="margin-top:24px">'),
            rk.tag(
                "p",
                Raw(
                    f"In the replay, a {example['count']}-contract buy at {example['price_cents']}¢ pays a "
                    f"{example['fee_cents']}¢ fee — {example['fee_cents'] / (example['count'] * example['price_cents']):.1%} of "
                    f"the stake, which is why the fee enters the price before Kelly sees it.{R('E2')}"
                ),
            ),
            rk.tag(
                "p",
                Raw(
                    f"<b>What would count as evidence.</b> Settled KXFED contracts, each scored on the bot's last price at "
                    f"least {rk.num(f'{horizon:g}', 'days')} before settlement, with Brier score and log loss for the model against the "
                    f"market's mid. <code>scripts/evaluate.py</code> computes exactly that; the log has no settlements "
                    f"yet.{R('E4')}"
                ),
            ),
            rk.tag(
                "p",
                Raw(
                    f"<b>Limits.</b> Futures prices are typed by hand into <code>data/fed_funds_futures.csv</code>; nothing "
                    f"in the code checks how old they are, so a stale file prices every contract from old data.{R('E5')} "
                    f"Futures-implied probabilities include a term premium. Paper mode is the default; live trading needs "
                    f"<code>TRADING_MODE=LIVE</code> and API keys. Not financial advice."
                ),
            ),
            Raw("</div>"),
        ]
    )
    main_sec = rk.section(
        "how",
        "Futures imply a probability; the fee decides whether to bet",
        body,
        eyebrow="How it works",
    )
    repro = rk.join(
        [
            rk.code(
                "uv sync\nuv run pytest -q                                  # offline, from recorded responses\n"
                "uv run python scripts/replay_snapshot.py             # results/replay_cycle.json\n"
                "python3 scripts/build_brief.py --readme README.md    # this page",
                "Reproduce",
            ),
            Raw('<div style="margin-top:40px"></div>'),
            rk.table(
                "Evidence",
                ["ID", "Claim", "Source", "Class"],
                [
                    [
                        Raw(f'<span id="evidence-{e.lower()}">{e}</span>'),
                        claim,
                        Raw(f'<a class="src" href="{esc(h)}">{esc(src)}</a>'),
                        cls,
                    ]
                    for e, claim, src, h, cls in evidence
                ],
                wrap_cols={1, 2},
            ),
        ]
    )
    repro_sec = rk.section("evidence", "Reproduce and trace every number", repro, eyebrow="Sources")
    sha = shas.get("results/replay_cycle.json") or ""
    footer = rk.footer(
        Raw(
            f"Generated by <code>scripts/build_brief.py</code> from <code>results/replay_cycle.json</code> · "
            f"results {esc(sha[:7])}"
        ),
        Raw(f'<a href="{HOME_URL}">Allen Wang</a> · <a href="{REPO_URL}">Source</a>'),
    )
    return rk.page(
        title="kalshibot · Allen Wang",
        description="A paper-trading bot that prices Kalshi Fed-rate contracts from Fed Funds futures and sizes bets "
        "after fees. No settled contracts yet, so no edge is claimed.",
        canonical=PAGE_URL,
        header=rk.report_header(HOME_URL, "← Allen Wang", [("Source", REPO_URL)]),
        main=rk.join([hero, main_sec, repro_sec]),
        footer=footer,
    )


def readme_svg(snap):
    svg = str(replay_chart(snap, "readme", 760))
    head, rest = svg.split(">", 1)
    return head + ">" + SVG_STYLE + rest + "\n"


def readme_block(snap):
    s = snap["settings"]
    return "\n".join(
        [
            README_START,
            "- **Meeting-by-meeting rate model** that solves each Fed meeting's expected move from monthly futures "
            "averages.",
            f"- **Fee-aware sizing:** shrinks the model {s['shrinkage']:.0%} toward the market, prices in Kalshi's fee "
            f"(ceil({s['fee_rate']:g}·C·P·(1−P)) cents), then bets {s['kelly_fraction']:g} Kelly, capped at "
            f"${s['max_position']:,.0f} per market and ${s['max_exposure']:,.0f} in total.",
            f"- **Honest scoring:** Brier score and log loss for model versus market, on prices at least "
            f"{horizon_days():g} days before settlement.",
            "- **Runs offline:** recorded API response shapes replay a full paper cycle with no keys or network.",
            README_END,
        ]
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default="docs/index.html")
    parser.add_argument("--readme")
    args = parser.parse_args()
    snap = json.loads((ROOT / "results" / "replay_cycle.json").read_text(encoding="utf-8"))
    cited = [
        "results/replay_cycle.json",
        "src/risk.py",
        "src/config.py",
        "scripts/evaluate.py",
        "src/models/fedfunds.py",
    ]
    shas = {rel: git_last_commit(rel) for rel in cited}
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_page(snap, shas), encoding="utf-8")
    if args.readme:
        (ROOT / "docs" / "replay.svg").write_text(readme_svg(snap), encoding="utf-8")
        path = ROOT / args.readme
        text = path.read_text(encoding="utf-8")
        head, rest = text.split(README_START, 1)
        _, tail = rest.split(README_END, 1)
        path.write_text(head + readme_block(snap) + tail, encoding="utf-8")


if __name__ == "__main__":
    main()
