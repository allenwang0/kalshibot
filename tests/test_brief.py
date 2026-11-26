"""The project brief stays honest: no edge claimed, fabricated inputs labelled,
and every number recomputed from the committed snapshot and the sources.

Readers of a trading-bot page look for a performance number. Until real
contracts settle there is none, so these tests fail if a hero metric appears,
if the "not a result" labels go missing, or if the fee and sizing numbers drift
from src/ (a stale page would show a rule the bot no longer follows).
"""

import html
import json
import math
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAP = json.loads((ROOT / "results" / "replay_cycle.json").read_text())


def build(tmp_path: Path) -> str:
    out = tmp_path / "index.html"
    subprocess.run(
        [sys.executable, "scripts/build_brief.py", "--out", str(out)], cwd=ROOT, check=True
    )
    return out.read_text()


def text_of(page: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", page))


def test_no_hero_metric_and_no_edge_claimed(tmp_path: Path) -> None:
    page = build(tmp_path)
    assert 'class="hero-metric"' not in page, "no settled contracts: the hero carries no number"
    hero = text_of(page.split('<section class="section')[0])
    assert "nothing here shows an edge" in hero
    assert "nothing here shows an edge" in (ROOT / "README.md").read_text()


def test_fabricated_inputs_are_labelled_on_the_figure_and_readme_image(tmp_path: Path) -> None:
    assert "fabricated" in SNAP["inputs"]["fixture_note"]
    assert "SYNTHETIC" in SNAP["inputs"]["prices_note"]
    page = build(tmp_path)
    assert page.count("Replay fixture: fabricated prices, not a result") == 3  # one per breakpoint
    assert "not a result" in (ROOT / "docs" / "replay.svg").read_text()
    alt = re.search(r"!\[([^\]]+)\]\(docs/replay.svg\)", (ROOT / "README.md").read_text())
    assert alt and "synthetic" in alt.group(1)


def test_fee_example_follows_the_kalshi_formula() -> None:
    # The page quotes one paper fill's fee; it must be ceil(0.07 * C * P * (1 - P)) cents.
    rate = float(re.search(r"FEE_RATE: float = ([\d.]+)", (ROOT / "src/config.py").read_text())[1])
    for fill in SNAP["fills"]:
        p = fill["price_cents"] / 100
        expected = math.ceil(round(rate * fill["count"] * p * (1 - p) * 100, 6))
        assert fill["fee_cents"] == expected, fill


def test_sizing_numbers_match_config(tmp_path: Path) -> None:
    cfg = (ROOT / "src/config.py").read_text()
    kelly = float(re.search(r"KELLY_FRACTION: float = ([\d.]+)", cfg)[1])
    per_market = float(re.search(r"MAX_POSITION_SIZE: float = ([\d.]+)", cfg)[1])
    total = float(re.search(r"MAX_TOTAL_EXPOSURE: float = ([\d.]+)", cfg)[1])
    text = text_of(build(tmp_path))
    assert f"bankroll × {kelly:g} × (p' − q) / (1 − q)" in text
    assert f"${per_market:,.0f} per market, ${total:,.0f} across markets" in text


def test_snapshot_is_what_the_bot_logs_now(tmp_path: Path) -> None:
    out = tmp_path / "snap.json"
    subprocess.run(
        [sys.executable, "scripts/replay_snapshot.py", "--out", str(out)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    assert json.loads(out.read_text()) == SNAP, "rerun scripts/replay_snapshot.py and commit"


def test_committed_brief_and_readme_are_current(tmp_path: Path) -> None:
    assert build(tmp_path) == (ROOT / "docs" / "index.html").read_text(), (
        "docs/index.html is stale: run python3 scripts/build_brief.py --readme README.md"
    )
    assert "allenwang0.github.io/kalshibot" in (ROOT / "README.md").read_text()
