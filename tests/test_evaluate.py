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


def test_html_has_all_four_views_and_the_brier_numbers(tmp_path):
    out = tmp_path / "report.html"
    assert evaluate.main(["--db", str(_db(tmp_path)), "--html", str(out)]) == 0
    page = out.read_text()
    for title in evaluate.VIEW_TITLES.values():
        assert title in page
    assert f"Model Brier {_expected_brier(1):.3f} vs market {_expected_brier(2):.3f}" in page
    assert "SYNTHETIC" not in page


def test_synthetic_data_is_bannered_and_refused_for_the_card(tmp_path):
    db = _db(tmp_path, synthetic=True)
    out = tmp_path / "report.html"
    evaluate.main(["--db", str(db), "--html", str(out)])
    assert out.read_text().count("SYNTHETIC DATA") == 4
    with pytest.raises(SystemExit) as e:
        evaluate.main(["--db", str(db), "--card", str(tmp_path / "card.json")])
    assert e.value.code != 0


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
