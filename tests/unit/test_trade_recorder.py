"""Unit tests for trade recording and execution lifecycle."""

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace
import unittest

from src.learning.trade_recorder import TradeRecorder


@dataclass
class FakeSetup:
    symbol: str = "EURUSD"
    direction: str = "LONG"
    entry_price: float = 1.1000
    stop_loss: float = 1.0985
    take_profit_1: float = 1.1020
    take_profit_2: float = 1.1040
    bias: str = "BULLISH"
    confidence: float = 80.0
    poi_type: str = "OB"
    poi_zone: tuple = (1.0990, 1.1010)
    rr_ratio: float = 2.0
    entry_method: str = "LIMIT"


class FakeDB:
    def __init__(self):
        self.rows = {}

    def add_trade(self, trade_data):
        self.rows[trade_data["trade_id"]] = dict(trade_data)
        return len(self.rows)

    def update_trade_exit(self, trade_id, exit_data):
        if trade_id not in self.rows:
            return False
        self.rows[trade_id].update(exit_data)
        return True

    def get_trade_by_trade_id(self, trade_id):
        row = self.rows.get(trade_id)
        if row is None:
            return None
        return SimpleNamespace(to_dict=lambda: dict(row))


class FakeRegimeDetector:
    def tag_trade(self, trade_dict, regime_result):
        tagged = dict(trade_dict)
        tagged["regime_trend"] = regime_result["trend"]["strength"]
        tagged["regime_trend_value"] = regime_result["trend"]["adx"]
        tagged["regime_volatility"] = regime_result["volatility"]["level"]
        tagged["regime_volatility_value"] = regime_result["volatility"]["atr_percentile"]
        tagged["regime_session"] = regime_result["session"]["name"]
        tagged["regime_spread"] = regime_result["spread"]["tier"]
        tagged["regime_spread_value"] = regime_result["spread"]["pips"]
        return tagged


class FakeBridge:
    def __init__(self):
        self.last_order = None

    def execute_trade(self, order):
        self.last_order = order
        return {"action": "EXECUTION", "ticket": 12345, "price": 1.1002, "volume": order.volume}


class TestTradeRecorder(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.bridge = FakeBridge()
        self.regime_detector = FakeRegimeDetector()
        self.recorder = TradeRecorder(self.db, self.regime_detector, self.bridge)
        self.setup = FakeSetup()
        self.regime_result = {
            "trend": {"strength": "STRONG", "adx": 32.0},
            "volatility": {"level": "HIGH", "atr_percentile": 0.9},
            "session": {"name": "LONDON_OPEN"},
            "spread": {"tier": "NORMAL", "pips": 1.2},
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None),
        }

    def test_record_send_and_exit_flow(self):
        trade_id = self.recorder.record_signal(self.setup, self.regime_result)
        self.assertIn(trade_id, self.db.rows)

        execution = self.recorder.send_trade(trade_id, volume=0.02)
        self.assertIsNotNone(execution)
        self.assertEqual(execution["action"], "EXECUTION")

        order = self.bridge.last_order
        self.assertIsNotNone(order)
        self.assertEqual(order.actionType, "BUY")
        self.assertEqual(order.orderType, "LIMIT")
        self.assertAlmostEqual(order.price, self.setup.entry_price, places=6)
        self.assertAlmostEqual(order.stoploss, 150.0, places=6)
        self.assertAlmostEqual(order.takeprofit, 200.0, places=6)

        result = self.recorder.record_exit(trade_id, exit_price=1.1015, exit_reason="TP1_HIT")
        self.assertIsNotNone(result)
        self.assertEqual(result["outcome"], "WIN")
        self.assertNotIn(trade_id, self.recorder.pending_trades)

    def test_jpy_point_size(self):
        setup = FakeSetup(symbol="USDJPY", entry_price=150.123, stop_loss=149.923, take_profit_1=150.423)
        trade_id = self.recorder.record_signal(setup, self.regime_result)
        self.recorder.send_trade(trade_id, volume=0.01)
        order = self.bridge.last_order
        self.assertAlmostEqual(order.stoploss, 200.0, places=6)
        self.assertAlmostEqual(order.takeprofit, 300.0, places=6)


if __name__ == "__main__":
    unittest.main()
