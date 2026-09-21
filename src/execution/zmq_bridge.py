"""ZeroMQ bridge client for communicating with the MT5 PythonBridgeEA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import Any, Dict, List, Optional
import json
import os
import re
import threading
import time

import zmq
from loguru import logger


@dataclass
class TradeOrder:
    actionType: str
    symbol: str
    volume: float
    orderType: str = "MARKET"
    price: float = 0.0
    stoploss: float = 0.0
    takeprofit: float = 0.0
    comment: str = ""


class ZMQBridge:
    """Simple REQ client for the MT5 ZeroMQ bridge."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        timeout_ms: int = 5000,
        request_retries: int = 1,
        retry_delay_seconds: float = 0.25,
        rates_action_fallbacks: Optional[List[str]] = None,
        context: Optional[zmq.Context] = None,
    ) -> None:
        self.host = host or os.getenv("ZMQ_HOST", "127.0.0.1")
        self.port = int(port or os.getenv("ZMQ_PYTHON_PORT", 5555))
        self.timeout_ms = timeout_ms
        self.request_retries = max(0, int(request_retries))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.rates_action_fallbacks = [str(action).upper() for action in (rates_action_fallbacks or ["GET_RATES", "DATA"])]
        self.endpoint = f"tcp://{self.host}:{self.port}"
        self._context = context or zmq.Context.instance()
        self._lock = threading.RLock()
        self._socket = self._create_socket()
        logger.info(f"ZMQBridge connected to {self.endpoint}")

    def _create_socket(self) -> zmq.Socket:
        socket = self._context.socket(zmq.REQ)
        socket.linger = 0
        socket.rcvtimeo = self.timeout_ms
        socket.sndtimeo = self.timeout_ms
        socket.connect(self.endpoint)
        return socket

    def _reset_socket(self) -> None:
        try:
            self._socket.close(linger=0)
        except Exception:
            pass
        self._socket = self._create_socket()
        logger.warning("ZMQBridge socket reset for {}", self.endpoint)

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            message = json.dumps(payload)
            total_attempts = self.request_retries + 1

            for attempt in range(total_attempts):
                try:
                    self._socket.send_string(message)
                    reply = self._socket.recv_string()
                    try:
                        return json.loads(reply)
                    except json.JSONDecodeError:
                        return {"error": "Invalid JSON response", "raw": reply}
                except zmq.error.Again:
                    # A timed-out REQ socket may remain in a bad send/recv state; always reset it.
                    self._reset_socket()
                    if attempt + 1 >= total_attempts:
                        return {
                            "error": "Timeout waiting for reply",
                            "action": payload.get("action"),
                            "endpoint": self.endpoint,
                            "attempts": total_attempts,
                        }
                    if self.retry_delay_seconds > 0:
                        time.sleep(self.retry_delay_seconds)
                except zmq.ZMQError as exc:
                    # Recover from REQ state-machine errors (e.g. operation cannot be accomplished in current state).
                    self._reset_socket()
                    if attempt + 1 >= total_attempts:
                        return {
                            "error": "ZMQ error",
                            "details": str(exc),
                            "action": payload.get("action"),
                            "attempts": total_attempts,
                        }
                    if self.retry_delay_seconds > 0:
                        time.sleep(self.retry_delay_seconds)

            return {
                "error": "ZMQ error",
                "details": "Unexpected retry loop termination",
                "action": payload.get("action"),
            }

    def ping(self) -> Dict[str, Any]:
        return self._send({"action": "PING"})

    def get_account_info(self) -> Dict[str, Any]:
        return self._send({"action": "ACCOUNT"})

    def get_positions(self) -> Dict[str, Any]:
        return self._send({"action": "POSITIONS"})

    def get_symbols(self) -> Dict[str, Any]:
        """Fetch available symbols from MT5 bridge and normalize response."""
        response = self._send({"action": "SYMBOLS"})
        if self._is_unknown_action(response):
            return {
                "error": "Unknown action",
                "action": "SYMBOLS",
                "details": "EA does not support SYMBOLS action",
                "symbols": [],
            }
        return self._normalize_symbols_response(response)

    @staticmethod
    def _normalize_timeframe(timeframe: Any, default: str = "H1") -> str:
        normalized = str(timeframe or "").upper().strip()
        if not normalized:
            normalized = str(default or "H1").upper().strip() or "H1"

        aliases = {
            "1M": "M1",
            "2M": "M2",
            "3M": "M3",
            "4M": "M4",
            "5M": "M5",
            "6M": "M6",
            "10M": "M10",
            "12M": "M12",
            "15M": "M15",
            "20M": "M20",
            "30M": "M30",
            "1H": "H1",
            "2H": "H2",
            "3H": "H3",
            "4H": "H4",
            "6H": "H6",
            "8H": "H8",
            "12H": "H12",
            "1D": "D1",
            "1W": "W1",
            "1MN": "MN1",
            "1MON": "MN1",
            "MONTHLY": "MN1",
            "DAILY": "D1",
            "WEEKLY": "W1",
        }
        if normalized in aliases:
            return aliases[normalized]

        # Accept plain "60" or "240" minute-style timeframe labels.
        minute_aliases = {
            "1": "M1",
            "2": "M2",
            "3": "M3",
            "4": "M4",
            "5": "M5",
            "6": "M6",
            "10": "M10",
            "12": "M12",
            "15": "M15",
            "20": "M20",
            "30": "M30",
            "60": "H1",
            "120": "H2",
            "180": "H3",
            "240": "H4",
            "360": "H6",
            "480": "H8",
            "720": "H12",
            "1440": "D1",
            "10080": "W1",
            "43200": "MN1",
        }
        if normalized in minute_aliases:
            return minute_aliases[normalized]

        compact = normalized.replace("_", "").replace("-", "").replace(" ", "")
        if compact in aliases:
            return aliases[compact]
        if compact in minute_aliases:
            return minute_aliases[compact]

        # Support forms like H1/M30 that are already MT5-compatible.
        if re.fullmatch(r"(M|H|D|W)\d+", compact):
            return compact
        if re.fullmatch(r"MN\d+", compact):
            return compact

        return str(default or "H1").upper().strip() or "H1"

    def get_rates(self, symbol: str, timeframe: str, count: int) -> Dict[str, Any]:
        """Fetch OHLCV bars from MT5 and normalize payload schema."""
        normalized_symbol = str(symbol or "").upper()
        normalized_timeframe = self._normalize_timeframe(timeframe, default="H1")
        requested_count = max(1, int(count))

        primary_request = {
            "action": "RATES",
            "symbol": normalized_symbol,
            "timeframe": normalized_timeframe,
            "count": requested_count,
        }
        response = self._send(primary_request)

        if self._is_unknown_action(response):
            for fallback_action in self.rates_action_fallbacks:
                fallback_request = dict(primary_request)
                fallback_request["action"] = fallback_action
                fallback_response = self._send(fallback_request)
                if self._is_unknown_action(fallback_response):
                    continue

                normalized = self._normalize_rates_response(
                    response=fallback_response,
                    symbol=normalized_symbol,
                    timeframe=normalized_timeframe,
                    count=requested_count,
                )
                if not normalized.get("error"):
                    normalized["source_action"] = fallback_action
                    logger.warning(
                        "Using legacy rates action {} for {} {}",
                        fallback_action,
                        normalized_symbol,
                        normalized_timeframe,
                    )
                return normalized

        return self._normalize_rates_response(
            response=response,
            symbol=normalized_symbol,
            timeframe=normalized_timeframe,
            count=requested_count,
        )

    @staticmethod
    def _is_unknown_action(response: Any) -> bool:
        if not isinstance(response, dict):
            return False
        return str(response.get("error", "")).strip().lower() == "unknown action"

    @staticmethod
    def _normalize_symbols_response(response: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(response, dict):
            return {
                "error": "Malformed symbols response",
                "details": "Response is not a JSON object",
                "symbols": [],
            }

        if response.get("error"):
            return {
                "error": response.get("error"),
                "details": response.get("details"),
                "symbols": [],
            }

        raw_symbols = response.get("symbols")
        if raw_symbols is None:
            raw_symbols = response.get("items")
        if raw_symbols is None:
            raw_symbols = response.get("data")
        if not isinstance(raw_symbols, list):
            return {
                "error": "Malformed symbols response",
                "details": "Missing symbols array",
                "symbols": [],
            }

        normalized: List[str] = []
        seen = set()
        for item in raw_symbols:
            symbol = str(item or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            normalized.append(symbol)
            seen.add(symbol)

        return {
            "action": "SYMBOLS",
            "count": len(normalized),
            "symbols": normalized,
            "source": response.get("source", "mt5"),
        }

    def execute_trade(self, order: TradeOrder) -> Dict[str, Any]:
        direction = self._map_direction(order.actionType)
        order_type = str(getattr(order, "orderType", "MARKET")).upper()
        payload = {
            "action": "TRADE",
            "symbol": order.symbol,
            "direction": direction,
            "order_type": order_type,
            "volume": float(order.volume),
            "entry_price": float(getattr(order, "price", 0.0) or 0.0),
            "stop_loss": float(order.stoploss) if order.stoploss else 0,
            "take_profit_1": float(order.takeprofit) if order.takeprofit else 0,
            "take_profit_2": 0,
            "comment": order.comment or "",
        }
        return self._send(payload)

    def _map_direction(self, action_type: str) -> str:
        if not action_type:
            return "BUY"
        action = action_type.upper()
        if action in {"ORDER_TYPE_BUY", "BUY", "LONG"}:
            return "BUY"
        if action in {"ORDER_TYPE_SELL", "SELL", "SHORT"}:
            return "SELL"
        return action

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    def _normalize_rates_response(
        self,
        response: Dict[str, Any],
        symbol: str,
        timeframe: str,
        count: int,
    ) -> Dict[str, Any]:
        if not isinstance(response, dict):
            return {
                "error": "Malformed rates response",
                "details": "Response is not a JSON object",
                "symbol": symbol,
                "timeframe": timeframe,
                "count": count,
            }

        if response.get("error"):
            return response

        raw_bars = response.get("bars")
        if raw_bars is None:
            raw_bars = response.get("rates")
        if raw_bars is None:
            raw_bars = response.get("data")
        if not isinstance(raw_bars, list):
            return {
                "error": "Malformed rates response",
                "details": "Missing bars array",
                "symbol": symbol,
                "timeframe": timeframe,
                "count": count,
            }

        bars: List[Dict[str, Any]] = []
        for row in raw_bars:
            if not isinstance(row, dict):
                continue

            timestamp = row.get("timestamp", row.get("time"))
            ts_int = self._to_int(timestamp, default=0)
            if ts_int <= 0:
                continue

            bar = {
                "timestamp": ts_int,
                "time": datetime.fromtimestamp(ts_int, tz=timezone.utc).isoformat(),
                "open": self._to_float(row.get("open"), 0.0),
                "high": self._to_float(row.get("high"), 0.0),
                "low": self._to_float(row.get("low"), 0.0),
                "close": self._to_float(row.get("close"), 0.0),
                "tick_volume": self._to_int(row.get("tick_volume"), 0),
                "spread": self._to_int(row.get("spread"), 0),
                "real_volume": self._to_int(row.get("real_volume"), 0),
            }
            if bar["high"] <= 0 or bar["low"] <= 0 or bar["close"] <= 0:
                continue
            bars.append(bar)

        bars.sort(key=lambda x: x["timestamp"])
        if not bars:
            return {
                "error": "Empty rates payload",
                "details": "No valid bars returned",
                "symbol": symbol,
                "timeframe": timeframe,
                "count": count,
            }

        return {
            "action": "RATES",
            "symbol": response.get("symbol", symbol),
            "timeframe": response.get("timeframe", timeframe),
            "count": len(bars),
            "bars": bars,
        }

    def stop(self) -> None:
        with self._lock:
            try:
                self._socket.close(linger=0)
            finally:
                logger.info("ZMQBridge closed")
