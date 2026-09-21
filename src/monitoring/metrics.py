"""Prometheus metrics collection for production monitoring."""

from __future__ import annotations

from functools import wraps
import time
from typing import Any, Dict

from loguru import logger

try:
    from prometheus_client import Counter
    from prometheus_client import Gauge
    from prometheus_client import Histogram
    from prometheus_client import REGISTRY
    from prometheus_client import generate_latest

    PROMETHEUS_AVAILABLE = True
except Exception:  # pragma: no cover - fallback path only when dependency missing
    Counter = Gauge = Histogram = None
    REGISTRY = None
    generate_latest = None
    PROMETHEUS_AVAILABLE = False


if PROMETHEUS_AVAILABLE:
    TRADES_TOTAL = Counter("bot_trades_total", "Total number of trades", ["symbol", "direction", "outcome"])
    PNL_TOTAL = Counter("bot_pnl_total", "Total P&L in USD", ["symbol"])
    ACCOUNT_BALANCE = Gauge("bot_account_balance", "Current account balance")
    ACCOUNT_EQUITY = Gauge("bot_account_equity", "Current account equity")
    DRAWDOWN = Gauge("bot_drawdown_percent", "Current drawdown percentage")
    OPEN_POSITIONS = Gauge("bot_open_positions", "Number of open positions")
    CONSECUTIVE_LOSSES = Gauge("bot_consecutive_losses", "Current consecutive losses")
    RISK_STATE = Gauge("bot_risk_state", "Current risk state (0=NORMAL,1=CAUTION,2=DEFENSIVE,3=HALT)")
    LIVE_DEPLOYMENT_PROGRESS = Gauge("bot_live_deployment_progress", "Live deployment progress (trades completed)")
    TRADE_DURATION = Histogram(
        "bot_trade_duration_seconds",
        "Trade duration in seconds",
        buckets=[60, 300, 600, 1800, 3600, 7200, 14400, 28800],
    )
    EXECUTION_LATENCY = Histogram(
        "bot_execution_latency_ms",
        "Order execution latency in milliseconds",
        buckets=[10, 50, 100, 200, 500, 1000, 2000],
    )


class MetricsCollector:
    """Collect and expose runtime metrics for Prometheus."""

    def __init__(self) -> None:
        self.start_time = time.time()
        self.enabled = PROMETHEUS_AVAILABLE
        if self.enabled:
            logger.info("MetricsCollector initialized (prometheus enabled)")
        else:
            logger.warning("prometheus_client not installed; metrics endpoint will be unavailable")

    def record_trade(self, trade_data: Dict[str, Any]) -> None:
        if not self.enabled:
            return

        symbol = str(trade_data.get("symbol", "unknown"))
        direction = str(trade_data.get("direction", "unknown"))
        outcome = str(trade_data.get("outcome", "unknown"))

        TRADES_TOTAL.labels(symbol=symbol, direction=direction, outcome=outcome).inc()

        pnl = float(trade_data.get("pnl", 0.0) or 0.0)
        PNL_TOTAL.labels(symbol=symbol).inc(pnl)

        duration = trade_data.get("duration")
        if duration is not None:
            TRADE_DURATION.observe(float(duration))

        latency_ms = trade_data.get("latency_ms")
        if latency_ms is not None:
            EXECUTION_LATENCY.observe(float(latency_ms))

    def update_account_metrics(self, account_data: Dict[str, Any]) -> None:
        if not self.enabled:
            return

        ACCOUNT_BALANCE.set(float(account_data.get("balance", 0.0) or 0.0))
        ACCOUNT_EQUITY.set(float(account_data.get("equity", 0.0) or 0.0))
        DRAWDOWN.set(float(account_data.get("drawdown", 0.0) or 0.0))
        OPEN_POSITIONS.set(float(account_data.get("open_positions", 0.0) or 0.0))
        CONSECUTIVE_LOSSES.set(float(account_data.get("consecutive_losses", 0.0) or 0.0))

        risk_state_map = {"NORMAL": 0, "CAUTION": 1, "DEFENSIVE": 2, "HALT": 3}
        risk_state = str(account_data.get("risk_state", "NORMAL"))
        RISK_STATE.set(risk_state_map.get(risk_state, 0))

        LIVE_DEPLOYMENT_PROGRESS.set(float(account_data.get("live_progress", 0.0) or 0.0))

    def get_metrics(self) -> bytes:
        if not self.enabled:
            return b"metrics_unavailable 1\n"
        return generate_latest(REGISTRY)

    def get_uptime(self) -> float:
        return max(0.0, time.time() - self.start_time)


def measure_time(metric_name: str):
    """Decorator for logging function execution duration."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = func(*args, **kwargs)
            duration = time.time() - start
            logger.debug(f"{metric_name} took {duration:.3f}s")
            return result

        return wrapper

    return decorator
