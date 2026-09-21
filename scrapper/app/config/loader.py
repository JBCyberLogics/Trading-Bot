from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.config.schema import AppConfig


class ConfigValidationError(RuntimeError):
    """Raised when startup config is invalid or unsafe."""


class RuntimeSettings(BaseModel):
    project_root: Path
    config_path: Path
    database_path: Path
    log_directory: Path
    data_directory: Path
    app_config_directory: Path
    credentials_store: Path
    encryption_key: Optional[str] = None
    encryption_enabled: bool = False
    encryption_error: Optional[str] = None
    dashboard_password_hash: str
    mt5_zmq_host: str = "127.0.0.1"
    mt5_zmq_port: int = 5555
    mt5_account_login: Optional[int] = None
    mt5_account_password: Optional[str] = None
    mt5_account_server: Optional[str] = None
    news_provider_url: Optional[str] = None


@dataclass(slots=True)
class LoadedConfig:
    config: AppConfig
    runtime: RuntimeSettings


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigValidationError(f"Config payload must be an object: {path}")
    return payload


def _resolve_path(root: Path, raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    return (root / candidate).resolve()


def _inject_env_overrides(payload: Dict[str, Any], project_root: Path) -> Dict[str, Any]:
    updated = dict(payload)
    system = dict(updated.get("system", {}))
    paths = dict(system.get("paths", {}))

    env_map = {
        "log_directory": os.getenv("BOT_PATHS_LOG_DIRECTORY"),
        "data_directory": os.getenv("BOT_PATHS_DATA_DIRECTORY"),
        "config_directory": os.getenv("BOT_PATHS_CONFIG_DIRECTORY"),
        "database_path": os.getenv("BOT_PATHS_DATABASE_PATH"),
        "credentials_store": os.getenv("BOT_PATHS_CREDENTIALS_STORE"),
    }
    for key, value in env_map.items():
        if value:
            paths[key] = value

    # Dev local-first defaults for empty entries.
    defaults = {
        "log_directory": "runtime/logs",
        "data_directory": "runtime/data",
        "config_directory": "runtime/config",
        "database_path": "runtime/data/trades.db",
        "credentials_store": "runtime/config/accounts",
    }
    for key, fallback in defaults.items():
        if not paths.get(key):
            paths[key] = fallback

    # Normalize relative values to project-root relative strings.
    for key in list(paths.keys()):
        raw = str(paths[key])
        resolved = _resolve_path(project_root, raw)
        paths[key] = str(resolved)

    system["paths"] = paths
    updated["system"] = system
    return updated


def _optional_int_env(*names: str) -> Optional[int]:
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        value = raw.strip()
        if not value:
            continue
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigValidationError(f"Environment variable {name} must be an integer") from exc
    return None


def _optional_str_env(*names: str) -> Optional[str]:
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        value = raw.strip()
        if value:
            return value
    return None


def load_config(project_root: Path | None = None) -> LoadedConfig:
    root = (project_root or Path(__file__).resolve().parents[2]).resolve()
    config_path_env = os.getenv("BOT_CONFIG_PATH", "config/phase1_config.json")
    config_path = _resolve_path(root, config_path_env)
    if not config_path.exists():
        raise ConfigValidationError(f"Config not found: {config_path}")

    payload = _read_json(config_path)

    override_path_env = os.getenv("BOT_CONFIG_OVERRIDES")
    if override_path_env:
        override_path = _resolve_path(root, override_path_env)
        if override_path.exists():
            payload = _deep_merge(payload, _read_json(override_path))

    payload = _inject_env_overrides(payload, root)

    config = AppConfig.model_validate(payload)

    dashboard_hash = os.getenv("DASHBOARD_PASSWORD_HASH") or config.dashboard.authentication.password_hash
    if config.dashboard.authentication.enabled and not dashboard_hash:
        raise ConfigValidationError(
            "Dashboard authentication is enabled but no password hash is set. "
            "Set DASHBOARD_PASSWORD_HASH."
        )

    encryption_key = os.getenv(config.system.accounts.encryption_key_env, "").strip()
    encryption_enabled = bool(encryption_key)
    encryption_error = None
    if config.account_manager.store_encrypted and not encryption_enabled:
        encryption_error = (
            f"Account persistence disabled: missing {config.system.accounts.encryption_key_env}. "
            "Set this env var to enable encrypted account storage."
        )

    runtime = RuntimeSettings(
        project_root=root,
        config_path=config_path,
        database_path=Path(config.system.paths.database_path),
        log_directory=Path(config.system.paths.log_directory),
        data_directory=Path(config.system.paths.data_directory),
        app_config_directory=Path(config.system.paths.config_directory),
        credentials_store=Path(config.system.paths.credentials_store),
        encryption_key=encryption_key if encryption_enabled else None,
        encryption_enabled=encryption_enabled,
        encryption_error=encryption_error,
        dashboard_password_hash=dashboard_hash,
        mt5_zmq_host=os.getenv("MT5_ZMQ_HOST", "127.0.0.1"),
        mt5_zmq_port=int(os.getenv("MT5_ZMQ_PORT", "5555")),
        mt5_account_login=_optional_int_env("MT5_ACCOUNT_LOGIN", "MT5_LOGIN_ID"),
        mt5_account_password=_optional_str_env("MT5_ACCOUNT_PASSWORD", "MT5_PASSWORD"),
        mt5_account_server=_optional_str_env("MT5_ACCOUNT_SERVER", "MT5_SERVER_ID"),
        news_provider_url=os.getenv("NEWS_PROVIDER_URL") or None,
    )

    for path in [runtime.log_directory, runtime.data_directory, runtime.app_config_directory, runtime.credentials_store]:
        path.mkdir(parents=True, exist_ok=True)
    runtime.database_path.parent.mkdir(parents=True, exist_ok=True)

    return LoadedConfig(config=config, runtime=runtime)
