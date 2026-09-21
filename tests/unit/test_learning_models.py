"""Unit tests for Phase 3 learning models and database access."""

import json
import unittest
from datetime import datetime
from datetime import timezone

from src.learning.models import ParameterVersion
from src.learning.models import TradeDatabase


class TestLearningModels(unittest.TestCase):
    def setUp(self):
        self.db = TradeDatabase("sqlite:///:memory:")

    def test_add_and_update_trade(self):
        trade_id = "TRADE_TEST_1"
        row_id = self.db.add_trade(
            {
                "trade_id": trade_id,
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
                "symbol": "EURUSD",
                "direction": "LONG",
                "entry_price": 1.1000,
                "stop_loss": 1.0980,
                "regime_trend": "STRONG",
                "regime_volatility": "HIGH",
                "regime_session": "LONDON_OPEN",
            }
        )
        self.assertGreater(row_id, 0)

        updated = self.db.update_trade_exit(
            trade_id,
            {
                "exit_price": 1.1015,
                "pnl": 15.0,
                "pnl_pips": 15.0,
                "r_multiple": 0.75,
                "outcome": "WIN",
                "exit_reason": "TP1_HIT",
            },
        )
        self.assertTrue(updated)

        saved = self.db.get_trade_by_trade_id(trade_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.outcome, "WIN")
        self.assertEqual(saved.exit_reason, "TP1_HIT")

        regime_trades = list(self.db.get_trades_by_regime({"trend": "STRONG", "volatility": "HIGH"}))
        self.assertEqual(len(regime_trades), 1)

    def test_champion_parameter_switch(self):
        session = self.db.Session()
        try:
            session.add(
                ParameterVersion(
                    version_id="V1",
                    parameters_json=json.dumps({"risk_per_trade": 0.01}),
                    is_champion=False,
                )
            )
            session.add(
                ParameterVersion(
                    version_id="V2",
                    parameters_json=json.dumps({"risk_per_trade": 0.02}),
                    is_champion=True,
                )
            )
            session.commit()
        finally:
            session.close()

        champion = self.db.get_champion_parameters()
        self.assertEqual(champion["risk_per_trade"], 0.02)

        changed = self.db.set_champion_parameters("V1")
        self.assertTrue(changed)
        champion = self.db.get_champion_parameters()
        self.assertEqual(champion["risk_per_trade"], 0.01)


if __name__ == "__main__":
    unittest.main()
