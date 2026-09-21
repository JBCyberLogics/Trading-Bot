from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
import logging
from time import monotonic
from typing import Any, Callable, Dict, List, Optional

from app.config.schema import AppConfig
from app.mt5_connection.market_fallback import (
    derive_order_book_from_rates,
    derive_tick_from_rates,
    is_unsupported_action_error,
)
from app.mt5_connection.zmq_bridge import ZMQBridge, parse_imbalance_from_order_book
from app.persistence.database import Database

logger = logging.getLogger(__name__)


class DataScraperService:
    def __init__(
        self,
        config: AppConfig,
        db: Database,
        bridge: ZMQBridge,
        symbols_provider: Callable[[], List[str]],
        is_connected: Callable[[], bool],
    ) -> None:
        self.config = config
        self.db = db
        self.bridge = bridge
        self.symbols_provider = symbols_provider
        self.is_connected = is_connected
        self._tasks: List[asyncio.Task] = []
        self._running = False
        self.last_tick: Dict[str, Dict] = {}
        self.last_order_book: Dict[str, Dict] = {}
        self.spread_history: Dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))
        self.imbalance_history: Dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))
        self._tick_action_supported: Optional[bool] = None
        self._order_book_action_supported: Optional[bool] = None
        self._last_tick_fallback_at: Dict[str, float] = {}
        self._last_order_book_fallback_at: Dict[str, float] = {}

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="scraper-tick"),
            asyncio.create_task(self._order_book_loop(), name="scraper-orderbook"),
            asyncio.create_task(self._ohlc_loop(), name="scraper-ohlc"),
            asyncio.create_task(self._account_loop(), name="scraper-account"),
            asyncio.create_task(self._retention_loop(), name="scraper-retention"),
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def _tick_loop(self) -> None:
        cfg = self.config.data_scraping.tick_data
        interval = max(0.05, cfg.interval_ms / 1000.0)
        fallback_min_interval = max(0.8, interval)
        while True:
            await asyncio.sleep(interval)
            if not self._running or not cfg.enabled or not self.is_connected():
                continue
            for symbol in self.symbols_provider():
                try:
                    pair = str(symbol).upper()
                    payload: Dict[str, Any] | None = None
                    if self._tick_action_supported is not False:
                        direct = await asyncio.to_thread(self.bridge.get_tick, pair)
                        if direct.get("error"):
                            if is_unsupported_action_error(str(direct.get("error") or "")):
                                self._tick_action_supported = False
                            payload = None
                        else:
                            self._tick_action_supported = True
                            payload = dict(direct)
                            payload["source"] = "bridge"

                    if payload is None:
                        now = monotonic()
                        last = self._last_tick_fallback_at.get(pair, 0.0)
                        if now - last < fallback_min_interval:
                            continue
                        self._last_tick_fallback_at[pair] = now
                        derived = await asyncio.to_thread(derive_tick_from_rates, self.bridge, pair)
                        if derived.get("error"):
                            continue
                        payload = dict(derived)

                    bid = float(payload.get("bid", 0.0) or 0.0)
                    ask = float(payload.get("ask", 0.0) or 0.0)
                    spread = float(payload.get("spread_pips", abs(ask - bid) * (100 if "JPY" in pair else 10000)) or 0.0)
                    snapshot = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "bid": bid,
                        "ask": ask,
                        "spread_pips": spread,
                        "source": payload.get("source", "bridge"),
                        "raw": payload,
                    }
                    self.last_tick[pair] = snapshot
                    self.spread_history[pair].append(spread)
                    if cfg.store_in_database:
                        self.db.add_tick(pair, bid=bid, ask=ask, spread_pips=spread, raw=payload)
                except Exception as exc:
                    logger.warning("tick scraper error symbol=%s err=%s", symbol, exc)

    async def _order_book_loop(self) -> None:
        cfg = self.config.data_scraping.order_book
        interval = max(0.1, cfg.interval_ms / 1000.0)
        fallback_min_interval = max(1.2, interval)
        while True:
            await asyncio.sleep(interval)
            if not self._running or not cfg.enabled or not self.is_connected():
                continue
            for symbol in self.symbols_provider():
                try:
                    pair = str(symbol).upper()
                    payload: Dict[str, Any] | None = None
                    if self._order_book_action_supported is not False:
                        direct = await asyncio.to_thread(self.bridge.get_order_book, pair, cfg.levels)
                        if direct.get("error"):
                            if is_unsupported_action_error(str(direct.get("error") or "")):
                                self._order_book_action_supported = False
                            payload = None
                        else:
                            self._order_book_action_supported = True
                            payload = dict(direct)
                            payload["source"] = "bridge"

                    if payload is None:
                        now = monotonic()
                        last = self._last_order_book_fallback_at.get(pair, 0.0)
                        if now - last < fallback_min_interval:
                            continue
                        self._last_order_book_fallback_at[pair] = now
                        derived = await asyncio.to_thread(
                            derive_order_book_from_rates,
                            self.bridge,
                            pair,
                            levels=cfg.levels,
                        )
                        if derived.get("error"):
                            continue
                        payload = dict(derived)

                    imbalance = float(payload.get("imbalance", parse_imbalance_from_order_book(payload)) or 0.0)
                    snapshot = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "imbalance": imbalance,
                        "source": payload.get("source", "bridge"),
                        "raw": payload,
                    }
                    self.last_order_book[pair] = snapshot
                    self.imbalance_history[pair].append(imbalance)
                    if cfg.store_in_database:
                        self.db.add_order_book(pair, imbalance=imbalance, levels=payload)
                except Exception as exc:
                    logger.warning("order-book scraper error symbol=%s err=%s", symbol, exc)

    async def _ohlc_loop(self) -> None:
        cfg = self.config.data_scraping.ohlc_data
        interval = max(5, int(cfg.update_interval_seconds))
        while True:
            await asyncio.sleep(interval)
            if not self._running or not self.is_connected():
                continue
            for symbol in self.symbols_provider():
                for timeframe in cfg.timeframes:
                    try:
                        payload = await asyncio.to_thread(self.bridge.get_rates, symbol, timeframe, cfg.bars_to_keep)
                        if payload.get("error"):
                            continue
                        bars = payload.get("bars", [])
                        if isinstance(bars, list) and bars:
                            self.db.add_ohlc_rows(symbol, timeframe, bars)
                    except Exception as exc:
                        logger.warning("ohlc scraper error symbol=%s tf=%s err=%s", symbol, timeframe, exc)

    async def _account_loop(self) -> None:
        cfg = self.config.data_scraping.account_info
        interval = max(1, int(cfg.update_interval_seconds))
        while True:
            await asyncio.sleep(interval)
            if not self._running or not self.is_connected() or not cfg.store_balance_history:
                continue
            try:
                account = await asyncio.to_thread(self.bridge.get_account_info)
                if account.get("error"):
                    continue
                login = str(account.get("login", "unknown"))
                balance = float(account.get("balance", 0.0) or 0.0)
                equity = float(account.get("equity", 0.0) or 0.0)
                margin = float(account.get("margin", 0.0) or 0.0)
                daily_pnl = float(account.get("daily_pnl", equity - balance) or 0.0)
                self.db.add_account_snapshot(login, balance, equity, margin, daily_pnl)
            except Exception as exc:
                logger.warning("account scraper error: %s", exc)

    async def _retention_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                self.db.cleanup_retention(
                    tick_days=self.config.data_scraping.tick_data.retention_days,
                    order_book_days=self.config.data_scraping.order_book.retention_days,
                    ohlc_days=max(self.config.data_scraping.order_book.retention_days, 7),
                )
            except Exception as exc:
                logger.warning("retention cleanup error: %s", exc)
