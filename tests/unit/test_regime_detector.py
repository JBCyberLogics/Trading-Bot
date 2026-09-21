"""Unit tests for regime detection and trade tagging."""

import unittest

import numpy as np
import pandas as pd

from src.core.regime_detector import RegimeDetector


class TestRegimeDetector(unittest.TestCase):
    def setUp(self):
        self.detector = RegimeDetector()
        index = pd.date_range("2026-01-01", periods=80, freq="h")
        base = 1.1000 + np.linspace(0, 0.0040, len(index))
        self.df = pd.DataFrame(
            {
                "open": base - 0.0002,
                "high": base + 0.0004,
                "low": base - 0.0004,
                "close": base,
            },
            index=index,
        )

    def test_detect_regime_shape(self):
        result = self.detector.detect_regime(self.df)
        self.assertIn("trend", result)
        self.assertIn("volatility", result)
        self.assertIn("session", result)
        self.assertIn("spread", result)
        self.assertIn(result["trend"]["strength"], {"STRONG", "MODERATE", "WEAK"})
        self.assertGreaterEqual(result["volatility"]["atr_percentile"], 0.0)
        self.assertLessEqual(result["volatility"]["atr_percentile"], 1.0)

    def test_tag_trade(self):
        regime = self.detector.detect_regime(self.df)
        trade = {
            "trade_id": "T1",
            "symbol": "EURUSD",
            "direction": "LONG",
            "entry_price": 1.1,
            "timestamp": self.df.index[-1].to_pydatetime(),
        }
        tagged = self.detector.tag_trade(trade, regime)
        self.assertIn("regime_trend", tagged)
        self.assertIn("regime_volatility", tagged)
        self.assertIn("regime_session", tagged)
        self.assertIn("regime_spread", tagged)


if __name__ == "__main__":
    unittest.main()
