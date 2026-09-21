"""Vectorized backtesting engine used by Phase 6 walk-forward validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd


@dataclass
class BacktestThresholds:
    min_sharpe: float = 1.0
    min_win_rate: float = 50.0
    min_consistency: float = 0.70


class BacktestEngine:
    """Simple vectorized RSI strategy backtester for robustness checks."""

    def __init__(self, initial_capital: float = 10_000.0, commission: float = 0.0001, slippage: float = 0.0):
        self.initial_capital = float(initial_capital)
        self.commission = float(commission)
        self.slippage = float(slippage)

    @staticmethod
    def prepare_data(df: pd.DataFrame, indicator_params: Dict[str, Any]) -> pd.DataFrame:
        data = df.copy()
        rsi_period = int(indicator_params.get("rsi_period", 14))
        atr_period = int(indicator_params.get("atr_period", 14))

        delta = data["close"].diff()
        gains = delta.clip(lower=0).rolling(window=rsi_period).mean()
        losses = (-delta.clip(upper=0)).rolling(window=rsi_period).mean()
        rs = gains / losses.replace(0, np.nan)
        data["rsi"] = 100 - (100 / (1 + rs))

        high_low = data["high"] - data["low"]
        high_close = (data["high"] - data["close"].shift()).abs()
        low_close = (data["low"] - data["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        data["atr"] = tr.rolling(window=atr_period).mean()

        return data.dropna(subset=["close", "rsi", "atr"])

    @staticmethod
    def generate_signals(data: pd.DataFrame, strategy_params: Dict[str, Any]) -> pd.DataFrame:
        oversold = float(strategy_params.get("rsi_oversold", 30))
        overbought = float(strategy_params.get("rsi_overbought", 70))

        signals = pd.DataFrame(index=data.index)
        signals["signal"] = 0
        signals.loc[data["rsi"] < oversold, "signal"] = 1
        signals.loc[data["rsi"] > overbought, "signal"] = -1
        return signals

    def run_backtest(self, data: pd.DataFrame, signals: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, float]]:
        merged = signals.copy()
        merged["returns"] = data["close"].pct_change().fillna(0.0)

        merged["position"] = merged["signal"].shift(1).fillna(0)
        merged["strategy_returns"] = merged["position"] * merged["returns"]

        trade_changes = merged["position"].diff().abs().fillna(0.0)
        transaction_cost = trade_changes * (self.commission + self.slippage)
        merged["strategy_returns"] = merged["strategy_returns"] - transaction_cost

        merged["trades"] = trade_changes
        merged["equity"] = self.initial_capital * (1 + merged["strategy_returns"]).cumprod()

        metrics = self.calculate_metrics(merged)
        return merged, metrics

    def calculate_metrics(self, results: pd.DataFrame) -> Dict[str, float]:
        metrics: Dict[str, float] = {}
        if results.empty:
            return {
                "total_return": 0.0,
                "annual_return": 0.0,
                "sharpe": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "total_trades": 0.0,
            }

        end_equity = float(results["equity"].iloc[-1])
        metrics["total_return"] = (end_equity / self.initial_capital - 1.0) * 100.0

        elapsed_days = max(1, (results.index[-1] - results.index[0]).days)
        metrics["annual_return"] = ((1.0 + metrics["total_return"] / 100.0) ** (365.0 / elapsed_days) - 1.0) * 100.0

        returns_std = float(results["strategy_returns"].std())
        if returns_std > 0:
            metrics["sharpe"] = float(results["strategy_returns"].mean() / returns_std * np.sqrt(252))
        else:
            metrics["sharpe"] = 0.0

        rolling_max = results["equity"].cummax()
        drawdown = (results["equity"] - rolling_max) / rolling_max.replace(0, np.nan)
        metrics["max_drawdown"] = abs(float(drawdown.min() * 100.0)) if not drawdown.empty else 0.0

        trade_rows = results[results["trades"] > 0]
        metrics["total_trades"] = float(len(trade_rows))
        if not trade_rows.empty:
            realized = results.loc[trade_rows.index, "strategy_returns"]
            metrics["win_rate"] = float((realized > 0).mean() * 100.0)
        else:
            metrics["win_rate"] = 0.0

        return metrics

    def walk_forward(
        self,
        data: pd.DataFrame,
        indicator_params: Dict[str, Any],
        strategy_params: Dict[str, Any],
        train_size: float = 0.60,
        test_size: float = 0.20,
    ) -> List[Dict[str, Any]]:
        prepared = self.prepare_data(data, indicator_params)
        if prepared.empty:
            return []

        total = len(prepared)
        train_window = int(total * train_size)
        test_window = int(total * test_size)
        if train_window < 50 or test_window < 20:
            return []

        periods: List[Dict[str, Any]] = []
        start = 0
        while True:
            train_end = start + train_window
            test_end = train_end + test_window
            if test_end > total:
                break

            test_data = prepared.iloc[train_end:test_end]
            signals = self.generate_signals(test_data, strategy_params)
            _, metrics = self.run_backtest(test_data, signals)

            periods.append(
                {
                    "train_start": prepared.index[start].isoformat(),
                    "train_end": prepared.index[train_end - 1].isoformat(),
                    "test_start": prepared.index[train_end].isoformat(),
                    "test_end": prepared.index[test_end - 1].isoformat(),
                    "metrics": metrics,
                }
            )
            start += test_window

        return periods
