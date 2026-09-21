from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import logging
import os
import resource
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.account_manager.models import AccountStored
from app.config.loader import LoadedConfig
from app.core.trade_analysis import enrich_trade_analysis
from app.mt5_connection.manager import ConnectionContext, ConnectionManager
from app.mt5_connection.market_fallback import derive_order_book_from_rates, derive_tick_from_rates
from app.mt5_connection.zmq_bridge import parse_imbalance_from_order_book
from app.persistence.models import TradeRecord
from app.risk.service import RiskService
from app.safety_monitor.service import SafetyMonitor
from app.trading_logic.engine import TradingEngine
from app.trading_logic.models import MarketSnapshot, TradeDecision

logger = logging.getLogger(__name__)
_PAIR_ANALYSIS_LOG_REPEAT_INTERVAL = timedelta(seconds=30)


class ConnectionFlowError(RuntimeError):
    pass


class BotOrchestrator:
    def __init__(
        self,
        loaded: LoadedConfig,
        connection: ConnectionManager,
        scraper,
        trading: TradingEngine,
        safety: SafetyMonitor,
        risk: RiskService,
        db,
        bridge,
        alerts,
    ) -> None:
        self.loaded = loaded
        self.config = loaded.config
        self.runtime = loaded.runtime
        self.connection = connection
        self.scraper = scraper
        self.trading = trading
        self.safety = safety
        self.risk = risk
        self.db = db
        self.bridge = bridge
        self.alerts = alerts

        self.active_pairs: List[str] = self._normalize_active_pairs(list(self.config.pairs.active))
        self.available_pairs: List[str] = list(self.config.pairs.available)
        self.current_account: Optional[ConnectionContext] = None
        self.halted = False
        self.halt_reason: Optional[str] = None
        self.started_at = datetime.now(timezone.utc)

        self._trading_task: Optional[asyncio.Task] = None
        self._pair_refresh_task: Optional[asyncio.Task] = None
        self._spread_history: Dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))
        self._imbalance_history: Dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=120))
        self._pair_analysis: Dict[str, Dict[str, Any]] = {}
        self._pair_analysis_log_state: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _normalize_active_pairs(symbols: List[str]) -> List[str]:
        ordered: List[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            pair = str(symbol).upper().strip()
            if not pair or pair in seen:
                continue
            ordered.append(pair)
            seen.add(pair)
            if len(ordered) >= 1:
                break
        return ordered

    async def startup(self) -> None:
        if self.config.startup.initial_delay_seconds > 0:
            await asyncio.sleep(self.config.startup.initial_delay_seconds)

        self.db.initialize()
        if self.config.safety_monitor.enabled and self.config.safety_monitor.independent_process:
            self.safety.start()

        if self.config.startup.validate_config_on_start:
            self.validate_runtime_guards()

    async def shutdown(self) -> None:
        if self._trading_task:
            self._trading_task.cancel()
            self._trading_task = None
        if self._pair_refresh_task:
            self._pair_refresh_task.cancel()
            self._pair_refresh_task = None
        await self.scraper.stop()
        await self.safety.stop()
        await self.connection.disconnect(reason="shutdown")
        self.bridge.stop()

    def validate_runtime_guards(self) -> None:
        if self.config.connection_mode.value != "zmq_ea_bridge":
            raise ConnectionFlowError("This phase only supports connection_mode=zmq_ea_bridge")

    async def _noop_step(self, step: str) -> str:
        # Compatibility no-op for richer phase3+/phase4+ connection plans.
        return f"noop:{step}"

    def _step_handlers(self, ctx: ConnectionContext) -> Dict[str, Callable[[], Awaitable[Any]]]:
        return {
            "validate_credentials": lambda: self.connection.login_account(ctx),
            "start_wine_server": self.connection.start_wine_server,
            "initialize_mt5": self.connection.initialize_mt5,
            "login_account": lambda: self.connection.login_account(ctx),
            "fetch_account_info": self.connection.fetch_account_info,
            "fetch_available_pairs": self._fetch_available_pairs,
            "load_pair_configuration": lambda: self._noop_step("load_pair_configuration"),
            "initialize_indicators": lambda: self._noop_step("initialize_indicators"),
            "detect_current_regime": lambda: self._noop_step("detect_current_regime"),
            "load_ml_models": lambda: self._noop_step("load_ml_models"),
            "calculate_dynamic_thresholds": lambda: self._noop_step("calculate_dynamic_thresholds"),
            "start_data_scrapers": self._start_data_scrapers,
            "enable_trading_logic": self._enable_trading_logic,
            "select_broker": lambda: self._noop_step("select_broker"),
            "optimize_parameters_if_needed": lambda: self._noop_step("optimize_parameters_if_needed"),
            "connect_secondary_brokers": lambda: self._noop_step("connect_secondary_brokers"),
        }

    async def connect_account(self, account: AccountStored) -> Dict[str, Any]:
        if self.halted:
            raise ConnectionFlowError(f"system_halted:{self.halt_reason}")

        ctx = ConnectionContext(
            account_id=account.id,
            login=int(account.login),
            password=account.password,
            server=account.server,
        )
        self.current_account = ctx

        await self.connection.connect(ctx)

        handlers = self._step_handlers(ctx)
        report: List[Dict[str, Any]] = []

        for step_cfg in self.config.connection_flow.steps:
            name = step_cfg.step
            timeout_seconds = max(1, step_cfg.timeout_seconds)
            handler = handlers.get(name)
            if handler is None:
                raise ConnectionFlowError(f"Unsupported connection step: {name}")

            started = datetime.now(timezone.utc)
            try:
                result = await asyncio.wait_for(handler(), timeout=timeout_seconds)
                report.append(
                    {
                        "step": name,
                        "status": "ok",
                        "started_at": started.isoformat(),
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "result": result if isinstance(result, (dict, list, str, int, float, bool)) else str(result),
                    }
                )
            except Exception as exc:
                await self.connection.disconnect(reason=f"connect_step_failed:{name}")
                self.alerts.emit("ERROR", "connection_failed", f"Step {name} failed: {exc}")
                report.append(
                    {
                        "step": name,
                        "status": "error",
                        "started_at": started.isoformat(),
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        "error": str(exc),
                    }
                )
                raise ConnectionFlowError(str({"step": name, "error": str(exc), "report": report})) from exc

        await self.connection.mark_connected()
        self.safety.mark_reconnected()
        self.alerts.emit("INFO", "connection_success", f"Connected account {account.account_name}")
        self.schedule_pair_refresh()

        return {
            "status": "connected",
            "account_id": account.id,
            "flow_report": report,
            "on_success": self.config.connection_flow.on_success,
        }

    async def disconnect(self, reason: str = "manual") -> None:
        await self.connection.disconnect(reason=reason)
        self.safety.mark_disconnect()
        if self.config.trading_logic.pause_on_disconnect:
            self.trading.pause()
        await self.scraper.stop()

    async def _fetch_available_pairs(self) -> List[str]:
        symbols = await self.connection.fetch_available_pairs(full_scan=False)
        self.available_pairs = symbols
        if self.config.pairs.pair_management.auto_restore_on_start and not self.active_pairs:
            self.active_pairs = self._normalize_active_pairs(symbols)
        return symbols

    async def refresh_available_pairs(self, *, full_scan: bool = True) -> List[str]:
        symbols = await self.connection.fetch_available_pairs(full_scan=full_scan)
        if symbols:
            self.available_pairs = symbols
            if self.config.pairs.pair_management.auto_restore_on_start and not self.active_pairs:
                self.active_pairs = self._normalize_active_pairs(symbols)
        return self.available_pairs

    def schedule_pair_refresh(self, *, force: bool = False) -> None:
        if not self.config.pairs.pair_management.fetch_from_mt5_on_connect:
            return
        if self._pair_refresh_task and not self._pair_refresh_task.done():
            if not force:
                return
            self._pair_refresh_task.cancel()
        self._pair_refresh_task = asyncio.create_task(
            self._refresh_available_pairs_background(),
            name="pair-refresh-background",
        )

    async def _refresh_available_pairs_background(self) -> None:
        try:
            discovered = await self.refresh_available_pairs(full_scan=True)
            self.alerts.emit("INFO", "pairs_refreshed", f"Pairs refreshed from MT5 ({len(discovered)})")
        except Exception as exc:
            logger.warning("pair_refresh_background_failed: %s", exc)

    async def _start_data_scrapers(self) -> str:
        should_start = any(
            [
                self.config.data_scraping.tick_data.start_on_connect,
                self.config.data_scraping.order_book.start_on_connect,
                self.config.data_scraping.ohlc_data.start_on_connect,
            ]
        )
        if should_start:
            self.scraper.start()
            return "started"
        return "skipped"

    async def _enable_trading_logic(self) -> str:
        # Auto-start settings only control eager startup; manual start remains available.
        startup_allows = self.config.startup.auto_start_trading
        subsystem_wants = self.config.trading_logic.auto_start_on_connect
        if startup_allows and subsystem_wants:
            self.trading.start()
            self._ensure_trading_task()
            return "started"
        self.trading.stop()
        return "manual_start_required"

    def _ensure_trading_task(self) -> None:
        if self._trading_task and not self._trading_task.done():
            return
        self._trading_task = asyncio.create_task(self._trading_loop(), name="trading-loop")

    async def _load_tick_snapshot(self, symbol: str, *, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        pair = str(symbol).upper()
        cached = None if force_refresh else self.scraper.last_tick.get(pair)
        if isinstance(cached, dict):
            bid = float(cached.get("bid", 0.0) or 0.0)
            ask = float(cached.get("ask", 0.0) or 0.0)
            if bid > 0 and ask > 0:
                return cached

        payload = await asyncio.to_thread(self.bridge.get_tick, pair)
        if payload.get("error"):
            payload = await asyncio.to_thread(derive_tick_from_rates, self.bridge, pair)
            if payload.get("error"):
                logger.warning("tick_snapshot_unavailable symbol=%s err=%s", pair, payload.get("error"))
                return None

        bid = float(payload.get("bid", 0.0) or 0.0)
        ask = float(payload.get("ask", 0.0) or 0.0)
        if bid <= 0 or ask <= 0:
            return None

        pip = 0.01 if "JPY" in pair else 0.0001
        spread = float(payload.get("spread_pips", ((ask - bid) / pip) if pip > 0 else 0.0) or 0.0)
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bid": bid,
            "ask": ask,
            "spread_pips": spread,
            "source": payload.get("source", "bridge"),
            "raw": payload,
        }
        self.scraper.last_tick[pair] = snapshot
        scraper_spread_history = getattr(self.scraper, "spread_history", None)
        if isinstance(scraper_spread_history, dict):
            try:
                scraper_spread_history[pair].append(spread)
            except Exception:
                pass
        return snapshot

    async def _load_order_book_snapshot(self, symbol: str, *, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        pair = str(symbol).upper()
        cached = None if force_refresh else self.scraper.last_order_book.get(pair)
        if isinstance(cached, dict):
            return cached

        levels = max(1, int(self.config.data_scraping.order_book.levels))
        payload = await asyncio.to_thread(self.bridge.get_order_book, pair, levels)
        if payload.get("error"):
            payload = await asyncio.to_thread(
                derive_order_book_from_rates,
                self.bridge,
                pair,
                levels=levels,
            )
            if payload.get("error"):
                logger.warning("order_book_snapshot_unavailable symbol=%s err=%s", pair, payload.get("error"))
                return None

        imbalance = float(payload.get("imbalance", parse_imbalance_from_order_book(payload)) or 0.0)
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "imbalance": imbalance,
            "source": payload.get("source", "bridge"),
            "raw": payload,
        }
        self.scraper.last_order_book[pair] = snapshot
        scraper_imbalance_history = getattr(self.scraper, "imbalance_history", None)
        if isinstance(scraper_imbalance_history, dict):
            try:
                scraper_imbalance_history[pair].append(imbalance)
            except Exception:
                pass
        return snapshot

    def _series_from_scraper(
        self,
        symbol: str,
        *,
        attr: str,
        fallback: Dict[str, deque[float]],
    ) -> List[float]:
        series = getattr(self.scraper, attr, None)
        if isinstance(series, dict):
            values = series.get(symbol)
            if values:
                return [float(item) for item in list(values)]
        return [float(item) for item in list(fallback[symbol])]

    @staticmethod
    def _normalize_positions_payload(payload: Any) -> List[Dict[str, Any]]:
        positions = payload.get("positions", []) if isinstance(payload, dict) else []
        if not isinstance(positions, list):
            return []
        return positions

    def _build_market_snapshot(self, pair: str, tick: Dict[str, Any], order_book: Dict[str, Any]) -> MarketSnapshot:
        spread = float(tick.get("spread_pips", 0.0) or 0.0)
        imbalance = float(order_book.get("imbalance", parse_imbalance_from_order_book(order_book)) or 0.0)
        self._spread_history[pair].append(spread)
        self._imbalance_history[pair].append(imbalance)
        spread_history = self._series_from_scraper(
            pair,
            attr="spread_history",
            fallback=self._spread_history,
        )
        imbalance_history = self._series_from_scraper(
            pair,
            attr="imbalance_history",
            fallback=self._imbalance_history,
        )
        return MarketSnapshot(
            symbol=pair,
            bid=float(tick.get("bid", 0.0) or 0.0),
            ask=float(tick.get("ask", 0.0) or 0.0),
            spread_pips=spread,
            order_book_imbalances=imbalance_history,
            spread_history=spread_history,
        )

    async def _confirm_trade_execution(
        self,
        pair: str,
        *,
        lot_size: float,
        expected_decision: TradeDecision,
    ) -> TradeDecision:
        positions_payload = await asyncio.to_thread(self.bridge.get_positions)
        positions = self._normalize_positions_payload(positions_payload)
        tick = await self._load_tick_snapshot(pair, force_refresh=True)
        order_book = await self._load_order_book_snapshot(pair, force_refresh=True)
        if not tick or not order_book:
            return TradeDecision(False, "latest_market_data_unavailable")

        safety_ok, safety_reason = await self.safety.evaluate_gate(
            symbol=pair,
            positions=positions,
            pending_lot=lot_size,
            connection_status=self.connection.status,
        )
        confirmed = self.trading.evaluate(
            self._build_market_snapshot(pair, tick, order_book),
            positions,
            connection_ok=self.connection.status == "connected",
            safety_ok=safety_ok,
            safety_reason=safety_reason,
            lot_size=lot_size,
        )
        if not confirmed.allowed:
            return confirmed
        if expected_decision.direction and confirmed.direction != expected_decision.direction:
            return TradeDecision(False, f"signal_changed:{confirmed.direction or 'none'}")
        return confirmed

    def _default_pair_analysis(self, pair: str) -> Dict[str, Any]:
        return enrich_trade_analysis(
            {
            "symbol": pair,
            "status": "idle",
            "stage": "pending",
            "reason": "not_analyzed_yet",
            "updated_at": None,
            }
        )

    def _update_pair_analysis(
        self,
        pair: str,
        *,
        status: str,
        stage: str,
        reason: str,
        lot_size: float | None = None,
        safety_ok: bool | None = None,
        safety_reason: str | None = None,
        tick: Dict[str, Any] | None = None,
        order_book: Dict[str, Any] | None = None,
        decision: TradeDecision | None = None,
        trade_result: Dict[str, Any] | None = None,
        trade_id: int | None = None,
    ) -> Dict[str, Any]:
        previous = self._pair_analysis.get(pair, {})
        now = datetime.now(timezone.utc)
        payload: Dict[str, Any] = {
            "symbol": pair,
            "status": status,
            "stage": stage,
            "reason": reason,
            "updated_at": now.isoformat(),
            "connection_status": self.connection.status,
            "trading_running": bool(self.trading.running),
            "trading_paused": bool(self.trading.paused),
        }
        last_opened_trade = previous.get("last_opened_trade")
        if isinstance(last_opened_trade, dict):
            payload["last_opened_trade"] = dict(last_opened_trade)
        if lot_size is not None:
            payload["lot_size"] = float(lot_size)
        if safety_ok is not None:
            payload["safety_ok"] = bool(safety_ok)
        if safety_reason is not None:
            payload["safety_reason"] = safety_reason
        if tick:
            payload["bid"] = float(tick.get("bid", 0.0) or 0.0)
            payload["ask"] = float(tick.get("ask", 0.0) or 0.0)
            payload["spread_pips"] = float(tick.get("spread_pips", 0.0) or 0.0)
        if order_book:
            payload["imbalance"] = float(order_book.get("imbalance", parse_imbalance_from_order_book(order_book)) or 0.0)
        if decision:
            payload["decision"] = {
                "allowed": bool(decision.allowed),
                "reason": decision.reason,
                "direction": decision.direction,
                "lot_size": decision.lot_size,
                "entry_price": decision.entry_price,
                "stop_loss": decision.stop_loss,
                "take_profit": decision.take_profit,
                "metadata": dict(decision.metadata or {}),
            }
        if trade_result:
            payload["trade_result"] = dict(trade_result)
        if trade_id is not None:
            payload["trade_id"] = int(trade_id)
        if status == "opened":
            payload["last_opened_trade"] = {
                "updated_at": payload["updated_at"],
                "reason": reason,
                "trade_id": int(trade_id) if trade_id is not None else None,
                "direction": decision.direction if decision else None,
                "lot_size": float(decision.lot_size) if decision and decision.lot_size is not None else None,
                "entry_price": float(decision.entry_price) if decision and decision.entry_price is not None else None,
                "stop_loss": float(decision.stop_loss) if decision and decision.stop_loss is not None else None,
                "take_profit": float(decision.take_profit) if decision and decision.take_profit is not None else None,
            }
        enriched = enrich_trade_analysis(payload)
        self._pair_analysis[pair] = enriched
        self._log_pair_analysis(pair, enriched, logged_at=now)
        return enriched

    @staticmethod
    def _pair_analysis_log_signature(payload: Dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            str(payload.get("status") or ""),
            str(payload.get("stage") or ""),
            str(payload.get("reason") or ""),
            str(payload.get("safety_reason") or ""),
        )

    def _log_pair_analysis(self, pair: str, payload: Dict[str, Any], *, logged_at: datetime) -> None:
        signature = self._pair_analysis_log_signature(payload)
        previous = self._pair_analysis_log_state.get(pair)
        should_log = previous is None or previous.get("signature") != signature
        if not should_log and previous:
            last_logged_at = previous.get("logged_at")
            if isinstance(last_logged_at, datetime):
                should_log = logged_at - last_logged_at >= _PAIR_ANALYSIS_LOG_REPEAT_INTERVAL
        if not should_log:
            return

        log_level = logging.WARNING if str(payload.get("status")) == "error" else logging.INFO
        logger.log(
            log_level,
            "trade_analysis symbol=%s status=%s stage=%s reason=%s display_reason=%s spread_pips=%s imbalance=%s safety_reason=%s trading_running=%s trading_paused=%s",
            pair,
            payload.get("status"),
            payload.get("stage"),
            payload.get("reason"),
            payload.get("display_reason"),
            payload.get("spread_pips"),
            payload.get("imbalance"),
            payload.get("safety_reason"),
            payload.get("trading_running"),
            payload.get("trading_paused"),
        )
        self._pair_analysis_log_state[pair] = {
            "signature": signature,
            "logged_at": logged_at,
        }

    def trade_analysis_payload(self) -> Dict[str, Any]:
        pairs = {
            pair: dict(self._pair_analysis.get(pair, self._default_pair_analysis(pair)))
            for pair in self.active_pairs
        }
        latest_update = None
        for row in pairs.values():
            updated_at = row.get("updated_at")
            if isinstance(updated_at, str) and (latest_update is None or updated_at > latest_update):
                latest_update = updated_at
        return {
            "pairs": pairs,
            "updated_at": latest_update,
        }

    async def _trading_loop(self) -> None:
        while True:
            if self.trading.running and not self.trading.paused and self.connection.status == "connected" and not self.halted:
                try:
                    await self._run_trading_iteration()
                except Exception as exc:
                    logger.exception("trading_iteration_failed: %s", exc)
            await asyncio.sleep(1)

    async def _run_trading_iteration(self) -> None:
        account = await asyncio.to_thread(self.bridge.get_account_info)
        if account.get("error"):
            self.safety.mark_disconnect()
            return

        balance = float(account.get("balance", 0.0) or 0.0)
        equity = float(account.get("equity", 0.0) or 0.0)
        self.safety.update_account(balance, equity)

        positions_payload = await asyncio.to_thread(self.bridge.get_positions)
        positions = self._normalize_positions_payload(positions_payload)

        for symbol in self.active_pairs:
            pair = str(symbol).upper()
            tick = await self._load_tick_snapshot(pair)
            order_book = await self._load_order_book_snapshot(pair)

            if not tick:
                self._update_pair_analysis(
                    pair,
                    status="rejected",
                    stage="market_data",
                    reason="tick_data_unavailable",
                )
                continue
            if not order_book:
                self._update_pair_analysis(
                    pair,
                    status="rejected",
                    stage="market_data",
                    reason="order_book_data_unavailable",
                    tick=tick,
                )
                continue

            lot_size = self.risk.lot_size(balance)
            safety_ok, safety_reason = await self.safety.evaluate_gate(
                symbol=pair,
                positions=positions,
                pending_lot=lot_size,
                connection_status=self.connection.status,
            )

            snapshot = self._build_market_snapshot(pair, tick, order_book)
            decision = self.trading.evaluate(
                snapshot,
                positions,
                connection_ok=self.connection.status == "connected",
                safety_ok=safety_ok,
                safety_reason=safety_reason,
                lot_size=lot_size,
            )

            if not decision.allowed:
                self._update_pair_analysis(
                    pair,
                    status="rejected",
                    stage="initial_evaluation",
                    reason=decision.reason,
                    lot_size=lot_size,
                    safety_ok=safety_ok,
                    safety_reason=safety_reason,
                    tick=tick,
                    order_book=order_book,
                    decision=decision,
                )
                continue

            confirmed_decision = await self._confirm_trade_execution(
                pair,
                lot_size=lot_size,
                expected_decision=decision,
            )
            if not confirmed_decision.allowed:
                logger.info("trade_execution_recheck_blocked symbol=%s reason=%s", pair, confirmed_decision.reason)
                self._update_pair_analysis(
                    pair,
                    status="rejected",
                    stage="execution_recheck",
                    reason=confirmed_decision.reason,
                    lot_size=lot_size,
                    safety_ok=safety_ok,
                    safety_reason=safety_reason,
                    tick=tick,
                    order_book=order_book,
                    decision=confirmed_decision,
                )
                continue

            trade_result = await asyncio.to_thread(
                self.bridge.execute_order,
                pair,
                str(confirmed_decision.direction),
                float(confirmed_decision.lot_size or lot_size),
                float(confirmed_decision.stop_loss or 0.0),
                float(confirmed_decision.take_profit or 0.0),
            )

            if trade_result.get("error"):
                self.alerts.emit("WARN", "trade_open_failed", str(trade_result.get("error")))
                self._update_pair_analysis(
                    pair,
                    status="error",
                    stage="order_submission",
                    reason=str(trade_result.get("error")),
                    lot_size=float(confirmed_decision.lot_size or lot_size),
                    safety_ok=safety_ok,
                    safety_reason=safety_reason,
                    tick=tick,
                    order_book=order_book,
                    decision=confirmed_decision,
                    trade_result=trade_result,
                )
                continue

            trade_id = self.db.add_trade(
                symbol=pair,
                direction=str(confirmed_decision.direction),
                lot_size=float(confirmed_decision.lot_size or lot_size),
                entry_price=float(confirmed_decision.entry_price or 0.0),
                stop_loss=float(confirmed_decision.stop_loss or 0.0),
                take_profit=float(confirmed_decision.take_profit or 0.0),
                source="phase1_engine",
            )
            self.trading.register_trade_open(pair)
            self._update_pair_analysis(
                pair,
                status="opened",
                stage="order_submission",
                reason="approved",
                lot_size=float(confirmed_decision.lot_size or lot_size),
                safety_ok=safety_ok,
                safety_reason=safety_reason,
                tick=tick,
                order_book=order_book,
                decision=confirmed_decision,
                trade_result=trade_result,
                trade_id=trade_id,
            )
            self.alerts.emit("INFO", "trade_opened", f"Trade opened id={trade_id} symbol={pair}", trade_result)

        await self._enforce_time_exit()

    async def _enforce_time_exit(self) -> None:
        cfg = self.config.trading_logic.exit_rules.time_exit
        if not cfg.enabled:
            return
        max_age = timedelta(minutes=max(1, cfg.max_duration_minutes))
        now = datetime.now(timezone.utc)
        stale: List[TradeRecord] = []
        for row in self.db.list_open_trades():
            opened_at = row.opened_at
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            if now - opened_at >= max_age:
                stale.append(row)

        if not stale:
            return

        await asyncio.to_thread(self.bridge.close_all_positions)
        for row in stale:
            self.db.close_trade(int(row.id), pnl=0.0)
            self.alerts.emit("INFO", "trade_closed", f"Time exit closed trade {row.id}")

    def set_active_pairs(self, symbols: List[str]) -> List[str]:
        self.active_pairs = self._normalize_active_pairs(symbols)
        self._pair_analysis = {
            pair: row
            for pair, row in self._pair_analysis.items()
            if pair in self.active_pairs
        }
        self._pair_analysis_log_state = {
            pair: row
            for pair, row in self._pair_analysis_log_state.items()
            if pair in self.active_pairs
        }
        return self.active_pairs

    def _system_snapshot(self) -> Dict[str, Any]:
        uptime_seconds = max(0, int((datetime.now(timezone.utc) - self.started_at).total_seconds()))
        try:
            load_1m = round(float(os.getloadavg()[0]), 2)
        except (AttributeError, OSError):
            load_1m = None

        try:
            rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            # Linux reports KB, some platforms may report bytes.
            memory_mb = round((rss / (1024.0 * 1024.0)) if rss > 10_000_000 else (rss / 1024.0), 1)
        except Exception:
            memory_mb = None

        return {
            "uptime_seconds": uptime_seconds,
            "load_1m": load_1m,
            "memory_mb": memory_mb,
            "connection_mode": self.config.connection_mode.value,
        }

    def status_payload(self) -> Dict[str, Any]:
        return {
            "connection": self.connection.snapshot(),
            "trading": self.trading.status(),
            "safety": self.safety.snapshot(),
            "active_pairs": self.active_pairs,
            "available_pairs": self.available_pairs,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "trade_analysis": self.trade_analysis_payload(),
            "system": self._system_snapshot(),
        }

    async def trading_start(self) -> Dict[str, Any]:
        if self.halted:
            for pair in self.active_pairs:
                self._update_pair_analysis(
                    pair,
                    status="blocked",
                    stage="trading_start",
                    reason=f"halted:{self.halt_reason or 'halted'}",
                )
            self.trading.stop()
            status = self.trading.status()
            status["analysis"] = f"blocked_halted:{self.halt_reason or 'halted'}"
            status["trade_analysis"] = self.trade_analysis_payload()
            return status

        self.trading.start()
        if self.connection.status == "connected":
            self.scraper.start()

        analysis_status = "skipped"
        if self.connection.status != "connected":
            analysis_status = "skipped_not_connected"
            for pair in self.active_pairs:
                self._update_pair_analysis(
                    pair,
                    status="blocked",
                    stage="trading_start",
                    reason="not_connected",
                )
        elif not self.active_pairs:
            analysis_status = "skipped_no_active_pairs"
        else:
            try:
                await self._run_trading_iteration()
                analysis_status = "triggered"
            except Exception as exc:
                logger.exception("trading_start_iteration_failed: %s", exc)
                analysis_status = f"failed:{exc}"

        self._ensure_trading_task()
        status = self.trading.status()
        status["analysis"] = analysis_status
        status["trade_analysis"] = self.trade_analysis_payload()
        return status

    async def trading_pause(self) -> Dict[str, Any]:
        self.trading.pause()
        return self.trading.status()

    async def trading_resume(self) -> Dict[str, Any]:
        self.trading.resume()
        return self.trading.status()

    async def trading_stop(self) -> Dict[str, Any]:
        self.trading.stop()
        return self.trading.status()

    async def emergency_stop(self, reason: str) -> Dict[str, Any]:
        self.halted = True
        self.halt_reason = reason
        self.safety.emergency_halt(reason)
        self.trading.stop()
        await asyncio.to_thread(self.bridge.close_all_positions)
        await self.disconnect(reason="emergency_stop")
        self.alerts.emit("CRITICAL", "emergency_stop", reason)
        return {"halted": True, "reason": reason}

    async def reset_halt(self, reason: str = "manual_reset") -> Dict[str, Any]:
        was_halted = bool(self.halted or self.safety.state.halted or self.safety.state.emergency_stop)
        self.halted = False
        self.halt_reason = None
        self.trading.stop()
        self.safety.clear_halt(reason)
        self.alerts.emit("WARN", "halt_cleared", reason)
        return {"halted": False, "was_halted": was_halted, "reason": "cleared"}

    async def close_all_positions(self) -> Dict[str, Any]:
        payload = await asyncio.to_thread(self.bridge.close_all_positions)
        return payload
