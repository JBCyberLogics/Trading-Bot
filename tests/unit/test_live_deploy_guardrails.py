"""Unit tests for live deployment runtime guardrail decisions."""

from __future__ import annotations

import unittest

from scripts.live_deploy import LiveDeploymentBot


class TestLiveDeployHardStopPolicy(unittest.TestCase):
    def test_hard_stop_only_for_terminal_rejections(self):
        self.assertTrue(LiveDeploymentBot._should_hard_stop_on_rejection("Risk state: HALT"))
        self.assertTrue(LiveDeploymentBot._should_hard_stop_on_rejection("Global circuit active"))

        self.assertFalse(LiveDeploymentBot._should_hard_stop_on_rejection("Risk state: CAUTION"))
        self.assertFalse(LiveDeploymentBot._should_hard_stop_on_rejection("Risk state: DEFENSIVE"))
        self.assertFalse(LiveDeploymentBot._should_hard_stop_on_rejection("Symbol circuit active for EURUSD"))
        self.assertFalse(LiveDeploymentBot._should_hard_stop_on_rejection("Spread too high: 5.10"))
        self.assertFalse(LiveDeploymentBot._should_hard_stop_on_rejection("Volatility spike"))
        self.assertFalse(LiveDeploymentBot._should_hard_stop_on_rejection("News event blocking"))

    def test_apply_fixed_rules_enforces_timeframes_and_min_rr(self):
        bot = object.__new__(LiveDeploymentBot)
        bot.fixed_rules = {
            "layer1_bias": {"timeframe": "4H"},
            "layer2_structure": {"primary_timeframe": "H1"},
            "layer3_entry": {"entry_method": "LIMIT at zone midpoint", "min_rr_1h": 2.0, "min_rr_30m": 1.8},
            "risk_management": {"base_risk_percent": 1.0},
        }
        merged = LiveDeploymentBot._apply_fixed_rules(bot, {"deployment": {"default_timeframe": "M30"}})
        self.assertEqual(merged["deployment"]["default_timeframe"], "H1")
        self.assertEqual(merged["strategy"]["bias_timeframe"], "4H")
        self.assertEqual(merged["strategy"]["entry_method"], "LIMIT")
        self.assertEqual(merged["strategy"]["min_rr"]["H1"], 2.0)
        self.assertEqual(merged["strategy"]["min_rr"]["M30"], 1.8)


if __name__ == "__main__":
    unittest.main()
