from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.loader import ConfigValidationError, load_config
from app.config.schema import AppConfig


def _base_payload() -> dict:
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "config" / "phase1_config.json").read_text(encoding="utf-8"))


def test_loader_requires_dashboard_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _base_payload()
    payload["system"]["paths"] = {
        "log_directory": str(tmp_path / "logs"),
        "data_directory": str(tmp_path / "data"),
        "config_directory": str(tmp_path / "config"),
        "database_path": str(tmp_path / "data" / "trades.db"),
        "credentials_store": str(tmp_path / "config" / "accounts"),
    }
    payload["dashboard"]["authentication"]["password_hash"] = ""

    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv("BOT_CONFIG_PATH", str(cfg_path))
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)

    with pytest.raises(ConfigValidationError):
        load_config(project_root=Path(__file__).resolve().parents[2])


def test_loader_resolves_paths_and_disables_encryption_without_key(
    monkeypatch: pytest.MonkeyPatch, test_env: dict
) -> None:
    monkeypatch.delenv("BOT_ENCRYPTION_KEY", raising=False)
    loaded = load_config(project_root=Path(__file__).resolve().parents[2])

    assert loaded.runtime.log_directory.is_absolute()
    assert loaded.runtime.data_directory.is_absolute()
    assert loaded.runtime.database_path.is_absolute()
    assert loaded.runtime.encryption_enabled is False
    assert "missing" in str(loaded.runtime.encryption_error).lower()


def test_loader_reads_default_mt5_account_from_env(
    monkeypatch: pytest.MonkeyPatch, test_env: dict
) -> None:
    monkeypatch.setenv("MT5_ACCOUNT_LOGIN", "33386503")
    monkeypatch.setenv("MT5_ACCOUNT_PASSWORD", "ophJM35##")
    monkeypatch.setenv("MT5_ACCOUNT_SERVER", "FundedNext-Server3")

    loaded = load_config(project_root=Path(__file__).resolve().parents[2])

    assert loaded.runtime.mt5_account_login == 33386503
    assert loaded.runtime.mt5_account_password == "ophJM35##"
    assert loaded.runtime.mt5_account_server == "FundedNext-Server3"


def test_terminal_config_defaults_unsupported_login_action_to_skip() -> None:
    payload = _base_payload()
    payload["mt5_connection"]["terminal_config"].pop("unsupported_login_action", None)

    config = AppConfig.model_validate(payload)

    assert config.mt5_connection.terminal_config.unsupported_login_action == "skip"


@pytest.mark.parametrize("policy", ["skip", "fail"])
def test_terminal_config_accepts_supported_unsupported_login_actions(policy: str) -> None:
    payload = _base_payload()
    payload["mt5_connection"]["terminal_config"]["unsupported_login_action"] = policy

    config = AppConfig.model_validate(payload)

    assert config.mt5_connection.terminal_config.unsupported_login_action == policy


def test_terminal_config_rejects_invalid_unsupported_login_action() -> None:
    payload = _base_payload()
    payload["mt5_connection"]["terminal_config"]["unsupported_login_action"] = "warn"

    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)
