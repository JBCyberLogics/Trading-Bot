"""Configuration loader for runtime, risk, deployment, and validation settings."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any, Dict

import yaml

from src.config.env import load_env


DEFAULT_CONFIG: Dict[str, Any] = {
    # Existing strategy-level keys used by current components.
    "swing_lookback": 20,
    "min_swing_distance_atr": 0.5,
    "sweep_max_age_candles": 5,
    "displacement_required_candles": 3,
    "retrace_max_candles": 8,
    "atr_period": 14,
    "adx_period": 14,
    "volatility_lookback": 20,
    # Phase 5/6 structured sections.
    "risk": {
        "max_daily_loss_pct": 3.0,
        "max_weekly_loss_pct": 6.0,
        "max_drawdown_pct": 8.0,
        "max_consecutive_losses": 5,
        "max_concurrent_positions": 3,
        "max_correlation": 0.70,
        "base_risk_percent": 1.0,
        "live_deployment_multiplier": 0.1,
        "live_deployment_target": 20,
        "rollout_confidence_floor": 0.70,
        "max_spread_multiplier": 3.0,
        "max_volatility_spike": 2.0,
    },
    "deployment": {
        "require_confirm_live": True,
        "default_symbol": "EURUSD",
        "default_timeframe": "H1",
        "poll_interval_seconds": 5,
        "min_live_lots": 0.01,
        "bridge_timeout_ms": 5000,
        "bridge_request_retries": 1,
        "bridge_retry_delay_seconds": 0.25,
        "max_consecutive_loop_failures": 5,
        "logs_subdir": "live",
    },
    "validation": {
        "risk_coverage_min": 90,
        "walk_forward": {
            "lookback_days": 180,
            "min_sharpe": 1.0,
            "min_win_rate": 50.0,
            "min_consistency": 0.70,
        },
    },
    "news": {
        "api_key_env": "NEWS_API_KEY",
        "endpoint_env": "NEWS_API_ENDPOINT",
        "rapidapi_host_env": "NEWS_RAPIDAPI_HOST",
        "rapidapi_app_env": "NEWS_RAPIDAPI_APP",
        "buffer_minutes": 15,
        "hard_block_without_api": True,
    },
    "monitoring": {
        "health_check_port": 3000,
        "prometheus_enabled": True,
        "prometheus_port": 9090,
        "metrics_retention_days": 30,
    },
    "alerts": {
        "telegram_enabled": True,
        "email_enabled": False,
        "sms_enabled": False,
    },
    "backup": {
        "enabled": True,
        "interval_hours": 6,
        "retention_days": 30,
        "local_path": "backups",
        "s3_bucket": "",
    },
    "database": {
        "path": "database/live_trades.db",
        "backup_before_migration": True,
        "connection_timeout": 10,
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml_file(path: Path) -> Dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _env_mt5_settings() -> Dict[str, Any]:
    return {
        "mt5": {
            "login": os.getenv("MT5_LOGIN", ""),
            "password": os.getenv("MT5_PASSWORD", ""),
            "server": os.getenv("MT5_SERVER", ""),
            "wine_prefix": os.getenv("WINEPREFIX", str(Path.home() / ".mt5")),
            "terminal_path_windows": os.getenv("MT5_TERMINAL_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe"),
            "terminal_path_linux": os.getenv(
                "MT5_WINE_PATH",
                str(Path.home() / ".mt5" / "drive_c" / "Program Files" / "MetaTrader 5" / "terminal64.exe"),
            ),
            "zmq_host": os.getenv("ZMQ_HOST", "127.0.0.1"),
            "zmq_python_port": int(os.getenv("ZMQ_PYTHON_PORT", "5555")),
            "zmq_mt5_port": int(os.getenv("ZMQ_MT5_PORT", "5556")),
            "zmq_live_port": int(os.getenv("ZMQ_LIVE_PORT", "5557")),
        },
        "db_path": os.getenv("DB_PATH", "database/trades.db"),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "monitoring": {
            "health_check_port": int(os.getenv("HEALTH_CHECK_PORT", "3000")),
            "prometheus_port": int(os.getenv("PROMETHEUS_PORT", "9090")),
        },
    }


def load_config(config_dir: str | Path | None = None, profile: str | None = None) -> Dict[str, Any]:
    """Load merged config from defaults, YAML files, and environment."""
    load_env()

    root = Path(__file__).resolve().parents[2]
    cfg_dir = Path(config_dir) if config_dir else (root / "config")
    active_profile = str(profile or os.getenv("BOT_ENV", "development")).strip().lower()

    settings = _load_yaml_file(cfg_dir / "settings.yaml")
    profile_settings = _load_yaml_file(cfg_dir / f"{active_profile}.yaml")
    symbols = _load_yaml_file(cfg_dir / "symbols.yaml")

    config = _deep_merge(DEFAULT_CONFIG, settings)
    if profile_settings:
        config = _deep_merge(config, profile_settings)
    config = _deep_merge(config, _env_mt5_settings())

    if symbols:
        config["symbols"] = symbols

    news_cfg = config.setdefault("news", {})
    api_key_env = str(news_cfg.get("api_key_env", "NEWS_API_KEY"))
    endpoint_env = str(news_cfg.get("endpoint_env", "NEWS_API_ENDPOINT"))
    if not news_cfg.get("api_key"):
        news_cfg["api_key"] = os.getenv(api_key_env, "")
    if not news_cfg.get("endpoint"):
        news_cfg["endpoint"] = os.getenv(endpoint_env, "")
    rapidapi_host_env = str(news_cfg.get("rapidapi_host_env", "NEWS_RAPIDAPI_HOST"))
    rapidapi_app_env = str(news_cfg.get("rapidapi_app_env", "NEWS_RAPIDAPI_APP"))
    if not news_cfg.get("rapidapi_host"):
        news_cfg["rapidapi_host"] = os.getenv(rapidapi_host_env, "")
    if not news_cfg.get("rapidapi_app"):
        news_cfg["rapidapi_app"] = os.getenv(rapidapi_app_env, "")
    config["news_api_key"] = news_cfg.get("api_key", "")
    config["environment"] = active_profile

    return config
