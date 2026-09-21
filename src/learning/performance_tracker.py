"""Performance tracking and analytics for self-learning."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from datetime import timezone
import json
from typing import Dict, Iterable, List

import numpy as np
from loguru import logger

from .models import DailyPerformance
from .models import Trade


class PerformanceTracker:
    """Tracks and analyzes trade performance by regime and POI type."""

    def __init__(self, db_session):
        self.db = db_session
        self.cache: Dict[str, Dict] = {}
        logger.info("PerformanceTracker initialized")

    @contextmanager
    def _session_scope(self):
        """Yield a SQLAlchemy session from either Session or TradeDatabase."""
        if all(hasattr(self.db, attr) for attr in ("query", "add", "commit")):
            yield self.db
            return

        session_factory = getattr(self.db, "Session", None)
        if session_factory is None:
            raise TypeError("db_session must be a SQLAlchemy session or TradeDatabase")

        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @staticmethod
    def _utcnow_naive() -> datetime:
        """Return a UTC timestamp without tzinfo for SQLite compatibility."""
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def calculate_expectancy(self, trades: Iterable[Trade]) -> float:
        """Calculate expectancy in R multiples."""
        r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
        return float(np.mean(r_values)) if r_values else 0.0

    def calculate_profit_factor(self, trades: Iterable[Trade]) -> float:
        """Calculate profit factor (gross profit / gross loss)."""
        gross_profit = sum(t.pnl for t in trades if t.pnl is not None and t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in trades if t.pnl is not None and t.pnl < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return float(gross_profit / gross_loss)

    def calculate_sharpe(self, trades: List[Trade]) -> float:
        """Calculate simple Sharpe ratio over raw PnL values."""
        if len(trades) < 5:
            return 0.0
        returns = [t.pnl for t in trades if t.pnl is not None]
        if not returns:
            return 0.0
        return float(np.mean(returns) / (np.std(returns) + 1e-9))

    def calculate_max_drawdown(self, trades: List[Trade]) -> float:
        """Calculate max drawdown percentage from cumulative PnL."""
        if not trades:
            return 0.0

        sorted_trades = sorted(trades, key=lambda x: x.timestamp)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0

        for trade in sorted_trades:
            if trade.pnl is not None:
                cumulative += float(trade.pnl)
                if cumulative > peak:
                    peak = cumulative
                dd = (peak - cumulative) / (peak + 1e-9)
                max_dd = max(max_dd, dd)

        return float(max_dd * 100.0)

    def get_regime_performance(self, days: int = 30) -> Dict[str, Dict]:
        """Get performance metrics by regime for the last N days."""
        cutoff = self._utcnow_naive() - timedelta(days=days)
        with self._session_scope() as session:
            trades = session.query(Trade).filter(Trade.timestamp >= cutoff).all()

        regimes: Dict[str, List[Trade]] = {}
        for trade in trades:
            key = f"{trade.regime_trend}_{trade.regime_volatility}_{trade.regime_session}"
            regimes.setdefault(key, []).append(trade)

        results: Dict[str, Dict] = {}
        for key, regime_trades in regimes.items():
            if len(regime_trades) < 5:
                continue
            rr_values = [t.rr_ratio for t in regime_trades if t.rr_ratio is not None]
            results[key] = {
                "trades": len(regime_trades),
                "expectancy": self.calculate_expectancy(regime_trades),
                "profit_factor": self.calculate_profit_factor(regime_trades),
                "win_rate": sum(1 for t in regime_trades if t.outcome == "WIN") / len(regime_trades),
                "sharpe": self.calculate_sharpe(regime_trades),
                "avg_rr": float(np.mean(rr_values)) if rr_values else 0.0,
                "max_drawdown": self.calculate_max_drawdown(regime_trades),
            }
        return results

    def get_poi_performance(self, days: int = 30) -> Dict[str, Dict]:
        """Get performance metrics by POI type."""
        cutoff = self._utcnow_naive() - timedelta(days=days)
        with self._session_scope() as session:
            trades = session.query(Trade).filter(Trade.timestamp >= cutoff).all()

        poi_types: Dict[str, List[Trade]] = {}
        for trade in trades:
            key = trade.poi_type or "UNKNOWN"
            poi_types.setdefault(key, []).append(trade)

        results: Dict[str, Dict] = {}
        for poi, poi_trades in poi_types.items():
            results[poi] = {
                "trades": len(poi_trades),
                "expectancy": self.calculate_expectancy(poi_trades),
                "win_rate": sum(1 for t in poi_trades if t.outcome == "WIN") / len(poi_trades),
                "by_regime": {},
            }

            regime_groups: Dict[str, List[Trade]] = {}
            for trade in poi_trades:
                regime_key = f"{trade.regime_trend}_{trade.regime_volatility}"
                regime_groups.setdefault(regime_key, []).append(trade)

            for regime_key, grouped in regime_groups.items():
                if len(grouped) >= 3:
                    results[poi]["by_regime"][regime_key] = {
                        "win_rate": sum(1 for x in grouped if x.outcome == "WIN") / len(grouped),
                        "trades": len(grouped),
                    }

        return results

    def update_daily_summary(self) -> DailyPerformance | None:
        """Generate and store a daily performance summary."""
        today = self._utcnow_naive().date()
        start = datetime.combine(today, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())

        with self._session_scope() as session:
            trades = session.query(Trade).filter(Trade.timestamp >= start, Trade.timestamp <= end).all()
            if not trades:
                logger.info("No trades today")
                return None

            wins = [t for t in trades if t.outcome == "WIN"]
            summary = DailyPerformance(
                date=today,
                total_trades=len(trades),
                winning_trades=len(wins),
                total_pnl=sum(t.pnl for t in trades if t.pnl is not None),
                performance_by_regime=json.dumps(self.get_regime_performance(1)),
                performance_by_poi=json.dumps(self.get_poi_performance(1)),
                performance_by_session=json.dumps({}),
            )

            session.add(summary)
            session.commit()
            logger.info(f"Daily summary saved for {today}")
            return summary
