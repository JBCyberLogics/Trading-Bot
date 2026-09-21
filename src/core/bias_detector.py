"""Layer 1: Bias Detection on 4H/Daily timeframe."""

import pandas as pd
from loguru import logger


class BiasDetector:
    """
    Detects market bias using Higher High/Higher Low (bullish) or
    Lower High/Lower Low (bearish) patterns on higher timeframes.
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.lookback = self.config.get("swing_lookback", 20)
        self.swing_confirmation = max(1, int(self.config.get("swing_confirmation_candles", 2)))
        self.min_swing_distance_atr = self.config.get("min_swing_distance_atr", 0.5)
        logger.info("BiasDetector initialized")

    def find_swing_highs(self, df, column="high", lookback=None):
        """Find swing highs using local maxima detection."""
        if lookback is None:
            lookback = self.lookback

        swings = []
        right_confirm = self.swing_confirmation
        for i in range(lookback, len(df) - right_confirm):
            left = i - lookback
            right = i + right_confirm + 1
            if df[column].iloc[i] == max(df[column].iloc[left:right]):
                swings.append(
                    {
                        "price": df[column].iloc[i],
                        "index": i,
                        "time": df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i,
                    }
                )
        return swings

    def find_swing_lows(self, df, column="low", lookback=None):
        """Find swing lows using local minima detection."""
        if lookback is None:
            lookback = self.lookback

        swings = []
        right_confirm = self.swing_confirmation
        for i in range(lookback, len(df) - right_confirm):
            left = i - lookback
            right = i + right_confirm + 1
            if df[column].iloc[i] == min(df[column].iloc[left:right]):
                swings.append(
                    {
                        "price": df[column].iloc[i],
                        "index": i,
                        "time": df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i,
                    }
                )
        return swings

    def filter_swings_by_distance(self, swings, min_distance):
        """Remove swings that are too close together."""
        if len(swings) < 2:
            return swings

        filtered = [swings[0]]
        for swing in swings[1:]:
            if abs(swing["price"] - filtered[-1]["price"]) >= min_distance:
                filtered.append(swing)
        return filtered

    def determine_bias(self, df):
        """
        Determine market bias based on swing highs and lows.

        Returns:
            dict with bias direction, confidence, and key levels
        """
        # Calculate ATR for filtering (avoid mutating input df)
        atr = self.calculate_atr(df, 14)
        current_atr = atr.iloc[-1]
        if pd.isna(current_atr) or current_atr <= 0:
            return {"bias": "NEUTRAL", "confidence": 0, "reason": "ATR unavailable"}
        min_distance = current_atr * self.min_swing_distance_atr

        # Find swings
        swing_highs = self.find_swing_highs(df)
        swing_lows = self.find_swing_lows(df)

        # Filter by distance
        swing_highs = self.filter_swings_by_distance(swing_highs, min_distance)[-5:]
        swing_lows = self.filter_swings_by_distance(swing_lows, min_distance)[-5:]

        # Need at least 3 swings for pattern detection
        if len(swing_highs) < 3 or len(swing_lows) < 3:
            return {"bias": "NEUTRAL", "confidence": 0, "reason": "Insufficient swing data"}

        # Get last 3 swings
        last_highs = [h["price"] for h in swing_highs[-3:]]
        last_lows = [l["price"] for l in swing_lows[-3:]]

        # Check for HH/HL pattern (bullish)
        hh_pattern = last_highs[2] > last_highs[1] > last_highs[0]
        hl_pattern = last_lows[2] > last_lows[1] > last_lows[0]

        # Check for LH/LL pattern (bearish)
        lh_pattern = last_highs[2] < last_highs[1] < last_highs[0]
        ll_pattern = last_lows[2] < last_lows[1] < last_lows[0]

        # Determine bias
        if hh_pattern and hl_pattern:
            bias = "BULLISH"
            confidence = min(100, 70 + (len(swing_highs) + len(swing_lows)))
        elif lh_pattern and ll_pattern:
            bias = "BEARISH"
            confidence = min(100, 70 + (len(swing_highs) + len(swing_lows)))
        else:
            # Check EMA alignment as secondary signal
            ema50 = df["close"].ewm(span=50).mean()
            ema200 = df["close"].ewm(span=200).mean()

            if df["close"].iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1]:
                bias = "BULLISH_CAUTION"
                confidence = 50
            elif df["close"].iloc[-1] < ema50.iloc[-1] < ema200.iloc[-1]:
                bias = "BEARISH_CAUTION"
                confidence = 50
            else:
                bias = "NEUTRAL"
                confidence = 30

        # Detect Change of Character (ChoCH)
        choch = self.detect_choch(df, swing_highs, swing_lows)

        result = {
            "bias": bias,
            "confidence": confidence,
            "major_support": swing_lows[-1]["price"] if swing_lows else None,
            "major_resistance": swing_highs[-1]["price"] if swing_highs else None,
            "discount_zone": swing_lows[-1]["price"] * 0.995 if swing_lows else None,
            "premium_zone": swing_highs[-1]["price"] * 1.005 if swing_highs else None,
            "choch_detected": choch["detected"],
            "choch_type": choch["type"],
            "swing_highs": swing_highs,
            "swing_lows": swing_lows,
        }

        logger.info(f"Bias detected: {bias} with {confidence}% confidence")
        return result

    def calculate_atr(self, df, period=14):
        """Calculate Average True Range."""
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()

        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        return atr

    def detect_choch(self, df, swing_highs, swing_lows):
        """Detect Change of Character."""
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {"detected": False, "type": None}

        current_price = df["close"].iloc[-1]
        prev_high = swing_highs[-2]["price"]
        prev_low = swing_lows[-2]["price"]

        # Bearish ChoCH: price breaks below previous swing low in uptrend
        if current_price < prev_low:
            return {"detected": True, "type": "BEARISH"}

        # Bullish ChoCH: price breaks above previous swing high in downtrend
        if current_price > prev_high:
            return {"detected": True, "type": "BULLISH"}

        return {"detected": False, "type": None}
