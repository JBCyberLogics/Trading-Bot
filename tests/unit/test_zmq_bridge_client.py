"""Unit tests for ZMQ bridge retry logic and rates compatibility fallbacks."""

from __future__ import annotations

import unittest
import threading

import zmq

from src.execution.zmq_bridge import TradeOrder
from src.execution.zmq_bridge import ZMQBridge


def _sample_bar() -> dict:
    return {
        "timestamp": 1700000000,
        "open": 1.1,
        "high": 1.11,
        "low": 1.09,
        "close": 1.105,
        "tick_volume": 123,
        "spread": 10,
        "real_volume": 0,
    }


class _TimeoutSocket:
    def send_string(self, _message: str) -> None:
        return

    def recv_string(self) -> str:
        raise zmq.error.Again()

    def close(self, linger: int = 0) -> None:
        return


class _ReplySocket:
    def __init__(self, response: str):
        self.response = response

    def send_string(self, _message: str) -> None:
        return

    def recv_string(self) -> str:
        return self.response

    def close(self, linger: int = 0) -> None:
        return


class TestZMQBridgeClient(unittest.TestCase):
    def _make_bridge(self) -> ZMQBridge:
        bridge = object.__new__(ZMQBridge)
        bridge.host = "127.0.0.1"
        bridge.port = 5555
        bridge.timeout_ms = 100
        bridge.request_retries = 0
        bridge.retry_delay_seconds = 0.0
        bridge.rates_action_fallbacks = ["GET_RATES", "DATA"]
        bridge.endpoint = "tcp://127.0.0.1:5555"
        bridge._lock = threading.RLock()
        return bridge

    def test_send_retries_and_recovers_after_timeout(self):
        bridge = self._make_bridge()
        bridge.request_retries = 1
        bridge._socket = _TimeoutSocket()
        reset_calls = {"count": 0}

        def fake_reset_socket() -> None:
            reset_calls["count"] += 1
            bridge._socket = _ReplySocket('{"action":"PONG"}')

        bridge._reset_socket = fake_reset_socket  # type: ignore[method-assign]

        response = ZMQBridge._send(bridge, {"action": "PING"})
        self.assertEqual(response.get("action"), "PONG")
        self.assertEqual(reset_calls["count"], 1)

    def test_get_rates_uses_legacy_fallback_action(self):
        bridge = self._make_bridge()
        calls: list[str] = []

        def fake_send(payload: dict) -> dict:
            calls.append(str(payload.get("action")))
            if payload.get("action") == "RATES":
                return {"error": "Unknown action", "action": "RATES"}
            return {"action": "GET_RATES", "bars": [_sample_bar()]}

        bridge._send = fake_send  # type: ignore[method-assign]

        response = bridge.get_rates("EURUSD", "H1", 10)
        self.assertNotIn("error", response)
        self.assertEqual(response.get("action"), "RATES")
        self.assertEqual(response.get("source_action"), "GET_RATES")
        self.assertEqual(calls, ["RATES", "GET_RATES"])

    def test_get_rates_returns_primary_unknown_action_if_all_fallbacks_fail(self):
        bridge = self._make_bridge()
        calls: list[str] = []

        def fake_send(payload: dict) -> dict:
            calls.append(str(payload.get("action")))
            return {"error": "Unknown action", "action": payload.get("action")}

        bridge._send = fake_send  # type: ignore[method-assign]

        response = bridge.get_rates("EURUSD", "H1", 10)
        self.assertEqual(response.get("error"), "Unknown action")
        self.assertEqual(response.get("action"), "RATES")
        self.assertEqual(calls, ["RATES", "GET_RATES", "DATA"])

    def test_get_rates_normalizes_timeframe_aliases_for_request(self):
        bridge = self._make_bridge()
        captured: list[dict] = []

        def fake_send(payload: dict) -> dict:
            captured.append(dict(payload))
            return {"action": "RATES", "bars": [_sample_bar()], "timeframe": payload.get("timeframe")}

        bridge._send = fake_send  # type: ignore[method-assign]

        response_h1 = bridge.get_rates("EURUSD", "1H", 10)
        response_h4 = bridge.get_rates("EURUSD", "4H", 10)

        self.assertNotIn("error", response_h1)
        self.assertNotIn("error", response_h4)
        self.assertEqual(captured[0].get("timeframe"), "H1")
        self.assertEqual(captured[1].get("timeframe"), "H4")

    def test_normalize_rates_response_accepts_data_payload(self):
        bridge = self._make_bridge()
        response = bridge._normalize_rates_response(
            response={"action": "DATA", "data": [_sample_bar()]},
            symbol="EURUSD",
            timeframe="H1",
            count=1,
        )
        self.assertNotIn("error", response)
        self.assertEqual(response.get("count"), 1)

    def test_execute_trade_sends_order_type_and_entry_price(self):
        bridge = self._make_bridge()
        captured: dict = {}

        def fake_send(payload: dict) -> dict:
            captured.update(payload)
            return {"action": "EXECUTION", "ticket": 1, "price": 1.1}

        bridge._send = fake_send  # type: ignore[method-assign]
        order = TradeOrder(
            actionType="BUY",
            symbol="EURUSD",
            volume=0.01,
            orderType="LIMIT",
            price=1.0950,
            stoploss=10.0,
            takeprofit=20.0,
        )
        response = bridge.execute_trade(order)

        self.assertEqual(response.get("action"), "EXECUTION")
        self.assertEqual(captured.get("order_type"), "LIMIT")
        self.assertEqual(captured.get("entry_price"), 1.0950)


if __name__ == "__main__":
    unittest.main()
