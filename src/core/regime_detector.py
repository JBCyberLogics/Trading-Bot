"""Market regime classifier for trade tagging."""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd
from loguru import logger


class RegimeDetector:
    """Classifies trend, volatility, session, and spread regime."""

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.atr_period = int(self.config.get("atr_period", 14))
        self.adx_period = int(self.config.get("adx_period", 14))
        self.volatility_lookback = int(self.config.get("volatility_lookback", 20))
        logger.info("RegimeDetector initialized")

    def calculate_adx(self, df: pd.DataFrame, period: int | None = None) -> pd.Series:
        """Calculate ADX as trend-strength proxy."""
        p = period or self.adx_period
        high = df["high"]
        low = df["low"]
        close = df["close"]

        plus_dm = high.diff()
        minus_dm = low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = minus_dm.abs()

        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(window=p).mean()

        plus_di = 100 * (plus_dm.rolling(window=p).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=p).mean() / atr)
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
        adx = dx.rolling(window=p).mean()
        return adx.ffill()

    def calculate_atr(self, df: pd.DataFrame, period: int | None = None) -> pd.Series:
        """Calculate ATR."""
        p = period or self.atr_period
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr = pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.rolling(window=p).mean()

    def get_atr_percentile(self, df: pd.DataFrame, period: int | None = None) -> float:
        """Get current ATR percentile against recent ATR history."""
        lookback = period or self.volatility_lookback
        atr = self.calculate_atr(df)
        if atr.empty:
            return 0.5

        current_atr = atr.iloc[-1]
        if pd.isna(current_atr):
            return 0.5

        atr_history = atr.iloc[-lookback - 1 : -1].dropna()
        if atr_history.empty:
            return 0.5

        percentile = float((atr_history < current_atr).sum() / len(atr_history))
        return max(0.0, min(1.0, percentile))

    def detect_regime(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Detect current market regime from OHLC dataframe."""
        if df.empty:
            raise ValueError("Cannot detect regime from empty dataframe")

        adx = self.calculate_adx(df)
        current_adx = adx.iloc[-1] if not adx.empty else 20.0
        if pd.isna(current_adx):
            current_adx = 20.0

        atr_percentile = self.get_atr_percentile(df, self.volatility_lookback)

        # Trend
        if current_adx > 25:
            trend_strength = "STRONG"
        elif current_adx > 20:
            trend_strength = "MODERATE"
        else:
            trend_strength = "WEAK"

        # Volatility
        if atr_percentile > 0.8:
            volatility = "HIGH"
        elif atr_percentile < 0.2:
            volatility = "LOW"
        else:
            volatility = "MODERATE"

        ts = df.index[-1]
        hour = int(getattr(ts, "hour", 12))
        if 7 <= hour < 9:
            session = "LONDON_OPEN"
        elif 13 <= hour < 16:
            session = "NY_OPEN"
        elif 15 <= hour < 17:
            session = "LONDON_CLOSE"
        elif hour < 7 or hour >= 22:
            session = "ASIAN"
        else:
            session = "REGULAR"

        # Placeholder until spread fetched from broker feed
        spread_tier = "NORMAL"
        spread_pips = 1.0

        result = {
            "trend": {"strength": trend_strength, "adx": float(current_adx)},
            "volatility": {"level": volatility, "atr_percentile": float(atr_percentile)},
            "session": {"name": session, "hour": hour},
            "spread": {"tier": spread_tier, "pips": spread_pips},
            "timestamp": ts if hasattr(ts, "strftime") else None,
        }
        logger.debug(
            f"Regime detected: {trend_strength} trend, {volatility} volatility, {session} session"
        )
        return result

    def tag_trade(self, trade_dict: Dict[str, Any], regime_result: Dict[str, Any]) -> Dict[str, Any]:
        """Attach regime tags to a trade record dictionary."""
        trade_dict["regime_trend"] = regime_result["trend"]["strength"]
        trade_dict["regime_trend_value"] = regime_result["trend"]["adx"]
        trade_dict["regime_volatility"] = regime_result["volatility"]["level"]
        trade_dict["regime_volatility_value"] = regime_result["volatility"]["atr_percentile"]
        trade_dict["regime_session"] = regime_result["session"]["name"]
        trade_dict["regime_spread"] = regime_result["spread"]["tier"]
        trade_dict["regime_spread_value"] = regime_result["spread"]["pips"]
        return trade_dict
