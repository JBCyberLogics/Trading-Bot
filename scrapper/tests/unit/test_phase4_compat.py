from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.config.loader import load_config
from app.config.schema import AppConfig
from app.core.orchestrator import BotOrchestrator
from app.mt5_connection.manager import ConnectionContext


def _base_payload() -> dict:
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "config" / "phase1_config.json").read_text(encoding="utf-8"))


def test_phase4_aliases_normalize_runtime_fields() -> None:
    payload = _base_payload()
    payload["phase"] = 4
    payload["version"] = "4.0.0"
    payload["description"] = "phase4-normalization"

    payload["system"].pop("accounts", None)

    payload["market_awareness"] = {
        "sessions": {
            "sessions": {
                "london": {"start_hour": 9, "end_hour": 18, "timezone": "UTC"},
                "newyork": {"start_hour": 13, "end_hour": 22, "timezone": "UTC"},
                "asian": {"start_hour": 0, "end_hour": 9, "timezone": "UTC"},
            }
        }
    }

    payload["trading_logic"]["entry_rules"]["trading_hours"] = {
        "enabled": True,
        "sessions": ["london", "newyork", "asian"],
        "weekdays_only": True,
    }
    payload["trading_logic"]["entry_rules"]["spread_requirement"] = {
        "enabled": True,
        "base_max_pips": 2.25,
        "check_on_every_tick": True,
        "reject_if_increasing": True,
    }
    payload["trading_logic"]["entry_rules"]["order_book_imbalance"] = {
        "enabled": True,
        "calculation": {
            "method": "weighted_proximity",
            "levels_to_scan": 12,
            "decay_factor": 0.82,
        },
        "dynamic_thresholds": {"minimum_value": 0.4, "maximum_value": 0.8},
        "confirmation": {
            "require_consecutive_readings": 2,
            "consecutive_interval_ms": 80,
        },
    }
    payload["trading_logic"]["exit_rules"]["take_profit"] = {
        "type": "ml_optimized_dynamic",
        "base_value": 1.8,
    }
    payload["trading_logic"]["exit_rules"]["stop_loss"] = {
        "type": "ml_optimized_dynamic",
        "base_value": 1.2,
    }
    payload["trading_logic"]["exit_rules"]["time_exit"] = {
        "enabled": True,
        "max_duration_minutes": 18,
        "exit_if_not_profitable": True,
    }
    payload["startup"]["health_check"] = {
        "mt5_connection": False,
        "wine_environment": True,
        "database_connection": True,
        "disk_space_gb": 30,
    }

    config = AppConfig.model_validate(payload)

    assert config.system.accounts.encryption_key_env == "BOT_ENCRYPTION_KEY"
    assert config.trading_logic.entry_rules.spread_requirement.max_pips == pytest.approx(2.25)
    assert config.trading_logic.entry_rules.order_book_imbalance.levels_to_scan == 12
    assert config.trading_logic.entry_rules.order_book_imbalance.decay_factor == pytest.approx(0.82)
    assert config.trading_logic.entry_rules.order_book_imbalance.min_imbalance_long == pytest.approx(0.6)
    assert config.trading_logic.entry_rules.order_book_imbalance.min_imbalance_short == pytest.approx(-0.6)
    assert config.trading_logic.exit_rules.take_profit.value == pytest.approx(1.8)
    assert config.trading_logic.exit_rules.stop_loss.value == pytest.approx(1.2)
    assert config.trading_logic.exit_rules.time_exit.exit_immediately_if_not_profitable is True
    assert config.startup.health_check.minimum_disk_space_gb == 30
    assert config.trading_logic.entry_rules.trading_hours.london.start_hour == 9


def test_phase4_config_file_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    paths_root = tmp_path / "phase4_runtime"
    monkeypatch.setenv("BOT_CONFIG_PATH", "config/phase4_config.json")
    monkeypatch.setenv("BOT_PATHS_LOG_DIRECTORY", str(paths_root / "logs"))
    monkeypatch.setenv("BOT_PATHS_DATA_DIRECTORY", str(paths_root / "data"))
    monkeypatch.setenv("BOT_PATHS_CONFIG_DIRECTORY", str(paths_root / "config"))
    monkeypatch.setenv("BOT_PATHS_DATABASE_PATH", str(paths_root / "data" / "trades.db"))
    monkeypatch.setenv("BOT_PATHS_CREDENTIALS_STORE", str(paths_root / "config" / "accounts"))
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", "test-hash")
    monkeypatch.setenv("BOT_ENCRYPTION_KEY", "phase4-test-key")

    loaded = load_config(project_root=root)

    assert loaded.config.phase == 4
    assert loaded.config.version == "4.0.0"
    assert loaded.config.trading_logic.exit_rules.take_profit.value is not None
    assert loaded.config.trading_logic.entry_rules.spread_requirement.max_pips > 0


def test_phase4_connection_steps_have_handlers() -> None:
    payload = _base_payload()
    payload["phase"] = 4
    payload["version"] = "4.0.0"
    payload["description"] = "phase4-steps"
    payload["connection_flow"]["steps"] = [
        {"step": "validate_credentials", "timeout_seconds": 5},
        {"step": "load_pair_configuration", "timeout_seconds": 3},
        {"step": "initialize_indicators", "timeout_seconds": 3},
        {"step": "detect_current_regime", "timeout_seconds": 3},
        {"step": "load_ml_models", "timeout_seconds": 3},
        {"step": "calculate_dynamic_thresholds", "timeout_seconds": 3},
        {"step": "start_data_scrapers", "timeout_seconds": 5},
        {"step": "enable_trading_logic", "timeout_seconds": 2},
    ]
    config = AppConfig.model_validate(payload)

    loaded = SimpleNamespace(config=config, runtime=SimpleNamespace())
    orchestrator = BotOrchestrator(
        loaded=loaded,
        connection=Mock(),
        scraper=Mock(),
        trading=Mock(),
        safety=Mock(),
        risk=Mock(),
        db=Mock(),
        bridge=Mock(),
        alerts=Mock(),
    )
    ctx = ConnectionContext(account_id="a1", login=1, password="p", server="s")
    handlers = orchestrator._step_handlers(ctx)

    for step in config.connection_flow.steps:
        assert step.step in handlers
