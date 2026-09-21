"""Circuit breakers for emergency risk protection."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Any, Dict, List

from loguru import logger


class CircuitBreakers:
    """Emergency controls that can globally or per-symbol halt trading."""

    def __init__(self, config: Dict[str, Any] | None = None):
        cfg = config or {}
        risk_cfg = cfg.get("risk", cfg)

        self.max_daily_loss = float(risk_cfg.get("max_daily_loss_pct", 3.0))
        self.max_weekly_loss = float(risk_cfg.get("max_weekly_loss_pct", 6.0))
        self.max_consecutive_losses = int(risk_cfg.get("max_consecutive_losses", 5))
        self.max_drawdown = float(risk_cfg.get("max_drawdown_pct", 8.0))
        self.max_spread_multiplier = float(risk_cfg.get("max_spread_multiplier", 3.0))
        self.max_volatility_spike = float(risk_cfg.get("max_volatility_spike", 2.0))

        self.loss_streak = 0
        self.trades_window = deque(maxlen=10)
        self.breach_history: List[Dict[str, Any]] = []

        self.global_circuit = False
        self.global_expiry: datetime | None = None
        self.symbol_circuits: Dict[str, datetime] = {}

        logger.info("CircuitBreakers initialized")

    def check_trade(self, trade_data: Dict[str, Any]):
        """Return approval boolean and reason for a candidate trade."""
        now = _utcnow_naive()
        symbol = str(trade_data.get("symbol", "")).upper()
        spread = float(trade_data.get("spread_pips", 0.0) or 0.0)
        avg_spread = max(1e-9, float(trade_data.get("avg_spread_pips", 1.0) or 1.0))
        atr = float(trade_data.get("atr_pips", 0.0) or 0.0)
        avg_atr = max(1e-9, float(trade_data.get("avg_atr", atr if atr > 0 else 1.0)))

        if self.global_circuit:
            if self.global_expiry and now >= self.global_expiry:
                self.global_circuit = False
                self.global_expiry = None
            else:
                return False, "Global circuit active"

        expiry = self.symbol_circuits.get(symbol)
        if expiry and now < expiry:
            return False, f"Symbol circuit active for {symbol}"
        if expiry and now >= expiry:
            self.symbol_circuits.pop(symbol, None)

        if spread > avg_spread * self.max_spread_multiplier:
            self.trigger_symbol_circuit(symbol, f"Spread too high: {spread:.2f}")
            return False, f"Spread too high: {spread:.2f}"

        if atr > avg_atr * self.max_volatility_spike:
            self.trigger_symbol_circuit(symbol, f"Volatility spike: ATR {atr:.2f}")
            return False, "Volatility spike"

        return True, "OK"

    def record_trade_outcome(self, outcome: str, symbol: str | None = None) -> None:
        normalized = str(outcome or "").upper()
        self.trades_window.append(normalized)

        if normalized == "LOSS":
            self.loss_streak += 1
        else:
            self.loss_streak = 0

        if self.loss_streak >= self.max_consecutive_losses:
            self.trigger_global_circuit(f"{self.loss_streak} consecutive losses")

        if len(self.trades_window) == self.trades_window.maxlen:
            wins = sum(1 for x in self.trades_window if x == "WIN")
            if wins <= 2:
                self.trigger_global_circuit(f"Low win rate in last {self.trades_window.maxlen} trades")

        if symbol and normalized == "LOSS" and self.loss_streak >= 3:
            self.trigger_symbol_circuit(str(symbol).upper(), "Loss streak on symbol")

    def trigger_global_circuit(self, reason: str) -> None:
        self.global_circuit = True
        self.global_expiry = _utcnow_naive() + timedelta(hours=1)
        breach = {
            "timestamp": _utcnow_naive(),
            "type": "GLOBAL",
            "reason": reason,
            "expiry": self.global_expiry,
        }
        self.breach_history.append(breach)
        logger.critical(f"GLOBAL CIRCUIT BREAKER: {reason}")

    def trigger_symbol_circuit(self, symbol: str, reason: str) -> None:
        expiry = _utcnow_naive() + timedelta(minutes=30)
        self.symbol_circuits[symbol] = expiry
        breach = {
            "timestamp": _utcnow_naive(),
            "type": "SYMBOL",
            "symbol": symbol,
            "reason": reason,
            "expiry": expiry,
        }
        self.breach_history.append(breach)
        logger.warning(f"SYMBOL CIRCUIT BREAKER for {symbol}: {reason}")

    def reset_circuits(self) -> None:
        now = _utcnow_naive()
        if self.global_circuit and self.global_expiry and now >= self.global_expiry:
            self.global_circuit = False
            self.global_expiry = None
            logger.info("Global circuit reset after cooldown")

        expired = [symbol for symbol, expiry in self.symbol_circuits.items() if now >= expiry]
        for symbol in expired:
            self.symbol_circuits.pop(symbol, None)
            logger.info(f"Symbol circuit reset: {symbol}")

    def get_status(self) -> Dict[str, Any]:
        now = _utcnow_naive()
        active_symbols = [s for s, expiry in self.symbol_circuits.items() if now < expiry]
        return {
            "global_circuit": self.global_circuit,
            "symbol_circuits": active_symbols,
            "loss_streak": self.loss_streak,
            "recent_trades": list(self.trades_window),
        }


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
