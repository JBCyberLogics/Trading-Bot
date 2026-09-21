from __future__ import annotations

import app.api.routes as api_routes
import pytest
from app.mt5_connection.zmq_bridge import ZMQBridgeError

from tests.integration.test_runtime_behaviors import _bootstrap_connected


@pytest.mark.asyncio
async def test_market_order_book_handles_fallback_timeout(async_client, test_app, test_env: dict, monkeypatch) -> None:
    headers = await _bootstrap_connected(async_client, test_env["password"])
    orchestrator = test_app.state.container.orchestrator

    orchestrator.bridge.get_order_book = lambda symbol, levels=10: {"error": "order_book_unavailable"}

    def raising_fallback(*args, **kwargs):
        raise ZMQBridgeError("Timeout waiting for MT5 reply (RATES)")

    monkeypatch.setattr(api_routes, "derive_order_book_from_rates", raising_fallback)

    response = await async_client.get("/api/market/order-book/EURUSD?levels=20", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["error"] == "Timeout waiting for MT5 reply (RATES)"
    assert payload["warning"] == "order_book_unavailable"
    assert payload["bids"] == []
    assert payload["asks"] == []


@pytest.mark.asyncio
async def test_market_rates_handles_bridge_timeout(async_client, test_app, test_env: dict) -> None:
    headers = await _bootstrap_connected(async_client, test_env["password"])
    orchestrator = test_app.state.container.orchestrator

    def raising_rates(symbol: str, timeframe: str, count: int):
        raise ZMQBridgeError("Timeout waiting for MT5 reply (RATES)")

    orchestrator.bridge.get_rates = raising_rates

    response = await async_client.get("/api/market/rates/EURUSD?timeframe=M5&count=72", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["connected"] is True
    assert payload["bars"] == []
    assert payload["error"] == "Timeout waiting for MT5 reply (RATES)"
