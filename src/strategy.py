import datetime
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
            self.cache = self.cme.get_probabilities()
            self.last_update = now

    def parse_kalshi_ticker(self, ticker: str):
        # Logic to map 'KX-FEDRATE-DEC24-5.25' -> '2024-12-18', 525
        try:
            if "DEC24" in ticker:
                date_key = "2024-12-18"
            elif "NOV24" in ticker:
                date_key = "2024-11-07"
            else:
                return None, None

            threshold_bps = float(ticker.split('-')[-1]) * 100
            return date_key, threshold_bps
        except:
            return None, None

    def calculate_fair_value(self, ticker: str) -> float:
        self._update_data()
        target_date, threshold_bps = self.parse_kalshi_ticker(ticker)

        if not target_date or not threshold_bps: return 0.0
        cme_probs = self.cache.get(target_date)
        if not cme_probs: return 0.0

        # Sum probs where rate > threshold
        fair_prob = 0.0
        for bucket in cme_probs:
            if bucket['min'] >= threshold_bps:
                fair_prob += bucket['prob']
        return fair_prob