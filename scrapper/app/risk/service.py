from __future__ import annotations

from typing import Dict

from app.config.schema import AppConfig


class RiskService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def lot_size(self, balance: float) -> float:
        sizing = self.config.risk_parameters.position_sizing
        if sizing.method == "fixed_lot" or not sizing.auto_calculate_from_risk:
            return float(sizing.fixed_lot_size)

        risk_pct = float(self.config.risk_parameters.risk_per_trade_percent) / 100.0
        if balance <= 0:
            return float(sizing.fixed_lot_size)

        # Conservative placeholder: scale toward fixed-lot cap.
        proposed = (balance * risk_pct) / 10000.0
        return max(0.01, min(float(sizing.fixed_lot_size), proposed))

    def risk_snapshot(self, balance: float, equity: float) -> Dict[str, float]:
        drawdown = 0.0
        if balance > 0:
            drawdown = max(0.0, (balance - equity) / balance * 100.0)
        return {
            "balance": float(balance),
            "equity": float(equity),
            "drawdown_percent": float(drawdown),
            "risk_per_trade_percent": float(self.config.risk_parameters.risk_per_trade_percent),
        }
