"""Health and dashboard API server for Mr. Wanjama monitoring panel."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from typing import Any, Dict, List
from urllib.parse import parse_qs
from urllib.parse import urlparse

from loguru import logger
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_TEMPLATE = Path(__file__).with_name("dashboard.html")
FIXED_RULES_PATH = PROJECT_ROOT / "config" / "fixed_rules.yaml"
USER_CONFIG_PATH = PROJECT_ROOT / "config" / "user_config.yaml"

DEFAULT_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "EURGBP",
    "USDCHF",
    "XAUUSD",
]

MT5_PROBE_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "EURGBP",
    "USDCHF",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
    "EURAUD",
    "GBPAUD",
    "XAUUSD",
    "XAGUSD",
]

try:
    SYMBOLS_CACHE_TTL_SECONDS = max(5.0, float(os.getenv("DASH_SYMBOLS_CACHE_TTL", "30")))
except Exception:
    SYMBOLS_CACHE_TTL_SECONDS = 30.0

try:
    MT5_SYMBOLS_PROBE_TTL_SECONDS = max(30.0, float(os.getenv("MT5_SYMBOLS_PROBE_TTL", "300")))
except Exception:
    MT5_SYMBOLS_PROBE_TTL_SECONDS = 300.0

try:
    MT5_SYMBOLS_PROBE_MAX_CANDIDATES = max(1, int(os.getenv("MT5_SYMBOLS_PROBE_MAX_CANDIDATES", "3")))
except Exception:
    MT5_SYMBOLS_PROBE_MAX_CANDIDATES = 3

MT5_SYMBOLS_PROBE_ENABLED = str(os.getenv("MT5_SYMBOLS_PROBE_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}

DEFAULT_FIXED_RULES: Dict[str, Any] = {
    "description": "These rules are hard-coded and cannot be modified by the user",
    "layer1_bias": {
        "timeframe": "4H",
        "swing_confirmation": "2 candles after",
        "bias_types": ["BULLISH", "BEARISH", "NEUTRAL", "TRANSITION"],
        "choch_detection": True,
    },
    "layer2_structure": {
        "primary_timeframe": "1H",
        "order_blocks": {"definition": "Last opposite candle before impulsive move", "impulse_threshold": "1.5 x ATR", "max_age_hours": 24},
        "fair_value_gaps": {"definition": "Three-candle imbalance", "min_pips": 2.0, "max_age_hours": 12},
        "liquidity_levels": ["swing_points", "previous_day", "round_numbers"],
    },
    "layer3_entry": {
        "state_machine": ["IDLE", "SWEEP", "DISPLACEMENT", "RETRACE", "ENTRY_READY"],
        "sweep_max_age": "5 candles",
        "displacement_required": "within 3 candles",
        "retrace_max_candles": 8,
        "entry_method": "LIMIT at zone midpoint",
        "min_rr_1h": 2.0,
        "min_rr_30m": 1.8,
    },
    "risk_management": {
        "governor_states": ["NORMAL", "CAUTION", "DEFENSIVE", "HALT"],
        "base_risk_percent": 1.0,
        "max_concurrent_positions": 3,
        "max_daily_loss_pct": 3.0,
        "max_weekly_loss_pct": 6.0,
        "max_drawdown_pct": 8.0,
        "news_filter_minutes": 15,
    },
    "self_learning": {
        "champion_allocation": 80,
        "challenger_allocation": 20,
        "bootstrap_confidence": 0.95,
        "weekly_evolution": "Sunday 00:00 UTC",
    },
}

DEFAULT_USER_CONFIG: Dict[str, Any] = {
    "symbol": "EURUSD",
    "lot_size": 0.01,
    "risk_percent": 1.0,
    "mode": "live",
    "strategy_version": "v2.3 (Latest)",
    "notification_preferences": {"telegram": True},
}


def _load_yaml(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else dict(default)
    except Exception:
        return dict(default)


def _save_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


class HealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP handler exposing dashboard UI, status APIs, and bot controls."""

    bot_instance = None
    _symbols_probe_cache: Dict[str, Any] = {"timestamp": 0.0, "symbols": []}
    _symbols_cache: Dict[str, Any] = {"timestamp": 0.0, "symbols": []}
    _mt5_symbols_supported: bool | None = None

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self._send_html(200, self._dashboard_html())
            return

        if path == "/favicon.ico":
            self._send_text(204, "")
            return

        if path in {"/api/health", "/health"}:
            self._send_json(200, {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()})
            return

        if path in {"/api/status", "/api/bot/status"}:
            status_payload = self.get_bot_status()
            self._send_json(200, {"service": "mr-wanjama-trading-bot", "status": status_payload, "endpoints": self._endpoint_urls()})
            return

        if path in {"/api/bot/config"}:
            self._send_json(200, self._user_config())
            return

        if path in {"/api/account"}:
            self._send_json(200, self._account_payload())
            return

        if path in {"/api/positions"}:
            self._send_json(200, {"positions": self._positions_payload()})
            return

        if path in {"/api/risk/state"}:
            self._send_json(200, self._risk_state_payload())
            return

        if path == "/api/symbols":
            self._send_json(200, {"symbols": self._available_symbols()})
            return

        if path == "/api/fixed-rules":
            self._send_json(200, self._fixed_rules())
            return

        if path in {"/api/trades/recent", "/api/audit/recent"}:
            limit = int(query.get("limit", ["50"])[0])
            events = self._recent_audit_events(limit=max(1, min(limit, 200)))
            self._send_json(200, {"items": events, "count": len(events)})
            return

        if path == "/api/performance":
            days = int(query.get("days", ["30"])[0])
            events = self._recent_audit_events(limit=max(50, min(days * 20, 1000)))
            self._send_json(200, self._performance_summary(events))
            return

        if path in {"/api/chart", "/api/chart/candles"}:
            requested_symbol = query.get("symbol", [getattr(self.bot_instance, "symbol", "EURUSD")])[0]
            requested_timeframe = query.get("timeframe", [getattr(self.bot_instance, "timeframe", "H1")])[0]
            try:
                requested_count = int(query.get("count", ["120"])[0])
            except Exception:
                requested_count = 120
            payload = self._market_chart_payload(
                symbol=str(requested_symbol or "EURUSD"),
                timeframe=str(requested_timeframe or "H1"),
                count=requested_count,
            )
            self._send_json(200, payload)
            return

        if path == "/metrics":
            bot = self.bot_instance
            if bot and getattr(bot, "metrics", None):
                metrics_payload = bot.metrics.get_metrics()
                if isinstance(metrics_payload, str):
                    metrics_payload = metrics_payload.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(metrics_payload)))
                self.end_headers()
                self.wfile.write(metrics_payload)
            else:
                self._send_text(503, "metrics unavailable")
            return

        if path == "/ready":
            bot = self.bot_instance
            if bot and getattr(bot, "running", False):
                self._send_text(200, "ready")
            else:
                self._send_text(503, "not ready")
            return

        if path == "/live":
            self._send_text(200, "alive")
            return

        self._send_text(404, "not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        bot = self.bot_instance
        if bot is None:
            self._send_json(503, {"ok": False, "message": "bot instance unavailable"})
            return

        payload = self._read_json_body()

        if path == "/api/bot/start":
            config = self._merge_user_config(payload)
            self._save_user_config(config)
            self._apply_user_config_to_bot(config)
            if getattr(bot, "running", False):
                self._send_json(200, {"ok": True, "message": "Bot already running; config applied", "config": config})
            else:
                self._send_json(409, {"ok": False, "message": "Bot process is stopped. Start using scripts/start.sh or systemd.", "config": config})
            return

        if path == "/api/bot/stop":
            setattr(bot, "running", False)
            setattr(bot, "last_action", {"status": "STOP_REQUESTED", "source": "dashboard"})
            if hasattr(bot, "_audit"):
                try:
                    bot._audit({"event": "manual_stop", "source": "dashboard"})
                except Exception:
                    pass
            self._send_json(200, {"ok": True, "status": "stopping"})
            return

        if path in {"/api/bot/emergency-stop", "/api/bot/emergency_stop"}:
            reason = "Emergency stop requested from dashboard"
            if hasattr(bot, "_hard_stop"):
                try:
                    bot._hard_stop(reason)
                except Exception:
                    setattr(bot, "running", False)
            else:
                setattr(bot, "running", False)
                setattr(bot, "last_action", {"status": "HARD_STOP", "reason": reason})
            self._send_json(200, {"ok": True, "status": "hard_stop", "reason": reason})
            return

        self._send_text(404, "not found")

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        if path != "/api/bot/config":
            self._send_text(404, "not found")
            return

        payload = self._read_json_body()
        config = self._merge_user_config(payload)
        self._save_user_config(config)
        self._apply_user_config_to_bot(config)
        self._send_json(200, config)

    def _dashboard_html(self) -> str:
        try:
            return DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to load dashboard template: {}", exc)
            return "<h1>Dashboard template unavailable</h1>"

    def _endpoint_urls(self) -> Dict[str, str]:
        host = self.headers.get("Host") or f"localhost:{getattr(self.server, 'server_port', 3000)}"
        base = f"http://{host}"
        return {
            "official": f"{base}/",
            "health": f"{base}/api/health",
            "bot_status": f"{base}/api/bot/status",
            "bot_start": f"{base}/api/bot/start",
            "bot_stop": f"{base}/api/bot/stop",
            "bot_config": f"{base}/api/bot/config",
            "account": f"{base}/api/account",
            "positions": f"{base}/api/positions",
            "trades_recent": f"{base}/api/trades/recent",
            "performance": f"{base}/api/performance",
            "risk_state": f"{base}/api/risk/state",
            "symbols": f"{base}/api/symbols",
            "chart": f"{base}/api/chart",
            "fixed_rules": f"{base}/api/fixed-rules",
            "metrics": f"{base}/metrics",
        }

    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _as_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _normalize_timeframe_label(raw: str) -> str:
        normalized = str(raw or "H1").upper().strip()
        aliases = {
            "M1": "M1",
            "1M": "M1",
            "M5": "M5",
            "5M": "M5",
            "M15": "M15",
            "15M": "M15",
            "M30": "M30",
            "30M": "M30",
            "H1": "H1",
            "1H": "H1",
            "H4": "H4",
            "4H": "H4",
            "D1": "D1",
            "1D": "D1",
        }
        return aliases.get(normalized, "H1")

    def _market_chart_payload(self, symbol: str, timeframe: str, count: int) -> Dict[str, Any]:
        bot = self.bot_instance
        normalized_symbol = str(symbol or getattr(bot, "symbol", "EURUSD") or "EURUSD").upper().strip() or "EURUSD"
        normalized_timeframe = self._normalize_timeframe_label(timeframe or getattr(bot, "timeframe", "H1"))
        requested_count = max(20, min(500, self._as_int(count, 120)))

        if bot is None:
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_timeframe,
                "count": 0,
                "bars": [],
                "source": "unavailable",
                "error": "bot unavailable",
            }

        bridge = getattr(bot, "bridge", None)
        if bridge is None or not hasattr(bridge, "get_rates"):
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_timeframe,
                "count": 0,
                "bars": [],
                "source": "unavailable",
                "error": "bridge unavailable",
            }

        try:
            rates = bridge.get_rates(normalized_symbol, normalized_timeframe, requested_count)
        except Exception as exc:
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_timeframe,
                "count": 0,
                "bars": [],
                "source": "unavailable",
                "error": "bridge request failed",
                "details": str(exc),
            }

        if not isinstance(rates, dict):
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_timeframe,
                "count": 0,
                "bars": [],
                "source": "unavailable",
                "error": "malformed rates payload",
            }

        if rates.get("error"):
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_timeframe,
                "count": 0,
                "bars": [],
                "source": "unavailable",
                "error": str(rates.get("error")),
                "details": rates.get("details"),
            }

        raw_bars = rates.get("bars")
        if not isinstance(raw_bars, list):
            return {
                "symbol": normalized_symbol,
                "timeframe": normalized_timeframe,
                "count": 0,
                "bars": [],
                "source": "unavailable",
                "error": "bars missing from rates payload",
            }

        bars: List[Dict[str, Any]] = []
        for row in raw_bars:
            if not isinstance(row, dict):
                continue

            timestamp = self._as_int(row.get("timestamp", row.get("time")), 0)
            open_price = self._as_float(row.get("open"), 0.0)
            high_price = self._as_float(row.get("high"), 0.0)
            low_price = self._as_float(row.get("low"), 0.0)
            close_price = self._as_float(row.get("close"), 0.0)
            if min(open_price, high_price, low_price, close_price) <= 0:
                continue

            if timestamp > 0:
                time_iso = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
            else:
                time_iso = str(row.get("time") or "")

            bars.append(
                {
                    "timestamp": timestamp,
                    "time": time_iso,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "tick_volume": self._as_int(row.get("tick_volume"), 0),
                }
            )

        bars.sort(key=lambda x: x.get("timestamp", 0))

        return {
            "symbol": str(rates.get("symbol", normalized_symbol)).upper(),
            "timeframe": self._normalize_timeframe_label(str(rates.get("timeframe", normalized_timeframe))),
            "count": len(bars),
            "bars": bars,
            "source": "mt5",
        }

    def _read_json_body(self) -> Dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except Exception:
            content_length = 0
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _user_config(self) -> Dict[str, Any]:
        config = _load_yaml(USER_CONFIG_PATH, DEFAULT_USER_CONFIG)
        return self._merge_user_config(config)

    def _save_user_config(self, config: Dict[str, Any]) -> None:
        _save_yaml(USER_CONFIG_PATH, config)

    def _merge_user_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current = _load_yaml(USER_CONFIG_PATH, DEFAULT_USER_CONFIG)
        merged = dict(current)
        for key in ["symbol", "lot_size", "risk_percent", "strategy_version", "notification_preferences"]:
            if key in payload:
                merged[key] = payload[key]

        symbol = str(merged.get("symbol", "EURUSD")).upper().strip() or "EURUSD"
        mode = "live"
        strategy_version = str(merged.get("strategy_version", "v2.3 (Latest)")).strip() or "v2.3 (Latest)"

        try:
            lot_size = float(merged.get("lot_size", 0.01))
        except Exception:
            lot_size = 0.01
        lot_size = max(0.01, min(1.0, lot_size))

        try:
            risk_percent = float(merged.get("risk_percent", 1.0))
        except Exception:
            risk_percent = 1.0
        risk_percent = max(0.1, min(3.0, risk_percent))

        notifications = merged.get("notification_preferences")
        if not isinstance(notifications, dict):
            notifications = {"telegram": True}

        return {
            "symbol": symbol,
            "lot_size": round(lot_size, 2),
            "risk_percent": round(risk_percent, 2),
            "mode": mode,
            "strategy_version": strategy_version,
            "notification_preferences": notifications,
        }

    def _apply_user_config_to_bot(self, config: Dict[str, Any]) -> None:
        bot = self.bot_instance
        if bot is None:
            return

        symbol = str(config.get("symbol", "EURUSD")).upper()
        setattr(bot, "symbol", symbol)

        setattr(bot, "confirm_live", True)
        setattr(bot, "dry_run", False)

        lot_size = float(config.get("lot_size", 0.01))
        setattr(bot, "min_live_lots", lot_size)

        risk_percent = float(config.get("risk_percent", 1.0))
        risk_manager = getattr(bot, "risk_manager", None)
        if risk_manager and getattr(risk_manager, "governor", None):
            risk_manager.governor.base_risk_percent = risk_percent

        bot_cfg = getattr(bot, "config", None)
        if isinstance(bot_cfg, dict):
            deployment = bot_cfg.setdefault("deployment", {})
            risk = bot_cfg.setdefault("risk", {})
            deployment["min_live_lots"] = lot_size
            risk["base_risk_percent"] = risk_percent

    def _fixed_rules(self) -> Dict[str, Any]:
        return _load_yaml(FIXED_RULES_PATH, DEFAULT_FIXED_RULES)

    def _symbols_from_mt5(self) -> List[str]:
        bot = self.bot_instance
        bridge = getattr(bot, "bridge", None) if bot else None
        if bridge is None or not hasattr(bridge, "get_symbols"):
            return []

        try:
            payload = bridge.get_symbols()
        except Exception:
            return []

        if not isinstance(payload, dict):
            return []

        error_text = str(payload.get("error", "")).strip().lower()
        if error_text:
            if error_text == "unknown action":
                type(self)._mt5_symbols_supported = False
            return []

        raw_symbols = payload.get("symbols")
        if not isinstance(raw_symbols, list):
            return []

        symbols: List[str] = []
        seen = set()
        for raw in raw_symbols:
            sym = str(raw or "").upper().strip()
            if not sym or sym in seen:
                continue
            symbols.append(sym)
            seen.add(sym)
        if symbols:
            type(self)._mt5_symbols_supported = True
        return symbols

    def _symbols_from_mt5_rates_probe(self) -> List[str]:
        if not MT5_SYMBOLS_PROBE_ENABLED:
            return []

        bot = self.bot_instance
        bridge = getattr(bot, "bridge", None) if bot else None
        if bridge is None or not hasattr(bridge, "get_rates"):
            return []

        now_ts = datetime.now(timezone.utc).timestamp()
        cache = type(self)._symbols_probe_cache
        if isinstance(cache, dict) and (now_ts - float(cache.get("timestamp", 0.0))) < MT5_SYMBOLS_PROBE_TTL_SECONDS:
            cached_symbols = cache.get("symbols")
            if isinstance(cached_symbols, list):
                return [str(sym).upper() for sym in cached_symbols if str(sym).strip()]

        candidates: List[str] = []
        candidates.extend(MT5_PROBE_SYMBOLS)

        if bot and isinstance(getattr(bot, "config", None), dict):
            symbols_cfg = bot.config.get("symbols", {})
            tradable = symbols_cfg.get("tradable", [])
            if isinstance(tradable, list):
                candidates.extend([str(sym).upper() for sym in tradable if str(sym).strip()])

        selected = str(getattr(bot, "symbol", "") or "").upper().strip()
        if selected:
            candidates.insert(0, selected)

        deduped: List[str] = []
        seen = set()
        for symbol in candidates:
            sym = str(symbol or "").upper().strip()
            if not sym or sym in seen:
                continue
            deduped.append(sym)
            seen.add(sym)

        valid: List[str] = []
        for symbol in deduped[:MT5_SYMBOLS_PROBE_MAX_CANDIDATES]:
            try:
                rates = bridge.get_rates(symbol, str(getattr(bot, "timeframe", "H1") or "H1"), 1)
            except Exception:
                continue
            if isinstance(rates, dict) and not rates.get("error") and isinstance(rates.get("bars"), list) and rates.get("bars"):
                valid.append(symbol)

        type(self)._symbols_probe_cache = {"timestamp": now_ts, "symbols": valid}
        return valid

    def _available_symbols(self) -> List[str]:
        bot = self.bot_instance
        selected = str(getattr(bot, "symbol", "") or "").upper()
        now_ts = datetime.now(timezone.utc).timestamp()
        cache = type(self)._symbols_cache
        symbols: List[str] = []
        if isinstance(cache, dict) and (now_ts - float(cache.get("timestamp", 0.0))) < SYMBOLS_CACHE_TTL_SECONDS:
            cached = cache.get("symbols")
            if isinstance(cached, list):
                symbols = [str(sym).upper() for sym in cached if str(sym).strip()]

        if not symbols:
            symbols = self._symbols_from_mt5()
            if not symbols:
                symbols = self._symbols_from_mt5_rates_probe()

        if not symbols and bot and isinstance(getattr(bot, "config", None), dict):
            symbols_cfg = bot.config.get("symbols", {})
            tradable = symbols_cfg.get("tradable", [])
            if isinstance(tradable, list):
                symbols.extend([str(s).upper() for s in tradable if str(s).strip()])

        if not symbols:
            symbols = list(DEFAULT_SYMBOLS)
        elif len(symbols) < 3:
            symbols.extend([sym for sym in DEFAULT_SYMBOLS if sym not in symbols])

        type(self)._symbols_cache = {"timestamp": now_ts, "symbols": list(symbols)}

        if selected:
            if selected in symbols:
                symbols = [selected] + [s for s in symbols if s != selected]
            else:
                symbols.insert(0, selected)

        deduped: List[str] = []
        seen = set()
        for sym in symbols:
            if sym not in seen:
                deduped.append(sym)
                seen.add(sym)
        return deduped

    def _positions_payload(self) -> List[Dict[str, Any]]:
        bot = self.bot_instance
        governor = getattr(getattr(bot, "risk_manager", None), "governor", None)
        positions = list(getattr(governor, "open_positions", []) or [])

        payload: List[Dict[str, Any]] = []
        for pos in positions:
            if isinstance(pos, dict):
                payload.append(pos)
            elif hasattr(pos, "__dict__"):
                payload.append(dict(vars(pos)))
            else:
                payload.append({"value": str(pos)})
        return payload

    def _account_payload(self) -> Dict[str, Any]:
        status = self.get_bot_status()
        return {
            "balance": float(status.get("balance", 0.0) or 0.0),
            "equity": float(status.get("equity", 0.0) or 0.0),
            "margin": 0.0,
            "free_margin": float(status.get("equity", 0.0) or 0.0),
            "timestamp": status.get("timestamp"),
        }

    def _risk_state_payload(self) -> Dict[str, Any]:
        bot = self.bot_instance
        governor = getattr(getattr(bot, "risk_manager", None), "governor", None)
        status = self.get_bot_status()
        return {
            "risk_state": status.get("risk_state", "unknown"),
            "drawdown": float(status.get("drawdown", 0.0) or 0.0),
            "daily_pnl": float(status.get("daily_pnl", 0.0) or 0.0),
            "weekly_pnl": float(status.get("weekly_pnl", 0.0) or 0.0),
            "consecutive_losses": int(getattr(governor, "consecutive_losses", 0) or 0),
            "open_positions": int(status.get("open_positions", 0) or 0),
        }

    def _recent_audit_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        bot = self.bot_instance
        audit_path = Path(getattr(bot, "audit_jsonl", "")) if bot else Path()
        if not audit_path.exists():
            return []

        try:
            lines = audit_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []

        events: List[Dict[str, Any]] = []
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue

            event_type = str(payload.get("event", payload.get("status", "event"))).upper()
            events.append(
                {
                    "timestamp": payload.get("timestamp"),
                    "event": event_type,
                    "symbol": payload.get("symbol"),
                    "direction": payload.get("direction"),
                    "lots": payload.get("lots"),
                    "entry": payload.get("entry_price"),
                    "exit": payload.get("exit_price"),
                    "pnl": payload.get("pnl"),
                    "r_multiple": payload.get("r_multiple"),
                    "reason": payload.get("reason"),
                    "trade_id": payload.get("trade_id"),
                    "raw": payload,
                }
            )
            if len(events) >= limit:
                break
        return events

    @staticmethod
    def _performance_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
        def _safe_float(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        ordered_events = sorted(events, key=lambda event: str(event.get("timestamp") or ""))

        executed = sum(1 for e in ordered_events if e.get("event") == "EXECUTED")
        approved = sum(1 for e in ordered_events if e.get("event") == "APPROVED")
        rejected = sum(1 for e in ordered_events if e.get("event") == "REJECTED")
        hard_stops = sum(1 for e in ordered_events if e.get("event") == "HARD_STOP")
        loop_errors = sum(1 for e in ordered_events if e.get("event") == "LOOP_ERROR")

        pnls = [_safe_float(e.get("pnl")) for e in ordered_events]
        pnls = [p for p in pnls if p is not None]
        r_values = [_safe_float(e.get("r_multiple")) for e in ordered_events]
        r_values = [r for r in r_values if r is not None]

        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        win_rate = (wins / max(1, wins + losses)) * 100.0
        execution_rate = (executed / max(1, approved + rejected + executed)) * 100.0

        # Frontend chart datasets
        now_iso = datetime.now(timezone.utc).isoformat()
        equity_curve: List[Dict[str, Any]] = []
        running_equity = 0.0
        for event in ordered_events:
            pnl_value = _safe_float(event.get("pnl"))
            if pnl_value is not None:
                running_equity += pnl_value
            timestamp = str(event.get("timestamp") or now_iso)
            equity_curve.append({"time": timestamp, "value": round(running_equity, 2)})
        if not equity_curve:
            equity_curve = [{"time": now_iso, "value": 0.0}]

        daily_totals: Dict[str, float] = {}
        for event in ordered_events:
            pnl_value = _safe_float(event.get("pnl"))
            if pnl_value is None:
                continue
            ts = str(event.get("timestamp") or now_iso)
            day = ts[:10] if len(ts) >= 10 else now_iso[:10]
            daily_totals[day] = daily_totals.get(day, 0.0) + pnl_value
        daily_pnl = [{"day": day, "pnl": round(value, 2)} for day, value in sorted(daily_totals.items())]
        if not daily_pnl:
            daily_pnl = [{"day": now_iso[:10], "pnl": 0.0}]

        regime_performance = [{"regime": "UNKNOWN", "win_rate": round(win_rate, 2)}]
        trade_distribution = r_values if r_values else [0.0]

        return {
            "executed_count": executed,
            "approved_count": approved,
            "rejected_count": rejected,
            "hard_stop_count": hard_stops,
            "loop_error_count": loop_errors,
            "execution_rate_pct": round(execution_rate, 2),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "total_pnl": round(sum(pnls), 2) if pnls else 0.0,
            "sample_size": len(ordered_events),
            "equity_curve": equity_curve,
            "daily_pnl": daily_pnl,
            "regime_performance": regime_performance,
            "trade_distribution": trade_distribution,
        }

    def get_bot_status(self) -> Dict[str, Any]:
        bot = self.bot_instance
        if bot is None:
            return {"status": "initializing"}

        risk_manager = getattr(bot, "risk_manager", None)
        governor = getattr(risk_manager, "governor", None)
        trade_recorder = getattr(bot, "trade_recorder", None)
        metrics = getattr(bot, "metrics", None)

        uptime = 0.0
        if metrics and hasattr(metrics, "get_uptime"):
            try:
                uptime = float(metrics.get_uptime() or 0.0)
            except Exception:
                uptime = 0.0

        risk_state = "unknown"
        if governor:
            current_state = getattr(governor, "current_state", None)
            current_state_value = getattr(current_state, "value", None)
            risk_state = str(current_state_value or getattr(governor, "risk_state", "unknown"))

        status: Dict[str, Any] = {
            "status": "running" if getattr(bot, "running", False) else "stopped",
            "uptime": uptime,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "risk_state": risk_state,
            "trades_pending": len(getattr(trade_recorder, "pending_trades", {})) if trade_recorder else 0,
            "open_positions": len(getattr(governor, "open_positions", [])) if governor else 0,
            "symbol": getattr(bot, "symbol", None),
            "timeframe": getattr(bot, "timeframe", None),
            "loop": getattr(bot, "loop_counter", 0),
            "last_action": getattr(bot, "last_action", {}),
            "mode": "LIVE",
        }

        if governor:
            status["balance"] = float(getattr(governor, "previous_balance", 0.0) or 0.0)
            status["equity"] = float(getattr(governor, "current_equity", 0.0) or 0.0)
            status["drawdown"] = float(getattr(governor, "current_drawdown", 0.0) or 0.0)
            status["daily_pnl"] = float(getattr(governor, "daily_pnl", 0.0) or 0.0)
            status["weekly_pnl"] = float(getattr(governor, "weekly_pnl", 0.0) or 0.0)
            status["consecutive_losses"] = int(getattr(governor, "consecutive_losses", 0) or 0)

        return status

    def _send_json(self, code: int, payload: Any) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, code: int, payload: str) -> None:
        raw = payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, code: int, payload: str) -> None:
        raw = payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return


class HealthServer:
    """Background HTTP server exposing dashboard and monitoring endpoints."""

    def __init__(self, bot, port: int = 3000):
        self.bot = bot
        self.port = int(port)
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None
        HealthCheckHandler.bot_instance = bot

    def start(self) -> None:
        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), HealthCheckHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Health check server started on port {self.port}")

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            logger.info("Health check server stopped")
