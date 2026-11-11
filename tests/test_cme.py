import src.data.cme as cme

# Fabricated values in the shape the existing parser expects.
MEETINGS = [{"year": 2024, "month": 11, "day": 7,
             "probabilityList": [{"min": 450, "max": 475, "prob": 70.0},
                                 {"min": 475, "max": 500, "prob": 30.0}]}]


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_single_digit_day_gives_iso_key(monkeypatch):
    # The strategy looks meetings up by ISO date. "2024-11-7" never matched
    # "2024-11-07", so fair value silently fell to zero for that meeting.
    monkeypatch.setattr(cme.requests, "get", lambda *a, **k: _Resp(MEETINGS))
    probs = cme.CMEFedWatch().get_probabilities()
    assert list(probs) == ["2024-11-07"]


def test_cme_request_does_not_spoof_a_browser(monkeypatch):
    # Faking a random browser User-Agent, Referer and Origin to get past CME's
    # site is a terms-of-service problem, so requests identify as themselves.
    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen.update(headers or {})
        return _Resp(MEETINGS)

    monkeypatch.setattr(cme.requests, "get", fake_get)
    cme.CMEFedWatch().get_probabilities()
    assert not {"User-Agent", "Referer", "Origin"} & set(seen)
