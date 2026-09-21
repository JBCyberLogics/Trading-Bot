"""Unit tests for risk governor state machine and sizing."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from src.risk.risk_governor import RiskGovernor
from src.risk.risk_governor import RiskState


class TestRiskGovernor(unittest.TestCase):
    def setUp(self):
        self.governor = RiskGovernor(
            {
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
                }
            }
        )

    def test_initial_state(self):
        self.assertEqual(self.governor.current_state, RiskState.NORMAL)
        self.assertTrue(self.governor.can_trade())

    def test_update_account_sets_anchors_and_drawdown(self):
        self.governor.update_account(10_000, 10_000, [])
        self.assertEqual(self.governor.previous_balance, 10_000)
        self.assertEqual(self.governor.daily_pnl, 0.0)

        self.governor.update_account(9_500, 9_500, [])
        self.assertAlmostEqual(self.governor.current_drawdown, 5.0, places=2)
        self.assertLess(self.governor.daily_pnl, 0)

    def test_daily_loss_halts(self):
        self.governor.daily_start_balance = 10_000
        self.governor.daily_pnl = -400  # 4%
        self.governor._check_state_transitions()
        self.assertEqual(self.governor.current_state, RiskState.HALT)

    def test_weekly_loss_halts(self):
        self.governor.weekly_start_balance = 10_000
        self.governor.weekly_pnl = -700  # 7%
        self.governor._check_state_transitions()
        self.assertEqual(self.governor.current_state, RiskState.HALT)

    def test_drawdown_halts(self):
        self.governor.current_drawdown = 9.0
        self.governor._check_state_transitions()
        self.assertEqual(self.governor.current_state, RiskState.HALT)

    def test_state_progression_from_loss_streak(self):
        self.governor.consecutive_losses = 2
        self.governor._check_state_transitions()
        self.assertEqual(self.governor.current_state, RiskState.CAUTION)

        self.governor.consecutive_losses = 3
        self.governor._check_state_transitions()
        self.assertEqual(self.governor.current_state, RiskState.DEFENSIVE)

        self.governor.consecutive_losses = 5
        self.governor._check_state_transitions()
        self.assertEqual(self.governor.current_state, RiskState.HALT)

    def test_get_position_multiplier_rollout(self):
        self.governor.live_deployment_trades = 0
        self.assertAlmostEqual(self.governor.get_position_size_multiplier(), 0.1, places=2)

        self.governor.live_deployment_trades = 10
        self.assertGreater(self.governor.get_position_size_multiplier(), 0.5)

        self.governor.live_deployment_trades = 20
        self.assertAlmostEqual(self.governor.get_position_size_multiplier(), 1.0, places=2)

        self.governor.current_state = RiskState.CAUTION
        self.assertAlmostEqual(self.governor.get_position_size_multiplier(), 0.5, places=2)

    def test_confidence_threshold_varies_with_state(self):
        self.governor.live_deployment_trades = 0
        self.assertEqual(self.governor.get_confidence_threshold(), 0.7)

        self.governor.live_deployment_trades = 100
        self.governor.current_state = RiskState.NORMAL
        self.assertEqual(self.governor.get_confidence_threshold(), 0.7)

        self.governor.current_state = RiskState.DEFENSIVE
        self.assertEqual(self.governor.get_confidence_threshold(), 0.9)

    def test_confidence_threshold_honors_rollout_floor_override(self):
        governor = RiskGovernor({"risk": {"rollout_confidence_floor": 0.8}})
        governor.live_deployment_trades = 0
        governor.current_state = RiskState.NORMAL
        self.assertEqual(governor.get_confidence_threshold(), 0.8)

    def test_approve_trade_rules(self):
        self.governor.update_account(10_000, 10_000, [])
        setup = SimpleNamespace(confidence=0.9)

        self.assertTrue(self.governor.approve_trade(setup, correlation_score=0.4))
        self.assertFalse(self.governor.approve_trade(SimpleNamespace(confidence=0.1), correlation_score=0.4))
        self.assertFalse(self.governor.approve_trade(setup, correlation_score=0.95))

        self.governor.open_positions = [1, 2, 3]
        self.assertFalse(self.governor.approve_trade(setup, correlation_score=0.4))

        self.governor.current_state = RiskState.HALT
        self.assertFalse(self.governor.approve_trade(setup, correlation_score=0.4))

    def test_calculate_position_size(self):
        self.governor.live_deployment_trades = 20
        lots = self.governor.calculate_position_size(
            account_balance=10_000,
            stop_loss_pips=20,
            pip_value=10,
            confidence=0.8,
            regime_multiplier=1.0,
            min_lot=0.01,
        )
        self.assertGreater(lots, 0)
        self.assertGreaterEqual(lots, 0.01)

        zero = self.governor.calculate_position_size(
            account_balance=10_000,
            stop_loss_pips=0,
            pip_value=10,
            confidence=0.8,
        )
        self.assertEqual(zero, 0.0)

    def test_trade_outcome_tracking(self):
        self.governor.record_trade_outcome("LOSS")
        self.assertEqual(self.governor.consecutive_losses, 1)

        self.governor.record_trade_outcome("WIN")
        self.assertEqual(self.governor.consecutive_losses, 0)
        self.assertEqual(self.governor.live_deployment_trades, 1)

    def test_state_report_schema(self):
        report = self.governor.get_state_report()
        self.assertIn("state", report)
        self.assertIn("position_size_multiplier", report)
        self.assertIn("confidence_threshold", report)
        self.assertIn("live_deployment_progress", report)


if __name__ == "__main__":
    unittest.main()
