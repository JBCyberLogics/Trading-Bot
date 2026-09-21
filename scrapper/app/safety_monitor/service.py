from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.config.loader import RuntimeSettings
from app.config.schema import AppConfig
from app.persistence.database import Database
from app.safety_monitor.news_provider import EconomicNewsEvent, HttpNewsProvider, NewsProvider, NewsProviderError


@dataclass(slots=True)
class SafetyState:
    trading_blocked: bool = False
    reason: str = "ok"
    halted: bool = False
    emergency_stop: bool = False
    last_heartbeat: Optional[str] = None
    daily_start_balance: float = 0.0
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    current_drawdown_percent: float = 0.0
    last_disconnect_at: Optional[datetime] = None


logger = logging.getLogger(__name__)


class SafetyMonitor:
    def __init__(
        self,
        config: AppConfig,
        runtime: RuntimeSettings,
        db: Database,
        *,
        news_provider: NewsProvider | None = None,
    ) -> None:
        self.config = config
        self.runtime = runtime
        self.db = db
        self.state = SafetyState()
        self._task: Optional[asyncio.Task] = None
        self._active_day: date = datetime.now(timezone.utc).date()
        self._news_provider = news_provider or (
            HttpNewsProvider(runtime.news_provider_url) if runtime.news_provider_url else None
        )
        self._news_events: List[EconomicNewsEvent] = []
        self._news_last_error: Optional[str] = None
        self._news_last_ignored_error: Optional[str] = None
        self._news_refresh_due_at: Optional[datetime] = None
        self._news_refresh_lock = asyncio.Lock()

    def start(self) -> None:
        if not self.config.safety_monitor.enabled:
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._heartbeat_loop(), name="safety-heartbeat")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _heartbeat_loop(self) -> None:
        interval = max(1, self.config.safety_monitor.heartbeat.interval_seconds)
        while True:
            self.state.last_heartbeat = datetime.now(timezone.utc).isoformat()
            await asyncio.sleep(interval)

    def emergency_halt(self, reason: str) -> None:
        self.state.trading_blocked = True
        self.state.halted = True
        self.state.emergency_stop = True
        self.state.reason = reason
        self.db.add_safety_event("CRITICAL", "emergency_halt", reason)

    def clear_halt(self, reason: str = "manual_reset") -> None:
        self.state.halted = False
        self.state.emergency_stop = False
        self.state.trading_blocked = False
        self.state.reason = "ok"
        self.db.add_safety_event("INFO", "halt_cleared", reason)

    def mark_disconnect(self) -> None:
        self.state.last_disconnect_at = datetime.now(timezone.utc)

    def mark_reconnected(self) -> None:
        self.state.last_disconnect_at = None

    def update_account(self, balance: float, equity: float) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._active_day and self.config.safety_monitor.daily_limits.reset_at_midnight:
            self._active_day = today
            self.state.daily_start_balance = float(balance)
            self.state.daily_pnl = 0.0

        if self.state.daily_start_balance <= 0:
            self.state.daily_start_balance = float(balance)

        self.state.daily_pnl = float(equity - self.state.daily_start_balance)

        if equity > self.state.peak_equity:
            self.state.peak_equity = float(equity)
        if self.state.peak_equity > 0:
            self.state.current_drawdown_percent = max(
                0.0,
                ((self.state.peak_equity - equity) / self.state.peak_equity) * 100.0,
            )

    def _daily_limit_hit(self) -> Tuple[bool, str]:
        cfg = self.config.safety_monitor.daily_limits
        if self.state.daily_start_balance <= 0:
            return False, "ok"
        daily_loss_percent = 0.0
        if self.state.daily_pnl < 0:
            daily_loss_percent = abs(self.state.daily_pnl) / self.state.daily_start_balance * 100.0
        if daily_loss_percent >= cfg.max_loss_percent:
            return True, "daily_loss_limit_hit"
        if cfg.max_loss_absolute > 0 and abs(self.state.daily_pnl) >= cfg.max_loss_absolute:
            return True, "daily_loss_absolute_hit"
        return False, "ok"

    def _drawdown_limit_hit(self) -> Tuple[bool, str]:
        cfg = self.config.safety_monitor.drawdown_limits
        if self.state.current_drawdown_percent >= cfg.max_drawdown_percent:
            return True, "drawdown_percent_limit_hit"
        return False, "ok"

    def _news_guard_requires_provider(self) -> bool:
        guard = self.config.safety_monitor.news_guard
        if not guard.enabled:
            return False
        source = str(guard.calendar_source or "").strip().lower()
        return any(token in source for token in ("builtin", "api"))

    def _news_refresh_ttl_seconds(self) -> int:
        guard = self.config.safety_monitor.news_guard
        window = max(guard.minutes_before, guard.minutes_after, 1)
        return max(15, min(60, window * 30))

    async def _ensure_news_cache(self, *, force: bool = False) -> Optional[str]:
        if not self._news_guard_requires_provider():
            return None
        if not self.runtime.news_provider_url:
            self._news_last_error = "news_provider_not_configured"
            return self._news_last_error
        if self._news_provider is None:
            self._news_last_error = "news_provider_not_configured"
            return self._news_last_error

        now = datetime.now(timezone.utc)
        if not force and self._news_refresh_due_at and now < self._news_refresh_due_at:
            return self._news_last_error

        async with self._news_refresh_lock:
            now = datetime.now(timezone.utc)
            if not force and self._news_refresh_due_at and now < self._news_refresh_due_at:
                return self._news_last_error

            try:
                self._news_events = await self._news_provider.fetch_events()
                self._news_last_error = None
                self._news_refresh_due_at = now + timedelta(seconds=self._news_refresh_ttl_seconds())
            except NewsProviderError as exc:
                self._news_last_error = str(exc) or "news_provider_unavailable"
                self._news_refresh_due_at = now + timedelta(seconds=15)
                logger.warning("news_provider_refresh_failed reason=%s", self._news_last_error)
            except Exception:
                self._news_last_error = "news_provider_unavailable"
                self._news_refresh_due_at = now + timedelta(seconds=15)
                logger.exception("news_provider_refresh_failed")
            return self._news_last_error

    @staticmethod
    def _symbol_currencies(symbol: str) -> set[str]:
        letters = "".join(ch for ch in str(symbol).upper() if ch.isalpha())
        if len(letters) >= 6:
            return {letters[:3], letters[3:6]}
        if len(letters) == 3:
            return {letters}
        return set()

    def _event_matches_symbol(self, event: EconomicNewsEvent, symbol: str) -> bool:
        currencies = self._symbol_currencies(symbol)
        if not currencies:
            return False
        event_currencies = set(event.currencies)
        if "ALL" in event_currencies:
            return True
        if not event_currencies:
            return False
        return bool(currencies & event_currencies)

    async def _news_guard_blocked(self, symbol: str) -> Tuple[bool, str]:
        guard = self.config.safety_monitor.news_guard
        if not guard.enabled:
            return False, "ok"
        if not self._news_guard_requires_provider():
            return False, "ok"

        error = await self._ensure_news_cache()
        if error:
            # Missing or degraded news-provider configuration should not hard-block trading.
            if self._news_last_ignored_error != error:
                self._news_last_ignored_error = error
                self.db.add_safety_event("WARN", "news_guard_fail_open", error)
                logger.info("news_guard_fail_open reason=%s", error)
            return False, "ok"

        self._news_last_ignored_error = None

        now = datetime.now(timezone.utc)
        window_before = timedelta(minutes=max(0, guard.minutes_before))
        window_after = timedelta(minutes=max(0, guard.minutes_after))
        for event in self._news_events:
            if guard.high_impact_only and event.impact != "high":
                continue
            if not self._event_matches_symbol(event, symbol):
                continue
            if event.timestamp - window_before <= now <= event.timestamp + window_after:
                return True, "news_window_active"
        return False, "ok"

    def _connection_guard_blocked(self, connection_status: str) -> Tuple[bool, str]:
        guard = self.config.safety_monitor.connection_guard
        if connection_status == "connected":
            return False, "ok"
        if self.state.last_disconnect_at is None:
            self.state.last_disconnect_at = datetime.now(timezone.utc)
            return False, "ok"
        delta = (datetime.now(timezone.utc) - self.state.last_disconnect_at).total_seconds()
        if delta >= guard.max_disconnect_seconds:
            return True, "disconnect_timeout"
        return False, "ok"

    def _position_limits_blocked(self, positions: List[Dict[str, Any]], pending_lot: float) -> Tuple[bool, str]:
        limits = self.config.safety_monitor.position_limits
        if pending_lot > limits.max_lot_size_per_trade:
            return True, "lot_size_per_trade_limit"

        total_lot = sum(float(row.get("volume", row.get("lot", 0.0)) or 0.0) for row in positions) + pending_lot
        if total_lot > limits.max_total_lot_size:
            return True, "total_lot_limit"

        return False, "ok"

    def _correlation_blocked(self, symbol: str, positions: List[Dict[str, Any]]) -> Tuple[bool, str]:
        guard = self.config.safety_monitor.correlation_guard
        if not guard.enabled:
            return False, "ok"

        open_symbols = {str(row.get("symbol", "")).upper() for row in positions}
        for pair_a, pair_b in guard.forbidden_pairs:
            a = str(pair_a).upper()
            b = str(pair_b).upper()
            if symbol.upper() == a and b in open_symbols:
                return True, f"correlation_blocked_with_{b}"
            if symbol.upper() == b and a in open_symbols:
                return True, f"correlation_blocked_with_{a}"
        return False, "ok"

    async def evaluate_gate(
        self,
        *,
        symbol: str,
        positions: List[Dict[str, Any]],
        pending_lot: float,
        connection_status: str,
    ) -> Tuple[bool, str]:
        if self.state.halted:
            return False, self.state.reason or "halted"

        checks = [
            self._daily_limit_hit(),
            self._drawdown_limit_hit(),
            await self._news_guard_blocked(symbol),
            self._connection_guard_blocked(connection_status),
            self._position_limits_blocked(positions, pending_lot),
            self._correlation_blocked(symbol, positions),
        ]
        for blocked, reason in checks:
            if blocked:
                self.state.trading_blocked = True
                self.state.reason = reason
                self.db.add_safety_event("WARN", "trade_blocked", reason)
                return False, reason

        self.state.trading_blocked = False
        self.state.reason = "ok"
        return True, "ok"

    def snapshot(self) -> Dict[str, Any]:
        return {
            "trading_blocked": self.state.trading_blocked,
            "reason": self.state.reason,
            "halted": self.state.halted,
            "emergency_stop": self.state.emergency_stop,
            "last_heartbeat": self.state.last_heartbeat,
            "daily_start_balance": self.state.daily_start_balance,
            "daily_pnl": self.state.daily_pnl,
            "peak_equity": self.state.peak_equity,
            "current_drawdown_percent": self.state.current_drawdown_percent,
            "last_disconnect_at": self.state.last_disconnect_at.isoformat() if self.state.last_disconnect_at else None,
        }
