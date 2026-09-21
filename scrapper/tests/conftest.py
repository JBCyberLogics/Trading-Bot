from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any, Dict

import httpx
import pytest
from passlib.context import CryptContext

from app.main import create_app


pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


class FakeBridge:
    def __init__(self, host: str, port: int, timeout_ms: int = 60000) -> None:
        self.host = host
        self.port = port
        self.timeout_ms = timeout_ms
        self._lock = threading.Lock()
        self._ticket = 1000
        self.positions: list[dict[str, Any]] = []

    def ping(self) -> Dict[str, Any]:
        return {"ok": True}

    def login(self, login: int, password: str, server: str) -> Dict[str, Any]:
        if int(login) <= 0 or not password or not server or password == "bad":
            return {"error": "invalid_credentials"}
        return {"ok": True}

    def get_account_info(self) -> Dict[str, Any]:
        return {
            "login": 123456,
            "balance": 10000.0,
            "equity": 10010.0,
            "margin": 250.0,
            "daily_pnl": 10.0,
        }

    def get_positions(self) -> Dict[str, Any]:
        return {"positions": list(self.positions)}

    def get_symbols(self) -> Dict[str, Any]:
        return {"symbols": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]}

    def get_rates(self, symbol: str, timeframe: str, count: int) -> Dict[str, Any]:
        bars = []
        base = 1.1000
        for idx in range(max(5, min(30, count))):
            ts = 1_700_000_000 + idx * 60
            bars.append(
                {
                    "timestamp": ts,
                    "open": base + idx * 0.0001,
                    "high": base + idx * 0.0002,
                    "low": base + idx * 0.00005,
                    "close": base + idx * 0.00012,
                    "tick_volume": 100 + idx,
                }
            )
        return {"symbol": symbol, "timeframe": timeframe, "bars": bars}

    def get_tick(self, symbol: str) -> Dict[str, Any]:
        return {"symbol": symbol, "bid": 1.1000, "ask": 1.1001}

    def get_order_book(self, symbol: str, levels: int = 10) -> Dict[str, Any]:
        bids = [{"price": 1.0999 - i * 0.0001, "volume": 15 + i} for i in range(levels)]
        asks = [{"price": 1.1001 + i * 0.0001, "volume": 4 + i} for i in range(levels)]
        return {"symbol": symbol, "bids": bids, "asks": asks}

    def execute_order(self, symbol: str, direction: str, lot_size: float, stop_loss: float, take_profit: float) -> Dict[str, Any]:
        with self._lock:
            self._ticket += 1
            ticket = self._ticket
            self.positions.append(
                {
                    "ticket": ticket,
                    "symbol": symbol,
                    "direction": direction,
                    "volume": lot_size,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                }
            )
            return {"ok": True, "ticket": ticket}

    def close_all_positions(self) -> Dict[str, Any]:
        with self._lock:
            self.positions.clear()
        return {"ok": True, "closed": "all"}

    def stop(self) -> None:
        return None


def _write_test_config(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    base_cfg = json.loads((root / "config" / "phase1_config.json").read_text(encoding="utf-8"))

    runtime_root = tmp_path / "runtime"
    (runtime_root / "logs").mkdir(parents=True, exist_ok=True)
    (runtime_root / "data").mkdir(parents=True, exist_ok=True)
    (runtime_root / "config" / "accounts").mkdir(parents=True, exist_ok=True)

    base_cfg["system"]["paths"] = {
        "log_directory": str(runtime_root / "logs"),
        "data_directory": str(runtime_root / "data"),
        "config_directory": str(runtime_root / "config"),
        "database_path": str(runtime_root / "data" / "trades.db"),
        "credentials_store": str(runtime_root / "config" / "accounts"),
    }
    base_cfg["logging"]["outputs"][0]["path"] = str(runtime_root / "logs" / "bot.log")
    base_cfg["startup"]["initial_delay_seconds"] = 0
    base_cfg["startup"]["auto_start_trading"] = False
    base_cfg["system"]["wine"]["wineserver_command"] = "true"
    base_cfg["dashboard"]["refresh_interval_seconds"] = 1
    base_cfg["data_scraping"]["tick_data"]["interval_ms"] = 50
    base_cfg["data_scraping"]["order_book"]["interval_ms"] = 50
    base_cfg["data_scraping"]["ohlc_data"]["update_interval_seconds"] = 1
    base_cfg["data_scraping"]["account_info"]["update_interval_seconds"] = 1

    cfg_path = tmp_path / "phase1_test.json"
    cfg_path.write_text(json.dumps(base_cfg), encoding="utf-8")
    return cfg_path


@pytest.fixture()
def test_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Dict[str, str]:
    cfg_path = _write_test_config(tmp_path)
    password_hash = pwd_context.hash("adminpass")
    encryption_key = "test-encryption-key"

    monkeypatch.setenv("BOT_CONFIG_PATH", str(cfg_path))
    monkeypatch.setenv("BOT_ENCRYPTION_KEY", encryption_key)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", password_hash)
    monkeypatch.delenv("NEWS_PROVIDER_URL", raising=False)
    monkeypatch.setenv("MT5_ZMQ_HOST", "127.0.0.1")
    monkeypatch.setenv("MT5_ZMQ_PORT", "5555")

    return {
        "config_path": str(cfg_path),
        "password": "adminpass",
        "encryption_key": encryption_key,
    }


@pytest.fixture()
def test_app(test_env: Dict[str, str]):
    return create_app(bridge_class=FakeBridge)


@pytest.fixture()
async def async_client(test_app):
    async with test_app.router.lifespan_context(test_app):
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture()
def auth_headers() -> Dict[str, str]:
    return {"Content-Type": "application/json"}
