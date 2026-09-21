from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Iterable, List

from app.config.schema import AppConfig
from app.trading_logic.models import MarketSnapshot, TradeDecision


class TradingEngine:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.running = False
        self.paused = False
        self.last_trade_by_pair: Dict[str, datetime] = {}
        self.trade_window_by_pair: Dict[str, Deque[datetime]] = defaultdict(deque)
        self.last_loss_by_pair: Dict[str, datetime] = {}

    def start(self) -> None:
        self.running = True
        self.paused = False

    def stop(self) -> None:
        self.running = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        if self.running:
            self.paused = False

    def status(self) -> Dict[str, bool]:
        return {"running": self.running, "paused": self.paused}

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def _pip_value(self, symbol: str) -> float:
        symbol = symbol.upper()
        if "JPY" in symbol:
            return 0.01
        return 0.0001

    def _within_sessions(self, now_utc: datetime) -> bool:
        hours_cfg = self.config.trading_logic.entry_rules.trading_hours
        if not hours_cfg.enabled:
            return True
        if hours_cfg.weekdays_only and now_utc.weekday() >= 5:
            return False

        london_active = hours_cfg.london.start_hour <= now_utc.hour < hours_cfg.london.end_hour
        ny_active = hours_cfg.newyork.start_hour <= now_utc.hour < hours_cfg.newyork.end_hour
        asian_active = hours_cfg.asian.start_hour <= now_utc.hour < hours_cfg.asian.end_hour

        if hours_cfg.overlap_only:
            return london_active and ny_active

        configured_sessions = {session.lower() for session in hours_cfg.sessions}
        if not configured_sessions:
            return london_active or ny_active or asian_active

        if london_active and ny_active and "london_ny_overlap" in configured_sessions:
            return True
        if london_active and "london" in configured_sessions:
            return True
        if ny_active and "newyork" in configured_sessions:
            return True
        if asian_active and "asian" in configured_sessions:
            return True

        return False

    def _current_session(self, now_utc: datetime) -> str:
        hours_cfg = self.config.trading_logic.entry_rules.trading_hours
        london_active = hours_cfg.london.start_hour <= now_utc.hour < hours_cfg.london.end_hour
        ny_active = hours_cfg.newyork.start_hour <= now_utc.hour < hours_cfg.newyork.end_hour
        asian_active = hours_cfg.asian.start_hour <= now_utc.hour < hours_cfg.asian.end_hour

        if london_active and ny_active:
            return "london_ny_overlap"
        if london_active:
            return "london"
        if ny_active:
            return "newyork"
        if asian_active:
            return "asian"
        return "off_session"

    def _resolve_session_int(
        self,
        value: int | Dict[str, int],
        now_utc: datetime,
        *,
        fallback: int,
        prefer: str,
    ) -> int:
        if isinstance(value, int):
            return value
        if not isinstance(value, dict) or not value:
            return fallback

        session = self._current_session(now_utc)
        for key in (session, "default"):
            raw = value.get(key)
            if isinstance(raw, int):
                return raw

        numeric_values = [item for item in value.values() if isinstance(item, int)]
        if not numeric_values:
            return fallback

        if prefer == "max":
            return max(numeric_values)
        return min(numeric_values)

    @staticmethod
    def _is_increasing(values: Iterable[float]) -> bool:
        values = list(values)
        if len(values) < 2:
            return False
        return all(values[idx] > values[idx - 1] for idx in range(1, len(values)))

    def _weighted_imbalance(self, values: List[float], decay: float) -> float:
        if not values:
            return 0.0
        total_weight = 0.0
        weighted = 0.0
        for idx, value in enumerate(reversed(values), start=0):
            weight = decay**idx
            weighted += value * weight
            total_weight += weight
        if total_weight <= 0:
            return 0.0
        return weighted / total_weight

    def _enforce_frequency_limits(self, symbol: str, now_utc: datetime) -> tuple[bool, str]:
        freq_cfg = self.config.trading_logic.entry_rules.trade_frequency

        min_gap_minutes = self._resolve_session_int(
            freq_cfg.min_time_between_trades_minutes,
            now_utc,
            fallback=60,
            prefer="max",
        )

        last_trade = self.last_trade_by_pair.get(symbol)
        if last_trade is not None:
            min_gap = timedelta(minutes=min_gap_minutes)
            if now_utc - last_trade < min_gap:
                return False, "min_time_between_trades_not_met"

        window = self.trade_window_by_pair[symbol]
        one_hour_ago = now_utc - timedelta(hours=1)
        while window and window[0] < one_hour_ago:
            window.popleft()

        max_trades = self._resolve_session_int(
            freq_cfg.max_trades_per_pair_per_hour,
            now_utc,
            fallback=1,
            prefer="min",
        )
        if len(window) >= max_trades:
            return False, "max_trades_per_hour_reached"

        last_loss = self.last_loss_by_pair.get(symbol)
        if last_loss is not None:
            cooloff_minutes = self._resolve_session_int(
                freq_cfg.cooloff_after_loss_minutes,
                now_utc,
                fallback=30,
                prefer="max",
            )
            cooloff = timedelta(minutes=cooloff_minutes)
            if now_utc - last_loss < cooloff:
                return False, "cooloff_after_loss_active"

        return True, "ok"

    def _enforce_concurrency_limits(
        self,
        symbol: str,
        direction: str,
        positions: List[Dict],
        now_utc: datetime,
    ) -> tuple[bool, str]:
        cfg = self.config.trading_logic.entry_rules.concurrent_positions

        max_total = self._resolve_session_int(cfg.max_total, now_utc, fallback=3, prefer="min")
        if len(positions) >= max_total:
            return False, "max_total_positions_reached"

        same_symbol = [p for p in positions if str(p.get("symbol", "")).upper() == symbol.upper()]
        if len(same_symbol) >= cfg.max_per_pair:
            return False, "max_per_pair_reached"

        max_direction = self._resolve_session_int(
            cfg.max_direction_exposure,
            now_utc,
            fallback=2,
            prefer="min",
        )
        same_direction = [p for p in positions if str(p.get("direction", "")).upper() == direction.upper()]
        if len(same_direction) >= max_direction:
            return False, "max_direction_exposure_reached"

        return True, "ok"

    def evaluate(
        self,
        snapshot: MarketSnapshot,
        positions: List[Dict],
        *,
        connection_ok: bool,
        safety_ok: bool,
        safety_reason: str,
        lot_size: float,
    ) -> TradeDecision:
        now_utc = self._utc_now()

        if not self.running or self.paused:
            return TradeDecision(False, "trading_not_running")

        if self.config.trading_logic.requires_connection and not connection_ok:
            return TradeDecision(False, "connection_required")

        if not safety_ok:
            return TradeDecision(False, f"safety_blocked:{safety_reason}")

        if not self._within_sessions(now_utc):
            return TradeDecision(False, "outside_trading_hours")

        spread_cfg = self.config.trading_logic.entry_rules.spread_requirement
        if snapshot.spread_pips <= 0:
            return TradeDecision(False, "missing_spread_data")
        if snapshot.spread_pips > spread_cfg.max_pips:
            return TradeDecision(False, "spread_too_high")
        if spread_cfg.reject_if_increasing and self._is_increasing(snapshot.spread_history[-3:]):
            return TradeDecision(False, "spread_increasing")

        ob_cfg = self.config.trading_logic.entry_rules.order_book_imbalance
        if ob_cfg.enabled:
            if len(snapshot.order_book_imbalances) < ob_cfg.require_consecutive_readings:
                return TradeDecision(False, "order_book_data_insufficient")
            recent = snapshot.order_book_imbalances[-ob_cfg.require_consecutive_readings :]
            if not all(-1.0 <= value <= 1.0 for value in recent):
                return TradeDecision(False, "invalid_order_book_imbalance")
            if ob_cfg.weighting_enabled:
                imbalance = self._weighted_imbalance(recent, ob_cfg.decay_factor)
            else:
                imbalance = sum(recent) / len(recent)
        else:
            return TradeDecision(False, "order_book_rule_disabled_fail_closed")

        direction = None
        if imbalance >= ob_cfg.min_imbalance_long:
            direction = "LONG"
        elif imbalance <= ob_cfg.min_imbalance_short:
            direction = "SHORT"

        if direction is None:
            return TradeDecision(False, "no_imbalance_signal")

        freq_ok, freq_reason = self._enforce_frequency_limits(snapshot.symbol, now_utc)
        if not freq_ok:
            return TradeDecision(False, freq_reason)

        conc_ok, conc_reason = self._enforce_concurrency_limits(snapshot.symbol, direction, positions, now_utc)
        if not conc_ok:
            return TradeDecision(False, conc_reason)

        entry = snapshot.ask if direction == "LONG" else snapshot.bid
        pip = self._pip_value(snapshot.symbol)
        tp_pips = float(self.config.trading_logic.exit_rules.take_profit.value or 10.0)
        sl_pips = float(self.config.trading_logic.exit_rules.stop_loss.value or 5.0)
        if direction == "LONG":
            stop_loss = entry - sl_pips * pip
            take_profit = entry + tp_pips * pip
        else:
            stop_loss = entry + sl_pips * pip
            take_profit = entry - tp_pips * pip

        return TradeDecision(
            allowed=True,
            reason="approved",
            direction=direction,
            lot_size=float(lot_size),
            entry_price=float(entry),
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            metadata={"imbalance": imbalance, "timestamp": now_utc.isoformat()},
        )

    def register_trade_open(self, symbol: str) -> None:
        now_utc = self._utc_now()
        self.last_trade_by_pair[symbol] = now_utc
        self.trade_window_by_pair[symbol].append(now_utc)

    def register_trade_loss(self, symbol: str) -> None:
        self.last_loss_by_pair[symbol] = self._utc_now()
