"""Unit tests for SMC pattern detectors."""

import unittest
import pandas as pd
import numpy as np
from src.smc.order_blocks import OrderBlockDetector
from src.smc.fair_value_gaps import FVGDetector


class TestSMCPatterns(unittest.TestCase):
    def setUp(self):
        self.ob_detector = OrderBlockDetector()
        self.fvg_detector = FVGDetector()

        # Create synthetic data with known patterns
        dates = pd.date_range(start="2024-01-01", periods=100, freq="1h")
        np.random.seed(42)

        data = {
            "open": np.random.normal(1.0800, 0.001, 100),
            "high": np.random.normal(1.0810, 0.001, 100),
            "low": np.random.normal(1.0790, 0.001, 100),
            "close": np.random.normal(1.0805, 0.001, 100),
        }
        self.df = pd.DataFrame(data, index=dates)

    def test_order_block_detection(self):
        atr = self.ob_detector.calculate_atr(self.df)
        obs = self.ob_detector.find_order_blocks(self.df, atr)
        self.assertIsInstance(obs, list)

    def test_fvg_detection(self):
        fvgs = self.fvg_detector.find_fvgs(self.df)
        self.assertIsInstance(fvgs, list)


if __name__ == "__main__":
    unittest.main()
