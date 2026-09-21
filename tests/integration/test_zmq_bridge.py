"""Integration smoke tests for live ZMQ bridge connectivity."""

import time
import unittest

from src.config.env import load_env
from src.execution.zmq_bridge import ZMQBridge


class TestZMQBridgeIntegration(unittest.TestCase):
    RETRYABLE_ERRORS = {"Timeout waiting for reply", "ZMQ error"}
    REQUEST_ATTEMPTS = 4
    REQUEST_RETRY_DELAY_SECONDS = 0.35

    def setUp(self):
        load_env()
        self.bridge = ZMQBridge(timeout_ms=3000, request_retries=2, retry_delay_seconds=0.2)

    def tearDown(self):
        self.bridge.stop()

    def _request_with_retry(self, request_fn):
        """Retry transient bridge errors to reduce false negatives in integration smoke tests."""
        response = {}
        for attempt in range(1, self.REQUEST_ATTEMPTS + 1):
            response = request_fn()
            if not isinstance(response, dict):
                if attempt < self.REQUEST_ATTEMPTS:
                    time.sleep(self.REQUEST_RETRY_DELAY_SECONDS)
                continue

            error = str(response.get("error", ""))
            if not error:
                return response

            if error in self.RETRYABLE_ERRORS and attempt < self.REQUEST_ATTEMPTS:
                time.sleep(self.REQUEST_RETRY_DELAY_SECONDS)
                continue

            return response

        return response

    def _assert_no_error(self, response, action_name):
        self.assertIsInstance(response, dict, f"{action_name} returned non-dict response")
        if "error" in response:
            self.fail(
                f"{action_name} failed against live bridge at "
                f"tcp://{self.bridge.host}:{self.bridge.port}: {response}"
            )

    def test_ping(self):
        response = self._request_with_retry(self.bridge.ping)
        self._assert_no_error(response, "PING")
        self.assertEqual(response.get("action"), "PONG")

    def test_account_info_schema(self):
        response = self._request_with_retry(self.bridge.get_account_info)
        self._assert_no_error(response, "ACCOUNT")
        self.assertEqual(response.get("action"), "ACCOUNT")

        for field in ("balance", "equity", "margin", "free_margin"):
            self.assertIn(field, response)
            self.assertIsInstance(response[field], (int, float))

    def test_positions_schema(self):
        response = self._request_with_retry(self.bridge.get_positions)
        self._assert_no_error(response, "POSITIONS")
        self.assertEqual(response.get("action"), "POSITIONS")
        self.assertIn("positions", response)
        self.assertIsInstance(response["positions"], list)

    def test_rates_schema(self):
        response = self._request_with_retry(lambda: self.bridge.get_rates("EURUSD", "H1", 50))
        if response.get("error") == "Unknown action":
            self.skipTest("Live EA does not yet expose RATES action on the connected terminal instance")
        self._assert_no_error(response, "RATES")
        self.assertEqual(response.get("action"), "RATES")
        self.assertEqual(response.get("symbol"), "EURUSD")
        self.assertEqual(response.get("timeframe"), "H1")
        self.assertIn("bars", response)
        self.assertIsInstance(response["bars"], list)
        self.assertGreater(len(response["bars"]), 0)

        first = response["bars"][0]
        for field in ("timestamp", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"):
            self.assertIn(field, first)


if __name__ == "__main__":
    unittest.main()
