"""Unit tests for phase 3 performance analytics."""

import unittest
from datetime import datetime, timedelta
from datetime import timezone

from src.learning.models import Trade
from src.learning.models import TradeDatabase
from src.learning.performance_tracker import PerformanceTracker


class TestPerformanceTracker(unittest.TestCase):
    def setUp(self):
        self.db = TradeDatabase("sqlite:///:memory:")
        session = self.db.Session()
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            pnls = [10.0, -5.0, 8.0, -2.0, 6.0, 4.0]
            for idx, pnl in enumerate(pnls):
                session.add(
                    Trade(
                        trade_id=f"T{idx}",
                        timestamp=now - timedelta(minutes=idx * 5),
                        symbol="EURUSD",
                        direction="LONG",
                        entry_price=1.1000,
                        exit_price=1.1010,
                        stop_loss=1.0980,
                        take_profit_1=1.1020,
                        lots=0.01,
                        pnl=pnl,
                        pnl_pips=pnl,
                        r_multiple=pnl / 10.0,
                        outcome="WIN" if pnl > 0 else "LOSS",
                        exit_reason="TP1_HIT" if pnl > 0 else "SL_HIT",
                        poi_type="OB",
                        rr_ratio=2.0,
                        regime_trend="STRONG",
                        regime_trend_value=30.0,
                        regime_volatility="HIGH",
                        regime_volatility_value=0.9,
                        regime_session="LONDON_OPEN",
                        regime_spread="NORMAL",
                        regime_spread_value=1.0,
                        version_id="V1",
                    )
                )
            session.commit()
        finally:
            session.close()

    def test_regime_and_poi_performance(self):
        tracker = PerformanceTracker(self.db)
        regime = tracker.get_regime_performance(days=30)
        key = "STRONG_HIGH_LONDON_OPEN"
        self.assertIn(key, regime)
        self.assertEqual(regime[key]["trades"], 6)
        self.assertAlmostEqual(regime[key]["win_rate"], 4 / 6, places=6)

        poi = tracker.get_poi_performance(days=30)
        self.assertIn("OB", poi)
        self.assertEqual(poi["OB"]["trades"], 6)

    def test_daily_summary_creation(self):
        tracker = PerformanceTracker(self.db)
        summary = tracker.update_daily_summary()
        self.assertIsNotNone(summary)
        self.assertEqual(summary.total_trades, 6)
        self.assertEqual(summary.winning_trades, 4)


if __name__ == "__main__":
    unittest.main()
