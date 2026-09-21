"""Correlation manager for controlling correlated exposure."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np
from loguru import logger


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CorrelationManager:
    """Tracks pair correlations and provides portfolio penalties."""

    def __init__(self, db_session=None, lookback_days: int = 30):
        self.db = db_session
        self.lookback_days = int(lookback_days)
        self.correlation_matrix: Dict[Tuple[str, str], float] = {}
        self.last_update: datetime | None = None
        self.calculate_correlations([])
        logger.info("CorrelationManager initialized")

    def fetch_price_data(self, symbols: Iterable[str]):
        """Reserved hook for real market data retrieval."""
        _ = symbols
        return None

    def calculate_correlations(self, symbols: Iterable[str]) -> Dict[Tuple[str, str], float]:
        """Populate a baseline correlation map (expandable with real data)."""
        _ = symbols
        self.correlation_matrix = {
            ("EURUSD", "GBPUSD"): 0.85,
            ("EURUSD", "USDJPY"): -0.40,
            ("EURUSD", "AUDUSD"): 0.75,
            ("GBPUSD", "USDJPY"): -0.35,
            ("GBPUSD", "AUDUSD"): 0.70,
            ("USDJPY", "AUDUSD"): -0.30,
            ("XAUUSD", "EURUSD"): 0.50,
            ("XAUUSD", "USDJPY"): -0.60,
        }
        self.last_update = _utcnow_naive()
        return self.correlation_matrix

    @staticmethod
    def _norm_symbol(symbol: str) -> str:
        return str(symbol or "").upper()

    def get_correlation(self, symbol1: str, symbol2: str) -> float:
        """Return pair correlation, falling back to moderate correlation."""
        s1 = self._norm_symbol(symbol1)
        s2 = self._norm_symbol(symbol2)
        if s1 == s2:
            return 1.0
        if (s1, s2) in self.correlation_matrix:
            return float(self.correlation_matrix[(s1, s2)])
        if (s2, s1) in self.correlation_matrix:
            return float(self.correlation_matrix[(s2, s1)])
        return 0.5

    @staticmethod
    def _extract_symbols(positions: Iterable[Any]) -> List[str]:
        symbols: List[str] = []
        for pos in positions or []:
            if isinstance(pos, dict):
                sym = pos.get("symbol")
            else:
                sym = getattr(pos, "symbol", None)
            if sym:
                symbols.append(str(sym).upper())
        return symbols

    def calculate_portfolio_correlation(self, positions: Iterable[Any], new_symbol: str | None = None) -> float:
        """Return average absolute pairwise portfolio correlation."""
        symbols = self._extract_symbols(positions)
        if new_symbol:
            symbols.append(str(new_symbol).upper())

        if len(symbols) < 2:
            return 0.0

        values: List[float] = []
        for idx, s1 in enumerate(symbols):
            for s2 in symbols[idx + 1 :]:
                values.append(abs(self.get_correlation(s1, s2)))

        return float(np.mean(values)) if values else 0.0

    def get_correlation_penalty(
        self,
        positions: Iterable[Any],
        new_symbol: str,
        max_correlation: float = 0.70,
    ) -> float:
        """Return sizing penalty based on projected portfolio correlation."""
        avg_corr = self.calculate_portfolio_correlation(positions, new_symbol)
        if avg_corr <= max_correlation:
            return 1.0
        penalty = max_correlation / max(avg_corr, 1e-9)
        return float(max(0.3, penalty))

    def update_daily(self) -> None:
        """Refresh internal correlations (placeholder for real recalculation)."""
        self.calculate_correlations([])
