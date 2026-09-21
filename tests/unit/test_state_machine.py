"""Unit tests for Entry State Machine."""

import unittest
from datetime import datetime
from src.core.state_machine import EntryStateMachine, TradeState


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.machine = EntryStateMachine()
        self.candle = {
            "time": datetime.now(),
            "open": 1.0850,
            "high": 1.0860,
            "low": 1.0825,
            "close": 1.0855,
        }
        self.liquidity = {
            "above": [1.0870, 1.0880],
            "below": [1.0830, 1.0820],
        }
        self.pois = {"BULLISH": [{"type": "OB", "zone": [1.0845, 1.0855], "strength": 1}]}

    def test_initial_state(self):
        self.assertEqual(self.machine.state, TradeState.IDLE)

    def test_sweep_detection_bullish(self):
        self.machine._check_for_sweep(self.candle, self.liquidity, "BULLISH")
        self.assertEqual(self.machine.state, TradeState.SWEEP_DETECTED)

    def test_reset(self):
        self.machine.state = TradeState.ENTRY_READY
        self.machine.reset()
        self.assertEqual(self.machine.state, TradeState.IDLE)


if __name__ == "__main__":
    unittest.main()
