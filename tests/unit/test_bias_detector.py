"""Unit tests for BiasDetector."""

import unittest
import pandas as pd
import numpy as np
from src.core.bias_detector import BiasDetector


def _make_zigzag(start, step, amplitude, cycles, up_bars, down_bars):
    values = []
    low = start
    for _ in range(cycles):
        high = low + amplitude
        values.extend(np.linspace(low, high, up_bars, endpoint=False))
        next_low = low + step
        values.extend(np.linspace(high, next_low, down_bars, endpoint=False))
        low = next_low
    return np.array(values)


class TestBiasDetector(unittest.TestCase):
    def setUp(self):
        self.detector = BiasDetector({"swing_lookback": 4, "min_swing_distance_atr": 0.3})

        dates = pd.date_range(start="2024-01-01", periods=200, freq="4h")

        # Rising zigzag for bullish bias
        uptrend = _make_zigzag(start=1.0800, step=0.0010, amplitude=0.0020, cycles=10, up_bars=10, down_bars=10)
        self.uptrend_df = pd.DataFrame(
            {
                "open": uptrend - 0.0002,
                "high": uptrend + 0.0005,
                "low": uptrend - 0.0005,
                "close": uptrend,
            },
            index=dates,
        )

        # Falling zigzag for bearish bias
        downtrend = _make_zigzag(start=1.1000, step=-0.0010, amplitude=0.0020, cycles=10, up_bars=10, down_bars=10)
        self.downtrend_df = pd.DataFrame(
            {
                "open": downtrend + 0.0002,
                "high": downtrend + 0.0005,
                "low": downtrend - 0.0005,
                "close": downtrend,
            },
            index=dates,
        )

    def test_swing_high_detection(self):
        swings = self.detector.find_swing_highs(self.uptrend_df)
        self.assertGreater(len(swings), 0)

    def test_swing_low_detection(self):
        swings = self.detector.find_swing_lows(self.uptrend_df)
        self.assertGreater(len(swings), 0)

    def test_uptrend_bias(self):
        result = self.detector.determine_bias(self.uptrend_df)
        self.assertIn(result["bias"], ["BULLISH", "BULLISH_CAUTION"])

    def test_downtrend_bias(self):
        result = self.detector.determine_bias(self.downtrend_df)
        self.assertIn(result["bias"], ["BEARISH", "BEARISH_CAUTION"])


if __name__ == "__main__":
    unittest.main()
