from src.config import settings


class RiskManager:
    @staticmethod
    def calculate_kelly_size(bankroll: float, win_prob: float, market_price: float) -> float:
        if win_prob <= market_price: return 0.0

        # Kelly: f = (p - q) / (1 - q) = (edge) / (loss_ratio)
        edge = win_prob - market_price
        loss_ratio = 1.0 - market_price
        if loss_ratio <= 0: return 0.0

        raw_kelly = edge / loss_ratio
        allocation = bankroll * raw_kelly * settings.KELLY_FRACTION
        return max(0.0, min(allocation, settings.MAX_POSITION_SIZE))