from __future__ import annotations

import logging

import pytest

from app.config.loader import load_config
from app.mt5_connection.manager import ConnectionManager
from app.mt5_connection.zmq_bridge import ZMQBridgeError


class DummyDB:
    def log_connection_event(self, status: str, details: dict) -> None:
        return None


class UnsupportedLoginPayloadBridge:
    host = "127.0.0.1"
    port = 5555

    def login(self, login: int, password: str, server: str) -> dict:
        return {"error": "unknown action: LOGIN"}


class UnsupportedLoginExceptionBridge:
    host = "127.0.0.1"
    port = 5555

    def login(self, login: int, password: str, server: str) -> dict:
        raise ZMQBridgeError("unknown action: LOGIN")


class SuccessfulLoginBridge:
    host = "127.0.0.1"
    port = 5555

    def login(self, login: int, password: str, server: str) -> dict:
        return {"ok": True}


def _manager_with_bridge(test_env: dict, bridge: object) -> ConnectionManager:
    loaded = load_config()
    loaded.config.mt5_connection.terminal_config.login_retry_attempts = 1
    loaded.config.mt5_connection.terminal_config.login_retry_delay_seconds = 0
    return ConnectionManager(loaded.config, bridge=bridge, db=DummyDB())


@pytest.mark.asyncio
async def test_validate_credentials_skips_when_unsupported_login_policy_is_skip(
    test_env: dict, caplog: pytest.LogCaptureFixture
) -> None:
    manager = _manager_with_bridge(test_env, UnsupportedLoginExceptionBridge())
    manager.config.mt5_connection.terminal_config.unsupported_login_action = "skip"

    with caplog.at_level(logging.INFO):
        ok, reason = await manager.validate_credentials(login=1, password="secret", server="demo")

    assert ok is True
    assert reason == "skipped_unsupported_login_action"
    assert "bridge_login_unsupported policy=skip" in caplog.text


@pytest.mark.asyncio
async def test_validate_credentials_fails_when_unsupported_login_policy_is_fail(
    test_env: dict, caplog: pytest.LogCaptureFixture
) -> None:
    manager = _manager_with_bridge(test_env, UnsupportedLoginPayloadBridge())
    manager.config.mt5_connection.terminal_config.unsupported_login_action = "fail"

    with caplog.at_level(logging.INFO):
        ok, reason = await manager.validate_credentials(login=1, password="secret", server="demo")

    assert ok is False
    assert "Bridge login command unsupported." in reason
    assert "127.0.0.1:5555" in reason
    assert "bridge_login_unsupported policy=fail" in caplog.text


@pytest.mark.asyncio
async def test_validate_credentials_keeps_supported_login_behavior(test_env: dict) -> None:
    manager = _manager_with_bridge(test_env, SuccessfulLoginBridge())
    manager.config.mt5_connection.terminal_config.unsupported_login_action = "fail"

    ok, reason = await manager.validate_credentials(login=1, password="secret", server="demo")

    assert ok is True
    assert reason == "ok"
