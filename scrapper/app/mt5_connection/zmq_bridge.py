from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List

import zmq

logger = logging.getLogger(__name__)


class ZMQBridgeError(RuntimeError):
    pass


class ZMQBridge:
    """REQ/REP bridge client for MT5 EA."""

    def __init__(self, host: str, port: int, timeout_ms: int = 60000) -> None:
        self.host = host
        self.port = int(port)
        self.timeout_ms = int(timeout_ms)
        self.endpoint = f"tcp://{self.host}:{self.port}"
        self._ctx = zmq.Context.instance()
        self._socket = None
        self._lock = threading.Lock()

    def _ensure_socket(self) -> zmq.Socket:
        if self._socket is None:
            sock = self._ctx.socket(zmq.REQ)
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVTIMEO, self.timeout_ms)
            sock.setsockopt(zmq.SNDTIMEO, self.timeout_ms)
            sock.connect(self.endpoint)
            self._socket = sock
        return self._socket

    def _reset_socket(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close(0)
            except Exception:
                pass
            self._socket = None

    def request(self, action: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        message = dict(payload or {})
        message["action"] = action
        encoded = json.dumps(message, ensure_ascii=True).encode("utf-8")

        with self._lock:
            socket = self._ensure_socket()
            try:
                socket.send(encoded)
                reply = socket.recv()
            except zmq.Again as exc:
                self._reset_socket()
                raise ZMQBridgeError(f"Timeout waiting for MT5 reply ({action})") from exc
            except zmq.ZMQError as exc:
                self._reset_socket()
                raise ZMQBridgeError(f"ZMQ error during {action}: {exc}") from exc

        try:
            decoded = json.loads(reply.decode("utf-8"))
        except Exception as exc:
            raise ZMQBridgeError(f"Invalid MT5 response JSON for action {action}") from exc
        if not isinstance(decoded, dict):
            raise ZMQBridgeError(f"Invalid MT5 payload type for action {action}")
        return decoded

    def ping(self) -> Dict[str, Any]:
        return self.request("PING")

    def login(self, login: int, password: str, server: str) -> Dict[str, Any]:
        return self.request("LOGIN", {"login": int(login), "password": password, "server": server})

    def get_account_info(self) -> Dict[str, Any]:
        return self.request("ACCOUNT")

    def get_positions(self) -> Dict[str, Any]:
        return self.request("POSITIONS")

    def get_symbols(self) -> Dict[str, Any]:
        return self.request("SYMBOLS")

    def get_rates(self, symbol: str, timeframe: str, count: int) -> Dict[str, Any]:
        return self.request(
            "RATES",
            {
                "symbol": str(symbol).upper(),
                "timeframe": str(timeframe).upper(),
                "count": int(count),
            },
        )

    def get_tick(self, symbol: str) -> Dict[str, Any]:
        return self.request("TICK", {"symbol": str(symbol).upper()})

    def get_order_book(self, symbol: str, levels: int = 10) -> Dict[str, Any]:
        return self.request("ORDER_BOOK", {"symbol": str(symbol).upper(), "levels": int(levels)})

    def execute_order(
        self,
        symbol: str,
        direction: str,
        lot_size: float,
        stop_loss: float,
        take_profit: float,
    ) -> Dict[str, Any]:
        return self.request(
            "TRADE",
            {
                "symbol": str(symbol).upper(),
                "direction": str(direction).upper(),
                "volume": float(lot_size),
                "sl": float(stop_loss),
                "tp": float(take_profit),
            },
        )

    def close_all_positions(self) -> Dict[str, Any]:
        return self.request("CLOSE_ALL")

    def stop(self) -> None:
        self._reset_socket()
        # Keep shared context alive.


def parse_imbalance_from_order_book(payload: Dict[str, Any]) -> float:
    bids: List[Dict[str, Any]] = payload.get("bids", []) if isinstance(payload.get("bids"), list) else []
    asks: List[Dict[str, Any]] = payload.get("asks", []) if isinstance(payload.get("asks"), list) else []

    bid_vol = sum(float(row.get("volume", 0.0) or 0.0) for row in bids)
    ask_vol = sum(float(row.get("volume", 0.0) or 0.0) for row in asks)
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def retryable_request(func, retries: int, delay_seconds: float) -> Dict[str, Any]:
    last_exc: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            return func()
        except Exception as exc:
            last_exc = exc
            time.sleep(max(0.0, delay_seconds))
    if last_exc is None:
        raise ZMQBridgeError("Unknown bridge retry failure")
    raise last_exc
