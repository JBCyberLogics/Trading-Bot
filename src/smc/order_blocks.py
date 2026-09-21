"""Order Block detection for SMC trading."""

import pandas as pd
from loguru import logger


class OrderBlockDetector:
    """
    Detects bullish and bearish order blocks based on ICT methodology.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.impulse_atr_mult = self.config.get("impulse_atr_mult", 1.5)
        self.ob_max_age = self.config.get("ob_max_age_hours", 24)
        logger.info("OrderBlockDetector initialized")

    def calculate_atr(self, df, period=14):
        """Calculate Average True Range."""
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()

        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr

    def find_impulsive_candles(self, df, atr):
        """Identify impulsive candles that could create order blocks."""
        impulsive = []
        for i in range(1, len(df) - 1):
            candle_range = df["high"].iloc[i] - df["low"].iloc[i]
            body_size = abs(df["close"].iloc[i] - df["open"].iloc[i])
            body_ratio = body_size / candle_range if candle_range > 0 else 0

            # Impulsive criteria: large range, large body
            if candle_range > self.impulse_atr_mult * atr.iloc[i] and body_ratio > 0.6:
                impulsive.append(
                    {
                        "index": i,
                        "time": df.index[i],
                        "type": "BULLISH" if df["close"].iloc[i] > df["open"].iloc[i] else "BEARISH",
                        "range": candle_range,
                        "body_ratio": body_ratio,
                        "close": df["close"].iloc[i],
                        "high": df["high"].iloc[i],
                        "low": df["low"].iloc[i],
                    }
                )
        return impulsive

    def find_order_blocks(self, df, atr):
        """Find order blocks (previous candle before impulsive move)."""
        impulsive = self.find_impulsive_candles(df, atr)
        order_blocks = []

        for imp in impulsive:
            idx = imp["index"]
            if idx > 0:
                prev = df.iloc[idx - 1]

                # Bullish OB: impulsive up, previous bearish
                if imp["type"] == "BULLISH" and prev["close"] < prev["open"]:
                    order_blocks.append(
                        {
                            "type": "BULLISH",
                            "zone": [prev["low"], prev["high"]],
                            "time": df.index[idx - 1],
                            "strength": imp["range"] / atr.iloc[idx],
                            "impulse_time": imp["time"],
                        }
                    )

                # Bearish OB: impulsive down, previous bullish
                elif imp["type"] == "BEARISH" and prev["close"] > prev["open"]:
                    order_blocks.append(
                        {
                            "type": "BEARISH",
                            "zone": [prev["low"], prev["high"]],
                            "time": df.index[idx - 1],
                            "strength": imp["range"] / atr.iloc[idx],
                            "impulse_time": imp["time"],
                        }
                    )

        return order_blocks

    def filter_by_age(self, order_blocks, current_time):
        """Remove order blocks older than max age."""
        filtered = []
        for ob in order_blocks:
            age_hours = (current_time - ob["time"]).total_seconds() / 3600
            if age_hours <= self.ob_max_age:
                filtered.append(ob)
        return filtered
