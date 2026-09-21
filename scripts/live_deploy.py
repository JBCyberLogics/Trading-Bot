#!/usr/bin/env python3
"""Direct live deployment runtime with explicit safety gating."""

from __future__ import annotations

import argparse
from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.loader import load_config
from src.core.bias_detector import BiasDetector
from src.core.regime_detector import RegimeDetector
from src.core.state_machine import EntryStateMachine
from src.execution.zmq_bridge import ZMQBridge
from src.learning.bootstrap_validator import BootstrapValidator
from src.learning.champion_vs_challenger import ChampionChallenger
from src.learning.models import TradeDatabase
from src.learning.trade_recorder import TradeRecorder
from src.monitoring.alerts import AlertSystem
from src.monitoring.health import HealthServer
from src.monitoring.logger import setup_logging
from src.monitoring.metrics import MetricsCollector
from src.risk.risk_manager import RiskManager
from src.smc.poi_manager import POIManager

FIXED_RULES_PATH = PROJECT_ROOT / "config" / "fixed_rules.yaml"


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_int_rule(value: Any, default: int) -> int:
    if isinstance(value, int):
        return max(1, value)
    if isinstance(value, float):
        return max(1, int(value))
    text = str(value or "").strip()
    match = re.search(r"(\d+)", text)
    if match:
        try:
            return max(1, int(match.group(1)))
        except ValueError:
            return default
    return default


def _to_float_rule(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _load_fixed_rules() -> Dict[str, Any]:
    if not FIXED_RULES_PATH.exists():
        return {}
    try:
        payload = yaml.safe_load(FIXED_RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_user_selected_symbol() -> str | None:
    user_cfg_path = PROJECT_ROOT / "config" / "user_config.yaml"
    if not user_cfg_path.exists():
        return None
    try:
        payload = yaml.safe_load(user_cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    symbol = str(payload.get("symbol", "")).upper().strip()
    return symbol or None


class LiveDeploymentBot:
    """Single-symbol H1 live loop with hard risk gating and status snapshots."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        symbol: str,
        timeframe: str,
        poll_interval_seconds: int,
        bar_count: int,
        max_loops: int = 0,
    ) -> None:
        self.fixed_rules = _load_fixed_rules()
        self.config = self._apply_fixed_rules(config)
        # Runtime is live-only; keep attributes for compatibility with existing status/dashboard code.
        self.confirm_live = True
        self.dry_run = False
        self.symbol = str(symbol).upper()
        self.requested_timeframe = ZMQBridge._normalize_timeframe(timeframe, default="H1")
        self.timeframe = ZMQBridge._normalize_timeframe(
            self.config.get("deployment", {}).get("default_timeframe", self.requested_timeframe),
            default=self.requested_timeframe,
        )
        self.poll_interval_seconds = max(1, int(poll_interval_seconds))
        self.bar_count = max(120, int(bar_count))
        self.max_loops = max(0, int(max_loops))
        self.bias_timeframe = ZMQBridge._normalize_timeframe(
            self.config.get("strategy", {}).get("bias_timeframe", "H4"),
            default="H4",
        )
        self.entry_method = str(self.config.get("strategy", {}).get("entry_method", "LIMIT")).upper()
        rr_cfg = self.config.get("strategy", {}).get("min_rr", {})
        self.min_rr_1h = _to_float_rule(rr_cfg.get("H1"), 2.0)
        self.min_rr_30m = _to_float_rule(rr_cfg.get("M30"), 1.8)

        logs_subdir = self.config.get("deployment", {}).get("logs_subdir", "live")
        self.logs_dir = PROJECT_ROOT / "logs" / str(logs_subdir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        setup_logging(str(self.logs_dir))

        deployment_cfg = self.config.get("deployment", {})
        bridge_timeout_ms = int(deployment_cfg.get("bridge_timeout_ms", 5000))
        bridge_retries = int(deployment_cfg.get("bridge_request_retries", 1))
        bridge_retry_delay_seconds = float(deployment_cfg.get("bridge_retry_delay_seconds", 0.25))

        self.bridge = ZMQBridge(
            host=self.config.get("mt5", {}).get("zmq_host"),
            port=self.config.get("mt5", {}).get("zmq_python_port"),
            timeout_ms=bridge_timeout_ms,
            request_retries=bridge_retries,
            retry_delay_seconds=bridge_retry_delay_seconds,
        )

        db_path = Path(self.config.get("db_path", "database/trades.db"))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = TradeDatabase(f"sqlite:///{db_path}")

        self.bias_detector = BiasDetector(self.config)
        self.poi_manager = POIManager(self.config)
        self.state_machine = EntryStateMachine(self.config)
        self.regime_detector = RegimeDetector(self.config)
        self.risk_manager = RiskManager(self.config, db_session=self.db)

        self.trade_recorder = TradeRecorder(self.db, self.regime_detector, self.bridge)
        bootstrap_confidence = _to_float_rule(
            self.fixed_rules.get("self_learning", {}).get("bootstrap_confidence", 0.95),
            0.95,
        )
        self.bootstrap_validator = BootstrapValidator(confidence_level=bootstrap_confidence)
        champion_allocation = _to_int_rule(
            self.fixed_rules.get("self_learning", {}).get("champion_allocation", 80),
            80,
        )
        self.champion = ChampionChallenger(
            self.db,
            self.bootstrap_validator,
            config_path=str(PROJECT_ROOT / "config" / "parameters.yaml"),
            champion_allocation=champion_allocation,
        )
        self.metrics = MetricsCollector()
        self.alerts = AlertSystem(self.config)
        self.health_server: HealthServer | None = None
        self.prev_risk_state = self.risk_manager.governor.current_state.value

        self.min_live_lots = float(self.config.get("deployment", {}).get("min_live_lots", 0.01))
        self.max_consecutive_loop_failures = int(deployment_cfg.get("max_consecutive_loop_failures", 5))
        self.consecutive_loop_failures = 0
        self.audit_jsonl = self.logs_dir / "runtime_audit.jsonl"
        self.status_json = self.logs_dir / "status_snapshot.json"

        self.running = True
        self.loop_counter = 0
        self.last_action: Dict[str, Any] = {"status": "INIT"}

        logger.info(
            "LiveDeploymentBot initialized: symbol={} timeframe={} bias_tf={} entry_method={} mode={}",
            self.symbol,
            self.timeframe,
            self.bias_timeframe,
            self.entry_method,
            "LIVE",
        )
        if self.requested_timeframe != self.timeframe:
            logger.warning(
                "Fixed rules enforced timeframe override: requested={} enforced={}",
                self.requested_timeframe,
                self.timeframe,
            )

        monitoring_cfg = self.config.get("monitoring", {})
        try:
            health_port = int(monitoring_cfg.get("health_check_port", 3000))
            self.health_server = HealthServer(self, port=health_port)
            self.health_server.start()
        except Exception as exc:
            logger.error(f"Health server failed to start: {exc}")
            self.health_server = None

    def _apply_fixed_rules(self, config: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(config)
        risk_cfg = dict(merged.get("risk", {}))
        deploy_cfg = dict(merged.get("deployment", {}))
        news_cfg = dict(merged.get("news", {}))
        strategy_cfg = dict(merged.get("strategy", {}))

        layer1 = self.fixed_rules.get("layer1_bias", {}) if isinstance(self.fixed_rules, dict) else {}
        layer2 = self.fixed_rules.get("layer2_structure", {}) if isinstance(self.fixed_rules, dict) else {}
        layer3 = self.fixed_rules.get("layer3_entry", {}) if isinstance(self.fixed_rules, dict) else {}
        risk_rules = self.fixed_rules.get("risk_management", {}) if isinstance(self.fixed_rules, dict) else {}

        bias_tf = str(layer1.get("timeframe", "4H")).upper()
        struct_tf = str(layer2.get("primary_timeframe", deploy_cfg.get("default_timeframe", "H1"))).upper()

        # Hard-enforce the documented strategy timeframes and entry windows.
        deploy_cfg["default_timeframe"] = struct_tf
        strategy_cfg["bias_timeframe"] = bias_tf
        strategy_cfg["entry_method"] = "LIMIT" if "LIMIT" in str(layer3.get("entry_method", "LIMIT")).upper() else "MARKET"
        strategy_cfg["min_rr"] = {
            "H1": _to_float_rule(layer3.get("min_rr_1h", 2.0), 2.0),
            "M30": _to_float_rule(layer3.get("min_rr_30m", 1.8), 1.8),
        }

        merged["sweep_max_age_candles"] = _to_int_rule(layer3.get("sweep_max_age", 5), 5)
        merged["displacement_required_candles"] = _to_int_rule(layer3.get("displacement_required", 3), 3)
        merged["retrace_max_candles"] = _to_int_rule(layer3.get("retrace_max_candles", 8), 8)
        merged["swing_confirmation_candles"] = _to_int_rule(layer1.get("swing_confirmation", 2), 2)

        # Hard-enforce risk guardrails from fixed rules.
        if risk_rules:
            risk_cfg["base_risk_percent"] = _to_float_rule(risk_rules.get("base_risk_percent", risk_cfg.get("base_risk_percent", 1.0)), 1.0)
            risk_cfg["max_concurrent_positions"] = _to_int_rule(risk_rules.get("max_concurrent_positions", risk_cfg.get("max_concurrent_positions", 3)), 3)
            risk_cfg["max_daily_loss_pct"] = _to_float_rule(risk_rules.get("max_daily_loss_pct", risk_cfg.get("max_daily_loss_pct", 3.0)), 3.0)
            risk_cfg["max_weekly_loss_pct"] = _to_float_rule(risk_rules.get("max_weekly_loss_pct", risk_cfg.get("max_weekly_loss_pct", 6.0)), 6.0)
            risk_cfg["max_drawdown_pct"] = _to_float_rule(risk_rules.get("max_drawdown_pct", risk_cfg.get("max_drawdown_pct", 8.0)), 8.0)
            news_cfg["buffer_minutes"] = _to_int_rule(
                risk_rules.get("news_filter_minutes", news_cfg.get("buffer_minutes", 15)),
                15,
            )

        merged["risk"] = risk_cfg
        merged["deployment"] = deploy_cfg
        merged["news"] = news_cfg
        merged["strategy"] = strategy_cfg
        return merged

    def _positions_list(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            positions = payload.get("positions")
            return positions if isinstance(positions, list) else []
        if isinstance(payload, list):
            return payload
        return []

    def _fetch_rates(self, timeframe: Optional[str] = None) -> pd.DataFrame:
        tf = ZMQBridge._normalize_timeframe(timeframe or self.timeframe, default=self.timeframe)
        rates = self.bridge.get_rates(self.symbol, tf, self.bar_count)
        if rates.get("error"):
            raise RuntimeError(f"RATES failed: {rates}")

        bars = rates.get("bars", [])
        if not bars:
            raise RuntimeError("RATES returned empty bars")

        frame = pd.DataFrame(bars)
        required = {"timestamp", "open", "high", "low", "close"}
        missing = required.difference(frame.columns)
        if missing:
            raise RuntimeError(f"Missing required rate fields: {sorted(missing)}")

        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
        frame = frame.set_index("timestamp").sort_index()
        for col in ["open", "high", "low", "close"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        if frame.empty:
            raise RuntimeError("No usable OHLC rows after normalization")

        if "tick_volume" in frame.columns:
            frame["volume"] = pd.to_numeric(frame["tick_volume"], errors="coerce").fillna(0)
        else:
            frame["volume"] = 0

        return frame

    def _round_number_step(self) -> float:
        symbol = str(self.symbol or "").upper()
        if symbol.startswith(("XAU", "XAG")):
            return 5.0
        if "JPY" in symbol:
            return 0.5
        return 0.005

    def _liquidity_levels(self, bias_result: Dict[str, Any], frame: pd.DataFrame) -> Dict[str, List[float]]:
        highs = [float(x.get("price")) for x in bias_result.get("swing_highs", []) if x.get("price") is not None]
        lows = [float(x.get("price")) for x in bias_result.get("swing_lows", []) if x.get("price") is not None]

        # Fixed rule: include previous-day liquidity levels.
        daily = frame[["high", "low"]].resample("1D").agg({"high": "max", "low": "min"}).dropna()
        if len(daily) >= 2:
            prev_day = daily.iloc[-2]
            highs.append(float(prev_day["high"]))
            lows.append(float(prev_day["low"]))

        # Fixed rule: include round-number liquidity levels around current price.
        close = float(frame["close"].iloc[-1])
        step = self._round_number_step()
        if step > 0:
            base = round(close / step) * step
            for i in range(1, 4):
                highs.append(base + i * step)
                lows.append(base - i * step)

        return {"above": sorted(set(highs))[-8:], "below": sorted(set(lows))[-8:]}

    def _last_candle_dict(self, frame: pd.DataFrame) -> Dict[str, Any]:
        row = frame.iloc[-1]
        return {
            "time": frame.index[-1].to_pydatetime().replace(tzinfo=None),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }

    def _normalize_setup_for_symbol(self, setup: Any) -> Any:
        if setup is None:
            return None
        setattr(setup, "symbol", self.symbol)
        if not hasattr(setup, "direction"):
            setattr(setup, "direction", "LONG")
        confidence = getattr(setup, "confidence", 70.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 70.0
        if confidence <= 1.0:
            confidence *= 100.0
        setattr(setup, "confidence", max(0.0, min(100.0, confidence)))
        setattr(setup, "entry_method", self.entry_method)
        return setup

    def _min_rr_for_timeframe(self) -> float:
        tf = str(self.timeframe).upper()
        if tf in {"M30", "30M"}:
            return self.min_rr_30m
        return self.min_rr_1h

    def _audit(self, payload: Dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("timestamp", _utcnow_naive().isoformat())
        with self.audit_jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    def _write_status(self, extra: Optional[Dict[str, Any]] = None) -> None:
        report = {
            "timestamp": _utcnow_naive().isoformat(),
            "mode": "LIVE",
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "loop": self.loop_counter,
            "last_action": self.last_action,
            "risk": self.risk_manager.get_risk_report(),
            "uptime_seconds": self.metrics.get_uptime(),
        }
        if extra:
            report.update(extra)
        self.status_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    def _hard_stop(self, reason: str) -> None:
        self.running = False
        self.last_action = {"status": "HARD_STOP", "reason": reason}
        self._audit({"event": "hard_stop", "reason": reason})
        self._write_status()
        self.alerts.alert_system_error(reason)
        logger.error("Hard stop triggered: {}", reason)

    @staticmethod
    def _should_hard_stop_on_rejection(reason: str) -> bool:
        """Return True only for terminal risk conditions, not temporary trade blocks."""
        lower_reason = str(reason or "").strip().lower()
        if lower_reason.startswith("risk state:"):
            state = lower_reason.split(":", 1)[1].strip().upper()
            return state == "HALT"
        return "global circuit active" in lower_reason

    @staticmethod
    def _is_transient_loop_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        transient_markers = [
            "timeout waiting for reply",
            "zmq error",
            "bridge request failed",
            "account failed",
            "positions failed",
        ]
        return any(marker in message for marker in transient_markers)

    def run_once(self) -> None:
        account = self.bridge.get_account_info()
        if account.get("error"):
            raise RuntimeError(f"ACCOUNT failed: {account}")

        positions_payload = self.bridge.get_positions()
        positions = self._positions_list(positions_payload)
        self.risk_manager.update_market_data(
            account_balance=float(account.get("balance", 0.0) or 0.0),
            equity=float(account.get("equity", 0.0) or 0.0),
            positions=positions,
            market_data={},
        )
        gov_report = self.risk_manager.governor.get_state_report()
        self.metrics.update_account_metrics(
            {
                "balance": float(account.get("balance", 0.0) or 0.0),
                "equity": float(account.get("equity", 0.0) or 0.0),
                "drawdown": float(gov_report.get("drawdown", 0.0) or 0.0),
                "open_positions": int(gov_report.get("open_positions", 0) or 0),
                "consecutive_losses": int(gov_report.get("consecutive_losses", 0) or 0),
                "risk_state": str(gov_report.get("state", "NORMAL")),
                "live_progress": float(self.risk_manager.governor.live_deployment_trades),
            }
        )
        current_risk_state = self.risk_manager.governor.current_state.value
        if current_risk_state != self.prev_risk_state:
            reasons: List[str] = []
            if self.risk_manager.governor.transition_history:
                reasons = self.risk_manager.governor.transition_history[-1].get("reasons", [])
            self.alerts.alert_risk_state(self.prev_risk_state, current_risk_state, reasons or ["state transition"])
            self.prev_risk_state = current_risk_state

        structure_frame = self._fetch_rates(self.timeframe)
        bias_frame = structure_frame if self.bias_timeframe == self.timeframe else self._fetch_rates(self.bias_timeframe)
        bias = self.bias_detector.determine_bias(bias_frame)
        self.poi_manager.update(structure_frame)
        regime = self.regime_detector.detect_regime(structure_frame)

        candle = self._last_candle_dict(structure_frame)
        setup = self.state_machine.process_candle(
            candle=candle,
            bias=bias.get("bias", "NEUTRAL"),
            pois=self.poi_manager.active_pois,
            liquidity_levels=self._liquidity_levels(bias, structure_frame),
            bias_confidence=bias.get("confidence"),
        )

        active_version = self.champion.select_parameter_version()
        if setup is None:
            self.last_action = {"status": "NO_SETUP"}
            self._audit({"event": "no_setup"})
            self._write_status()
            return

        setup = self._normalize_setup_for_symbol(setup)
        min_rr = self._min_rr_for_timeframe()
        if float(getattr(setup, "rr_ratio", 0.0) or 0.0) < min_rr:
            reason = f"R:R below minimum for {self.timeframe}: {float(setup.rr_ratio):.2f} < {min_rr:.2f}"
            self.last_action = {"status": "REJECTED", "reason": reason}
            self._audit({"event": "rejected", "reason": reason})
            self._write_status()
            logger.warning("Trade rejected by fixed rules: {}", reason)
            return

        market_gate_payload = {
            "symbol": self.symbol,
            "spread_pips": float(structure_frame.iloc[-1].get("spread", 0.0) or 0.0),
            "avg_spread_pips": 1.0,
            "atr_pips": float((structure_frame["high"] - structure_frame["low"]).tail(14).mean() * 10000),
            "avg_atr": float((structure_frame["high"] - structure_frame["low"]).tail(50).mean() * 10000),
        }
        can_trade, reason = self.risk_manager.can_trade_symbol(self.symbol, market_gate_payload)
        if not can_trade:
            self.last_action = {"status": "REJECTED", "reason": reason}
            self._audit({"event": "rejected", "reason": reason})
            self._write_status()

            if self._should_hard_stop_on_rejection(reason):
                self._hard_stop(reason)
            else:
                logger.warning("Trade rejected by risk gate (runtime continues): {}", reason)
            return

        approved = self.risk_manager.approve_trade(setup, positions)
        if not approved:
            reason = "Governor rejected (confidence/correlation/position rules)"
            self.last_action = {"status": "REJECTED", "reason": reason}
            self._audit({"event": "rejected", "reason": reason})
            self._write_status()
            return

        lots = max(self.min_live_lots, float(approved.get("lots", self.min_live_lots)))
        decision = {
            "event": "approved",
            "symbol": self.symbol,
            "direction": approved.get("direction", getattr(setup, "direction", "LONG")),
            "lots": lots,
            "version_id": active_version.version_id if active_version else None,
        }
        signal_payload = dict(approved)
        signal_payload["version_id"] = decision["version_id"]
        self.alerts.alert_signal(signal_payload, mode="LIVE-READY")

        trade_id = self.trade_recorder.record_signal(setup, regime)
        if active_version is not None:
            self.champion.record_trade_with_version(trade_id, active_version.version_id)

        feedback = self.trade_recorder.send_trade(trade_id, volume=lots)
        if not feedback:
            self.last_action = {"status": "EXECUTION_FAILED", "trade_id": trade_id}
            self._audit({"event": "execution_failed", "trade_id": trade_id})
            self._write_status()
            return

        self.last_action = {"status": "EXECUTED", "trade_id": trade_id, "feedback": feedback}
        self._audit({"event": "executed", "trade_id": trade_id, "feedback": feedback})
        self.metrics.record_trade(
            {
                "symbol": self.symbol,
                "direction": approved.get("direction", "UNKNOWN"),
                "outcome": "FILLED",
                "latency_ms": feedback.get("latency_ms"),
            }
        )
        self.alerts.alert(
            "INFO",
            f"Trade Executed: {self.symbol}",
            (
                f"Trade ID: {trade_id}\n"
                f"Ticket: {feedback.get('ticket')}\n"
                f"Price: {feedback.get('price')}\n"
                f"Lots: {lots:.2f}"
            ),
            force=True,
        )
        self._write_status()
        logger.info("Live order executed: trade_id={} ticket={}", trade_id, feedback.get("ticket"))

    def run(self) -> int:
        logger.info("Starting runtime loop")
        try:
            while self.running:
                self.loop_counter += 1
                try:
                    self.run_once()
                    self.consecutive_loop_failures = 0
                except Exception as exc:
                    if self._is_transient_loop_error(exc):
                        self.consecutive_loop_failures = 0
                        self.last_action = {
                            "status": "LOOP_WARN",
                            "warning": str(exc),
                            "consecutive_failures": self.consecutive_loop_failures,
                        }
                        self._audit(
                            {
                                "event": "loop_warning",
                                "loop": self.loop_counter,
                                "warning": str(exc),
                            }
                        )
                        self._write_status()
                        logger.warning("Transient runtime warning (continuing): {}", exc)
                        time.sleep(self.poll_interval_seconds)
                        continue

                    self.consecutive_loop_failures += 1
                    self.last_action = {
                        "status": "LOOP_ERROR",
                        "error": str(exc),
                        "consecutive_failures": self.consecutive_loop_failures,
                    }
                    self._audit(
                        {
                            "event": "loop_error",
                            "loop": self.loop_counter,
                            "error": str(exc),
                            "consecutive_failures": self.consecutive_loop_failures,
                        }
                    )
                    self._write_status()
                    logger.exception(
                        "Runtime loop error ({}/{} consecutive failures)",
                        self.consecutive_loop_failures,
                        self.max_consecutive_loop_failures,
                    )
                    if self.consecutive_loop_failures >= self.max_consecutive_loop_failures:
                        self._hard_stop(
                            (
                                f"Exceeded max consecutive runtime failures "
                                f"({self.max_consecutive_loop_failures}): {exc}"
                            )
                        )
                        break
                if self.max_loops and self.loop_counter >= self.max_loops:
                    logger.info("Max loops reached: {}", self.max_loops)
                    break
                time.sleep(self.poll_interval_seconds)
            return 0 if self.last_action.get("status") != "HARD_STOP" else 2
        except KeyboardInterrupt:
            logger.warning("Interrupted by user")
            return 130
        except Exception as exc:
            self.last_action = {"status": "FATAL", "error": str(exc)}
            self._audit({"event": "fatal", "error": str(exc)})
            self._write_status()
            self.alerts.alert_system_error(str(exc))
            logger.exception("Fatal runtime error")
            return 1
        finally:
            if self.health_server:
                self.health_server.stop()
            self.bridge.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run controlled live deployment runtime")
    parser.add_argument("--symbol", default=None, help="Symbol to trade (default from config)")
    parser.add_argument("--timeframe", default=None, help="Timeframe to trade (default H1)")
    parser.add_argument("--poll-seconds", type=int, default=None, help="Loop interval in seconds")
    parser.add_argument("--bars", type=int, default=300, help="Candles to request per iteration")
    parser.add_argument("--max-loops", type=int, default=0, help="Stop after N loops (0=infinite)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load_config(PROJECT_ROOT / "config")

    deployment_cfg = cfg.get("deployment", {})

    persisted_symbol = _load_user_selected_symbol()
    symbol = (args.symbol or persisted_symbol or deployment_cfg.get("default_symbol") or "EURUSD").upper()
    timeframe = ZMQBridge._normalize_timeframe(args.timeframe or deployment_cfg.get("default_timeframe") or "H1", default="H1")
    poll_interval = args.poll_seconds or int(deployment_cfg.get("poll_interval_seconds", 5))

    bot = LiveDeploymentBot(
        cfg,
        symbol=symbol,
        timeframe=timeframe,
        poll_interval_seconds=poll_interval,
        bar_count=args.bars,
        max_loops=args.max_loops,
    )
    return bot.run()


if __name__ == "__main__":
    raise SystemExit(main())
