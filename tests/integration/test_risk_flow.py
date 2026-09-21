"""Integration tests for end-to-end risk approval and state transitions."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.risk.risk_manager import RiskManager


class TestRiskFlow(unittest.TestCase):
    def setUp(self):
        self.config = {
            "risk": {
                "max_daily_loss_pct": 3.0,
                "max_weekly_loss_pct": 6.0,
                "max_drawdown_pct": 8.0,
                "max_consecutive_losses": 5,
                "max_concurrent_positions": 3,
                "max_correlation": 0.7,
                "base_risk_percent": 1.0,
                "live_deployment_multiplier": 0.1,
                "live_deployment_target": 20,
            },
            "deployment": {"min_live_lots": 0.01},
            "news": {
                "api_key": "key",
                "endpoint": "https://example.com/news",
                "hard_block_without_api": True,
                "buffer_minutes": 15,
            },
            "news_api_key": "key",
        }
        self.rm = RiskManager(self.config)
        self.rm.update_market_data(account_balance=10_000, equity=10_000, positions=[], market_data={})

    @staticmethod
    def _setup() -> SimpleNamespace:
        return SimpleNamespace(
            symbol="EURUSD",
            direction="LONG",
            entry_price=1.1000,
            stop_loss=1.0985,
            take_profit_1=1.1020,
            take_profit_2=1.1040,
            confidence=0.9,
            regime_multiplier=1.0,
            spread_pips=0.8,
            avg_spread_pips=0.8,
            atr_pips=10,
            avg_atr=10,
            poi_type="OB",
            poi_zone=[1.0990, 1.1010],
            bias="BULLISH",
            rr_ratio=1.5,
        )

    def test_full_approval_pipeline(self):
        setup = self._setup()
        with patch.object(self.rm.news_filter, "should_block_trade", return_value=False):
            approved = self.rm.approve_trade(setup, current_positions=[])
        self.assertIsNotNone(approved)
        self.assertGreater(approved["lots"], 0)
        self.assertIn("session_multiplier", approved)
        self.assertIn("correlation_penalty", approved)

    def test_rejection_from_news_and_circuit(self):
        setup = self._setup()

        with patch.object(self.rm.news_filter, "should_block_trade", return_value=True):
            approved = self.rm.approve_trade(setup, current_positions=[])
        self.assertIsNone(approved)

        self.rm.circuits.trigger_global_circuit("manual")
        with patch.object(self.rm.news_filter, "should_block_trade", return_value=False):
            approved = self.rm.approve_trade(setup, current_positions=[])
        self.assertIsNone(approved)

    def test_state_transition_to_halt_blocks_trading(self):
        with patch.object(self.rm.news_filter, "should_block_trade", return_value=False):
            for _ in range(3):
                self.rm.record_trade_outcome("LOSS", symbol="EURUSD")
            self.assertEqual(self.rm.governor.current_state.value, "DEFENSIVE")

            for _ in range(2):
                self.rm.record_trade_outcome("LOSS", symbol="EURUSD")
            self.assertEqual(self.rm.governor.current_state.value, "HALT")

            setup = self._setup()
            approved = self.rm.approve_trade(setup, current_positions=[])
            self.assertIsNone(approved)


if __name__ == "__main__":
    unittest.main()
