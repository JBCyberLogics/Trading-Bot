from __future__ import annotations

import httpx
import pytest

from app.main import create_app


async def _login(async_client, password: str) -> str:
    response = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": password},
    )
    assert response.status_code == 200
    return response.json()["token"]


@pytest.mark.asyncio
async def test_account_save_connect_disconnect_reconnect(async_client, test_env: dict):
    token = await _login(async_client, test_env["password"])
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/accounts",
        headers=headers,
        json={
            "account_name": "Primary Demo",
            "login": 123456,
            "password": "pass123",
            "server": "Broker-Demo",
            "broker_name": "Broker",
            "account_type": "Demo",
        },
    )
    assert create.status_code == 200

    accounts = await async_client.get("/api/accounts", headers=headers)
    assert accounts.status_code == 200
    items = accounts.json()["items"]
    assert len(items) == 1
    account_id = items[0]["id"]

    connect = await async_client.post("/api/connection/connect", headers=headers, json={"account_id": account_id})
    assert connect.status_code == 200
    payload = connect.json()
    assert payload["status"] == "connected"
    assert len(payload["flow_report"]) == 8

    status = await async_client.get("/api/connection/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "connected"

    disconnect = await async_client.post("/api/connection/disconnect", headers=headers, json={})
    assert disconnect.status_code == 200

    reconnect = await async_client.post("/api/connection/connect", headers=headers, json={"account_id": account_id})
    assert reconnect.status_code == 200
    assert reconnect.json()["status"] == "connected"


@pytest.mark.asyncio
async def test_account_save_allows_disconnected_mode(async_client, test_env: dict):
    token = await _login(async_client, test_env["password"])
    headers = {"Authorization": f"Bearer {token}"}

    create = await async_client.post(
        "/api/accounts",
        headers=headers,
        json={
            "account_name": "Offline Demo",
            "login": 999999,
            "password": "bad",
            "server": "Offline-Broker",
            "account_type": "Demo",
        },
    )
    # While disconnected, account save should not block on MT5 validation.
    assert create.status_code == 200


@pytest.mark.asyncio
async def test_default_account_seeded_from_env(monkeypatch: pytest.MonkeyPatch, test_env: dict):
    monkeypatch.setenv("MT5_ACCOUNT_LOGIN", "33386503")
    monkeypatch.setenv("MT5_ACCOUNT_PASSWORD", "ophJM35##")
    monkeypatch.setenv("MT5_ACCOUNT_SERVER", "FundedNext-Server3")

    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            token = await _login(async_client, test_env["password"])
            headers = {"Authorization": f"Bearer {token}"}
            accounts = await async_client.get("/api/accounts", headers=headers)

    assert accounts.status_code == 200
    items = accounts.json()["items"]
    assert any(row["login"] == 33386503 and row["server"] == "FundedNext-Server3" for row in items)
