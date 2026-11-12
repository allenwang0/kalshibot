from pathlib import Path

from src.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def test_env_example_loads_as_is_and_stays_in_paper_mode() -> None:
    """The README says to copy .env.example to .env first; that copy must start."""
    s = Settings(_env_file=ROOT / ".env.example")  # type: ignore[call-arg]
    assert s.TRADING_MODE == "PAPER"
    assert s.API_KEY_ID in (None, "")
