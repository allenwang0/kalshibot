"""evaluate.py on a fixture database with fabricated, hand-checkable values."""

import json
import sys
from pathlib import Path

import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import evaluate  # noqa: E402

from src.ledger import Ledger  # noqa: E402

FIXTURE_SCHEMA = Path(__file__).parent / "fixtures" / "card.schema.json"
# ticker, model p, market mid, outcome
CONTRACTS = [
    ("A", 0.8, 0.6, "yes"),
    ("B", 0.3, 0.5, "no"),
    ("C", 0.6, 0.7, "no"),
    ("D", 0.1, 0.2, "no"),
]


def _db(tmp_path, synthetic=False):
    path = tmp_path / "log.db"
    ledger = Ledger(str(path))
    if synthetic:
        ledger.set_meta("synthetic", "1")
    for ticker, p, mid, result in CONTRACTS:
        # The scored row is 8 days before settlement; the later row is inside
        # the 7-day horizon and must be ignored.
        ledger.log_cycle(
            ts="2026-10-20T12:00:00+00:00",
            mode="PAPER",
            ticker=ticker,
            strike=4.0,
            meeting_date="2026-10-28",
            model_p=p,
            best_bid=40,
            best_ask=44,
            mid=mid,
            action="hold",
        )
        ledger.log_cycle(
            ts="2026-10-28T12:00:00+00:00",
            mode="PAPER",
            ticker=ticker,
            strike=4.0,
            meeting_date="2026-10-28",
            model_p=0.99,
            best_bid=97,
            best_ask=99,
            mid=0.98,
            action="hold",
        )
        ledger.set_settlement(ticker, result, "2026-10-28T18:20:00+00:00")
    ledger.add_fill("A", "yes", 10, 60, 17, 0.8, ts="2026-10-20T12:00:00+00:00")
    ledger.add_fill("C", "yes", 10, 50, 18, 0.6, ts="2026-10-20T12:00:00+00:00")
    return path


def _expected_brier(i):
    return sum((c[i] - (c[3] == "yes")) ** 2 for c in CONTRACTS) / len(CONTRACTS)


def test_metrics_score_the_horizon_row_not_the_last_minute(tmp_path):
    conn = evaluate.load(_db(tmp_path))
    s = evaluate.summary(evaluate.compute(conn))
    assert s["n"] == 4
    assert s["model_brier"] == pytest.approx(_expected_brier(1))  # 0.125
    assert s["market_brier"] == pytest.approx(_expected_brier(2))  # 0.235
    # A pays 10 * (100 - 60) - 17 = 383c, C loses 10 * 50 + 18 = 518c.
    assert s["pnl_after_fees"] == pytest.approx(-1.35)
    assert s["pnl_before_fees"] == pytest.approx(-1.00)


def test_html_has_every_figure_and_the_scores(tmp_path):
    out = tmp_path / "report.html"
    assert evaluate.main(["--db", str(_db(tmp_path)), "--html", str(out)]) == 0
    page = out.read_text()
    for fig in evaluate.FIGURES:
        assert f'id="c-{fig}"' in page and f'id="t-{fig}"' in page  # chart and table twin
    assert f"{_expected_brier(1):.3f}" in page
    assert f"vs {_expected_brier(2):.3f} for the market mid" in page
    assert "Synthetic sample" not in page
    # Self-contained: no Plotly and no external scripts, so the page stays small.
    assert "plotly" not in page.lower()
    assert 'src="http' not in page


def test_synthetic_label_sits_in_the_hero_and_next_to_the_pnl(tmp_path):
    db = _db(tmp_path, synthetic=True)
    out = tmp_path / "report.html"
    evaluate.main(["--db", str(db), "--html", str(out)])
    page = out.read_text()
    # A reader who only sees the hero, or only the P&L, still learns the data is fake.
    hero = page[page.index('class="hero') : page.index('aria-label="Key results"')]
    assert '<span class="key">Synthetic sample' in hero
    pnl = page[page.index('id="pnl"') : page.index('id="edges"')]
    assert "Synthetic sample" in pnl
    assert "on the synthetic sample" in pnl
    with pytest.raises(SystemExit) as e:
        evaluate.main(["--db", str(db), "--card", str(tmp_path / "card.json")])
    assert e.value.code != 0


def test_reliability_bins_carry_counts_and_a_wilson_interval():
    rows = evaluate.reliability([0.1, 0.15, 0.9, 0.95], [0, 1, 1, 1])
    assert [r["count"] for r in rows] == [2, 2]
    low, high = rows
    # 1 of 2 YES: the interval must be wide enough to include both 0.1 and 0.9.
    assert low["ci_lo"] < 0.1 and low["ci_hi"] > 0.9
    assert high["freq"] == 1 and high["ci_hi"] == 1 and high["ci_lo"] < 0.5


def test_drawdown_treats_simultaneous_settlements_as_one_step(tmp_path):
    # Both fixture fills settle at the same instant: A wins 3.83, C loses 5.18.
    # Per-fill ordering would invent a 5.18 drop from a 3.83 peak; per settlement
    # the log just goes 0 -> -1.35.
    conn = evaluate.load(_db(tmp_path))
    r = evaluate.compute(conn)
    d = evaluate.report_data(conn, r, evaluate.summary(r))
    assert d["pnl"]["settlements"] == 1
    assert d["pnl"]["drawdown"] == pytest.approx(1.35)


def test_card_validates_and_matches_the_report(tmp_path):
    db = _db(tmp_path)
    card_path = tmp_path / "card.json"
    evaluate.main(["--db", str(db), "--card", str(card_path)])
    card = json.loads(card_path.read_text())
    jsonschema.validate(card, json.loads(FIXTURE_SCHEMA.read_text()))
    assert card["metric"]["value"] == round(_expected_brier(1), 4)
    assert len(card["takeaway"]) <= 140


def test_empty_log_shows_the_waiting_state(tmp_path):
    path = tmp_path / "log.db"
    Ledger(str(path)).log_cycle(ts="2026-11-01T00:00:00+00:00", ticker="A")
    out = tmp_path / "report.html"
    evaluate.main(["--db", str(path), "--html", str(out)])
    assert "No settled contracts yet, 1 cycles logged since 2026-11-01" in out.read_text()
    with pytest.raises(SystemExit):
        evaluate.main(["--db", str(path), "--card", str(tmp_path / "card.json")])


def test_missing_database_exits_non_zero(tmp_path):
    with pytest.raises(SystemExit) as e:
        evaluate.main(["--db", str(tmp_path / "nope.db"), "--html", str(tmp_path / "x.html")])
    assert e.value.code != 0
    assert not (tmp_path / "x.html").exists()
