from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import subprocess
from typing import Any, Dict, List, Optional

from app.config.schema import AppConfig
from app.mt5_connection.market_fallback import is_unsupported_action_error
from app.mt5_connection.zmq_bridge import ZMQBridge, ZMQBridgeError, retryable_request
from app.persistence.database import Database

logger = logging.getLogger(__name__)

_FAST_SCAN_SYMBOLS: List[str] = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "NZDUSD",
    "USDCAD",
    "USDCHF",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "EURAUD",
    "AUDJPY",
    "CADJPY",
    "CHFJPY",
    "XAUUSD",
    "XAGUSD",
]

_FULL_SCAN_SYMBOLS: List[str] = []
for _base in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"):
    for _quote in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"):
        if _base == _quote:
            continue
        _FULL_SCAN_SYMBOLS.append(f"{_base}{_quote}")
_FULL_SCAN_SYMBOLS.extend(
    [
        "XAUUSD",
        "XAGUSD",
        "XPTUSD",
        "XPDUSD",
        "US30",
        "US500",
        "US100",
        "GER40",
        "UK100",
        "JP225",
    ]
)


@dataclass(slots=True)
class ConnectionContext:
    account_id: str
    login: int
    password: str
    server: str


class ConnectionManager:
    def __init__(self, config: AppConfig, bridge: ZMQBridge, db: Database) -> None:
        self.config = config
        self.bridge = bridge
        self.db = db
        self.status = "disconnected"
        self.current_account: Optional[ConnectionContext] = None
        self.last_connected: Optional[str] = None
        self.connection_history: list[dict[str, Any]] = []
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_lock = asyncio.Lock()
        self._missed_heartbeats = 0
        self._symbols_action_supported: Optional[bool] = None

    def _normalize_login_error(self, reason: str) -> str:
        message = str(reason)
        if "unknown action" in message.lower():
            return (
                "Bridge login command unsupported. "
                f"Ensure the compatible MT5 EA bridge is running on {self.bridge.host}:{self.bridge.port}."
            )
        return message

    @staticmethod
    def _is_unsupported_login_action(reason: str) -> bool:
        message = str(reason).lower()
        return "unknown action" in message or "unsupported" in message and "login" in message

    @staticmethod
    def _is_symbol_missing_error(reason: str | None) -> bool:
        message = str(reason or "").lower()
        return "symbol not found" in message or "invalid symbol" in message

    def snapshot(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "current_account": self.current_account.account_id if self.current_account else None,
            "last_connected": self.last_connected,
            "connection_history": self.connection_history[-100:],
            "missed_heartbeats": self._missed_heartbeats,
        }

    def _record(self, event: str, **details: Any) -> None:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "details": details,
        }
        self.connection_history.append(row)
        self.db.log_connection_event(event, details)

    async def validate_credentials(self, login: int, password: str, server: str) -> tuple[bool, str]:
        retry_attempts = self.config.mt5_connection.terminal_config.login_retry_attempts
        retry_delay = self.config.mt5_connection.terminal_config.login_retry_delay_seconds
        unsupported_login_action = self.config.mt5_connection.terminal_config.unsupported_login_action

        def _do_login() -> Dict[str, Any]:
            return self.bridge.login(login=login, password=password, server=server)

        try:
            payload = await asyncio.to_thread(retryable_request, _do_login, retry_attempts, retry_delay)
        except Exception as exc:
            normalized = self._normalize_login_error(str(exc))
            if self._is_unsupported_login_action(normalized):
                logger.info("bridge_login_unsupported policy=%s", unsupported_login_action)
                if unsupported_login_action == "skip":
                    return True, "skipped_unsupported_login_action"
                return False, normalized
            return False, normalized

        if payload.get("error"):
            normalized = self._normalize_login_error(str(payload.get("error")))
            if self._is_unsupported_login_action(normalized):
                logger.info("bridge_login_unsupported policy=%s", unsupported_login_action)
                if unsupported_login_action == "skip":
                    return True, "skipped_unsupported_login_action"
                return False, normalized
            return False, normalized
        success = bool(payload.get("ok", payload.get("success", True)))
        return success, "ok" if success else self._normalize_login_error(str(payload))

    async def start_wine_server(self) -> None:
        cmd = [self.config.system.wine.wineserver_command, "-p"]
        try:
            await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            # Some bridge deployments do not require local wineserver control.
            logger.info("start_wine_server_skipped: %s", exc)

    async def initialize_mt5(self) -> None:
        payload = await asyncio.to_thread(self.bridge.ping)
        if payload.get("error"):
            raise ZMQBridgeError(str(payload.get("error")))

    async def login_account(self, ctx: ConnectionContext) -> None:
        ok, reason = await self.validate_credentials(ctx.login, ctx.password, ctx.server)
        if not ok:
            raise ZMQBridgeError(f"Login failed: {reason}")

    async def fetch_account_info(self) -> Dict[str, Any]:
        payload = await asyncio.to_thread(self.bridge.get_account_info)
        if payload.get("error"):
            raise ZMQBridgeError(str(payload.get("error")))
        return payload

    async def _probe_symbol_from_rates(self, symbol: str) -> bool:
        payload = await asyncio.to_thread(self.bridge.get_rates, str(symbol).upper(), "M1", 1)
        rows = payload.get("bars")
        if isinstance(rows, list) and rows:
            return True
        error = str(payload.get("error") or "")
        if self._is_symbol_missing_error(error):
            return False
        # Some brokers can fail CopyRates for valid symbols; keep them selectable.
        return bool(error)

    async def _discover_pairs_from_rates(self, *, full_scan: bool) -> list[str]:
        candidates = _FULL_SCAN_SYMBOLS if full_scan else _FAST_SCAN_SYMBOLS
        found: list[str] = []
        for symbol in candidates:
            try:
                if await self._probe_symbol_from_rates(symbol):
                    found.append(symbol)
            except Exception as exc:
                logger.debug("symbol_probe_failed symbol=%s err=%s", symbol, exc)
        return sorted({row.upper() for row in found if str(row).strip()})

    async def fetch_available_pairs(self, *, full_scan: bool = False) -> list[str]:
        if self._symbols_action_supported is not False:
            payload = await asyncio.to_thread(self.bridge.get_symbols)
            symbols = payload.get("symbols")
            if not payload.get("error") and isinstance(symbols, list):
                self._symbols_action_supported = True
                return sorted({str(sym).upper() for sym in symbols if str(sym).strip()})

            if is_unsupported_action_error(str(payload.get("error") or "")):
                self._symbols_action_supported = False
            else:
                logger.warning("bridge_symbols_failed: %s", payload.get("error"))

        discovered = await self._discover_pairs_from_rates(full_scan=full_scan)
        if discovered:
            self._record(
                "symbols_discovered",
                source="rates_probe",
                full_scan=bool(full_scan),
                count=len(discovered),
            )
        return discovered

    async def connect(self, ctx: ConnectionContext) -> None:
        self.status = "connecting"
        self._record("connecting", account_id=ctx.account_id)
        self.current_account = ctx

    async def mark_connected(self) -> None:
        self.status = "connected"
        self.last_connected = datetime.now(timezone.utc).isoformat()
        self._record("connected", account_id=self.current_account.account_id if self.current_account else None)
        self._start_heartbeat()

    async def disconnect(self, reason: str = "manual") -> None:
        self.status = "disconnected"
        self._record("disconnected", reason=reason)
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="mt5-heartbeat")

    async def _heartbeat_loop(self) -> None:
        interval = self.config.mt5_connection.server_config.heartbeat_interval_seconds
        tolerance = self.config.safety_monitor.heartbeat.missed_heartbeats_tolerance

        while True:
            await asyncio.sleep(max(1, interval))
            if self.status != "connected":
                continue
            try:
                payload = await asyncio.to_thread(self.bridge.ping)
                if payload.get("error"):
                    raise ZMQBridgeError(str(payload.get("error")))
                self._missed_heartbeats = 0
            except Exception as exc:
                self._missed_heartbeats += 1
                self._record("heartbeat_missed", count=self._missed_heartbeats, error=str(exc))
                if self._missed_heartbeats > tolerance:
                    self.status = "degraded"
                    self._record("heartbeat_timeout", action=self.config.safety_monitor.heartbeat.action_on_timeout)
                    await self._attempt_reconnect()

    async def _attempt_reconnect(self) -> None:
        async with self._reconnect_lock:
            retries = self.config.mt5_connection.server_config.max_reconnect_attempts
            delay = self.config.mt5_connection.server_config.reconnect_delay_seconds
            for attempt in range(1, retries + 1):
                try:
                    payload = await asyncio.to_thread(self.bridge.ping)
                    if payload.get("error"):
                        raise ZMQBridgeError(str(payload.get("error")))
                    self.status = "connected"
                    self._missed_heartbeats = 0
                    self._record("reconnected", attempt=attempt)
                    return
                except Exception as exc:
                    self._record("reconnect_failed", attempt=attempt, error=str(exc))
                    await asyncio.sleep(max(1, delay))

            self.status = "halted"
            self._record("reconnect_exhausted", action=self.config.safety_monitor.connection_guard.action_on_disconnect)
