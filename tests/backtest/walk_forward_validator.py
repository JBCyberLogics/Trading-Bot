"""Walk-forward validator with MT5-required historical pull."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from src.execution.zmq_bridge import ZMQBridge
from tests.backtest.backtest_engine import BacktestEngine
from tests.backtest.backtest_engine import BacktestThresholds


_TIMEFRAME_SECONDS = {
    "M1": 60,
    "M2": 120,
    "M3": 180,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


class WalkForwardValidator:
    """Runs walk-forward validation against MT5 bars and fixed readiness thresholds."""

    def __init__(self, bridge: ZMQBridge, backtest_engine: BacktestEngine, thresholds: BacktestThresholds):
        self.bridge = bridge
        self.engine = backtest_engine
        self.thresholds = thresholds

    @staticmethod
    def _parse_dt(value: str | datetime) -> datetime:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _bars_required(start_dt: datetime, end_dt: datetime, timeframe: str) -> int:
        tf = str(timeframe).upper()
        seconds = _TIMEFRAME_SECONDS.get(tf)
        if not seconds:
            raise ValueError(f"Unsupported timeframe for walk-forward: {timeframe}")
        span_seconds = max(1, int((end_dt - start_dt).total_seconds()))
        base = span_seconds // seconds
        return max(100, int(base * 1.2) + 100)

    def load_historical_data(self, symbol: str, start_date: str | datetime, end_date: str | datetime, timeframe: str = "H1") -> pd.DataFrame:
        """Load historical OHLC data exclusively from MT5 bridge."""
        start_dt = self._parse_dt(start_date)
        end_dt = self._parse_dt(end_date)
        if end_dt <= start_dt:
            raise ValueError("end_date must be after start_date")

        count = self._bars_required(start_dt, end_dt, timeframe)
        response = self.bridge.get_rates(symbol=symbol, timeframe=timeframe, count=count)
        if response.get("error"):
            raise RuntimeError(f"MT5 rates pull failed: {response}")

        bars = response.get("bars", [])
        if not bars:
            raise RuntimeError("MT5 rates pull returned empty bars")

        frame = pd.DataFrame(bars)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
        frame = frame.set_index("timestamp").sort_index()
        frame = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        frame = frame.dropna(subset=["open", "high", "low", "close"])

        window = frame[(frame.index >= start_dt) & (frame.index <= end_dt)]
        if window.empty:
            raise RuntimeError("No MT5 bars available in requested lookback window")

        return window

    def validate_parameter_set(self, params: Dict[str, Any], data: pd.DataFrame) -> Dict[str, Any]:
        periods = self.engine.walk_forward(
            data=data,
            indicator_params=params.get("indicators", {}),
            strategy_params=params.get("strategy", {}),
            train_size=0.60,
            test_size=0.20,
        )
        if not periods:
            return {
                "metrics": {
                    "total_return": 0.0,
                    "sharpe": 0.0,
                    "win_rate": 0.0,
                    "max_drawdown": 0.0,
                    "consistency": 0.0,
                    "periods": 0,
                    "total_trades": 0,
                },
                "periods": [],
                "ready_for_live": False,
            }

        metrics_keys = ["total_return", "sharpe", "max_drawdown", "win_rate"]
        averages: Dict[str, float] = {}
        for key in metrics_keys:
            series = [float(p["metrics"].get(key, 0.0)) for p in periods]
            averages[key] = float(np.mean(series)) if series else 0.0
            averages[f"{key}_std"] = float(np.std(series)) if series else 0.0

        consistency = 1.0
        for key in ("sharpe", "total_return"):
            mean_val = averages.get(key, 0.0)
            std_val = averages.get(f"{key}_std", 0.0)
            if mean_val != 0:
                cv = abs(std_val / mean_val)
                consistency *= max(0.0, 1.0 - cv)
        averages["consistency"] = float(consistency)
        averages["periods"] = len(periods)
        averages["total_trades"] = int(sum(int(p["metrics"].get("total_trades", 0)) for p in periods))

        ready = self.meets_thresholds(averages)
        return {
            "metrics": averages,
            "periods": periods,
            "ready_for_live": ready,
        }

    def meets_thresholds(self, metrics: Dict[str, Any]) -> bool:
        return (
            float(metrics.get("sharpe", 0.0)) > self.thresholds.min_sharpe
            and float(metrics.get("win_rate", 0.0)) > self.thresholds.min_win_rate
            and float(metrics.get("consistency", 0.0)) > self.thresholds.min_consistency
        )

    def generate_validation_report(self, result: Dict[str, Any], output_path: str | Path) -> bool:
        ready = bool(result.get("ready_for_live", False))
        metrics = result.get("metrics", {})
        periods = result.get("periods", [])

        rows = []
        for metric in ["sharpe", "total_return", "max_drawdown", "win_rate"]:
            values = [float(p.get("metrics", {}).get(metric, 0.0)) for p in periods]
            rows.append(
                {
                    "metric": metric,
                    "avg": float(metrics.get(metric, 0.0)),
                    "std": float(metrics.get(f"{metric}_std", 0.0)),
                    "min": float(min(values)) if values else 0.0,
                    "max": float(max(values)) if values else 0.0,
                }
            )

        table_rows = "".join(
            [
                (
                    f"<tr><td>{r['metric']}</td><td>{r['avg']:.3f}</td><td>{r['std']:.3f}</td>"
                    f"<td>{r['min']:.3f}</td><td>{r['max']:.3f}</td></tr>"
                )
                for r in rows
            ]
        )

        html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Walk-Forward Validation Report</title>
  <style>
    body {{ font-family: sans-serif; margin: 24px; background: #f6f8fa; color: #17212b; }}
    .banner {{ padding: 14px; color: #fff; font-weight: 700; border-radius: 8px; }}
    .ready {{ background: #1f8f4e; }}
    .not-ready {{ background: #b23a2b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; background: #fff; }}
    th, td {{ border: 1px solid #d0d7de; padding: 8px; text-align: left; }}
    th {{ background: #0a4f86; color: #fff; }}
    .panel {{ background: #fff; padding: 12px; border-radius: 8px; margin-top: 16px; }}
  </style>
</head>
<body>
  <h1>Walk-Forward Validation Report</h1>
  <p>Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
  <div class="banner {'ready' if ready else 'not-ready'}">
    {'READY FOR LIVE DEPLOYMENT' if ready else 'NOT READY FOR LIVE DEPLOYMENT'}
  </div>

  <h2>Performance Metrics</h2>
  <table>
    <tr><th>Metric</th><th>Average</th><th>Std Dev</th><th>Min</th><th>Max</th></tr>
    {table_rows}
  </table>

  <div class="panel">
    <h2>Threshold Check</h2>
    <p>Sharpe > {self.thresholds.min_sharpe:.2f}: {'PASS' if float(metrics.get('sharpe', 0.0)) > self.thresholds.min_sharpe else 'FAIL'}</p>
    <p>Win Rate > {self.thresholds.min_win_rate:.2f}: {'PASS' if float(metrics.get('win_rate', 0.0)) > self.thresholds.min_win_rate else 'FAIL'}</p>
    <p>Consistency > {self.thresholds.min_consistency:.2f}: {'PASS' if float(metrics.get('consistency', 0.0)) > self.thresholds.min_consistency else 'FAIL'}</p>
    <p>Periods: {int(metrics.get('periods', 0))}</p>
    <p>Total trades: {int(metrics.get('total_trades', 0))}</p>
  </div>
</body>
</html>
"""

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        return ready
