import datetime
from zoneinfo import ZoneInfo
from src.data.cme import CMEFedWatch


class AlphaModel:
    def __init__(self):
        self.cme = CMEFedWatch()
        self.cache = {}
        self.last_update = 0
        self.cache_duration = 300

    def _update_data(self):
        now = datetime.datetime.now().timestamp()
        if now - self.last_update > self.cache_duration:
            print("[STRATEGY] Refreshing CME FedWatch data...")
            data = self.cme.get_probabilities()
            if data:
                self.cache = data
                self.last_update = now
            else:
                # Leave last_update alone so the next call retries; no model
                # prices (and no trades) until the data loads.
                print("[STRATEGY] CME data unavailable; skipping model prices")
                self.cache = {}

    def parse_market(self, market: dict):
        """Map a market from GET /markets to (FOMC meeting date, threshold in bps).

        Uses strike_type, floor_strike and close_time from the market object.
        https://docs.kalshi.com/api-reference/market/get-markets
        A market settles on the FOMC meeting held in its close month; there is at
        most one meeting per month, and the meeting dates come from the CME data.
        close_time is UTC; FOMC dates are Eastern, so compare in Eastern time.
        """
        if market.get('strike_type') != 'greater':
            return None, None
        try:
            threshold_bps = float(market['floor_strike']) * 100
            close = (datetime.datetime.fromisoformat(market['close_time'])
                     .astimezone(ZoneInfo('America/New_York')).date())
        except (KeyError, TypeError, ValueError):
            return None, None
        month = close.isoformat()[:7]
        for date_key in sorted(self.cache):
            if date_key.startswith(month) and date_key <= close.isoformat():
                return date_key, threshold_bps
        return None, None

    def calculate_fair_value(self, market: dict):
        """Model probability that the market settles YES, or None if unknown."""
        self._update_data()
        target_date, threshold_bps = self.parse_market(market)

        if target_date is None: return None
        cme_probs = self.cache.get(target_date)
        if not cme_probs: return None

        # Sum probs where rate > threshold
        fair_prob = 0.0
        for bucket in cme_probs:
            if bucket['min'] >= threshold_bps:
                fair_prob += bucket['prob']
        return fair_prob
